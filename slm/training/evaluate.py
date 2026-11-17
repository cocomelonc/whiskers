"""Compare lexical/base/tuned selectors on identical frozen evidence and questions."""
import argparse
import gzip
import json
import os
import re
import statistics
import time
from collections import Counter
from pathlib import Path

from slm.client import WhiskersSLM
from slm.protocol import canonical, family_evidence, fingerprint, render_selection
from slm.training.prepare import BEHAVIOR_QUESTIONS


def lexical(question, evidence):
    """A deliberately simple non-neural reference, not a fallback for a missing model."""
    q = question.lower()
    keys = {e["id"] for e in evidence}
    ids = []
    if re.search(r"current|right now|live botnet|uploaded|laptop|ignore|every variant|compromised|i found|today", q):
        pass
    elif re.search(r"brief|summarize|record say this malware", q):
        ids = [e["id"] for e in evidence if e["id"].startswith("description:")][:2]
    elif re.search(r"alias|alternative names|other names|other labels|alternate family name", q):
        ids = ["aliases"] if "aliases" in keys else []
    elif re.search(r"actor|association|attribute|operator|group labels", q):
        ids = ["attribution"] if "attribution" in keys else []
    elif re.search(r"updat", q):
        ids = ["record_updated"] if "record_updated" in keys else []
    elif re.search(r"canonical|which family record|record identifier", q):
        ids = ["identity"] if "identity" in keys else []
    elif re.search(r"who wrote", q):
        ids = ["rule_author"] if "rule_author" in keys else []
    elif re.search(r"rule states", q):
        ids = [k for k in ("rule_name", "rule_description", "rule_condition", "rule_limits") if k in keys]
    else:
        # Generic token overlap, with no per-benchmark-answer lookup.
        words = set(re.findall(r"[a-z]{4,}", q)) - {"what", "which", "this", "that", "does", "from", "record", "description", "mentioned"}
        scores = [(len(words & set(re.findall(r"[a-z]{4,}", e["text"].lower()))), e["id"])
                  for e in evidence if e["id"].startswith("description:")]
        if scores and max(scores)[0] > 0:
            ids = [max(scores)[1]]
    return {"evidence_ids": ids, "abstain": not bool(ids)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--split", choices=["validation", "test", "demo", "manual"], default="manual")
    ap.add_argument("--backend", choices=["lexical", "slm"], default="slm")
    ap.add_argument("--model", default="whiskers-ti-0.6b-v0.1")
    ap.add_argument("--url", default="http://127.0.0.1:8082")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if Path(args.out).exists():
        raise SystemExit("Choose a new output path; evaluation evidence is not overwritten")
    if args.split == "manual":
        families = json.loads(Path("slm/demo/families.json").read_text())
        cases = json.loads(Path("slm/training/manual_cases.json").read_text())
        rows = [{"id": fingerprint(case), "task": "manual", "question": case["question"],
                 "evidence": family_evidence(case["family"], families[case["family"]]),
                 "target": {"evidence_ids": case["ids"], "abstain": not bool(case["ids"])}}
                for case in cases]
    else:
        rows = [json.loads(line) for line in gzip.decompress(Path(f"slm/data/{args.split}.jsonl.gz").read_bytes()).splitlines()]
    if args.limit:
        rows = rows[:args.limit]
    os.environ.update(WHISKERS_SLM_ENABLED="1", WHISKERS_SLM_URL=args.url, WHISKERS_SLM_MODEL=args.model)
    client = WhiskersSLM()
    if args.backend == "slm" and not client.status()["ready"]:
        raise SystemExit("Requested model alias is not loaded; no neural evaluation was run")
    results = []
    for row in rows:
        start = time.monotonic()
        valid, selection, error = True, None, None
        try:
            if args.backend == "lexical":
                selection = lexical(row["question"], row["evidence"])
                render_selection(canonical(selection), row["evidence"])
            else:
                selection = client.answer(row["question"], row["evidence"])["selection"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            valid, error = False, str(exc)
        gold = row["target"]
        pred_ids = set(selection["evidence_ids"]) if selection else set()
        gold_ids = set(gold["evidence_ids"])
        overlap = len(pred_ids & gold_ids)
        f1 = 2 * overlap / (len(pred_ids) + len(gold_ids)) if pred_ids or gold_ids else float(valid and selection["abstain"])
        results.append({"id": row["id"], "task": row["task"], "question": row["question"],
                        "target": gold, "selection": selection, "valid": valid, "error": error,
                        "exact": valid and selection == gold, "evidence_f1": f1,
                        "seconds": time.monotonic() - start})
        if len(results) % 25 == 0 or len(results) == len(rows):
            print(f'{len(results)}/{len(rows)} exact={sum(r["exact"] for r in results)}', flush=True)
    unknown = [r for r in results if r["target"]["abstain"]]
    known = [r for r in results if not r["target"]["abstain"]]
    latency = sorted(r["seconds"] for r in results)
    report = {"backend": args.backend, "model": args.model if args.backend == "slm" else "lexical-v1",
              "split": args.split, "n": len(results), "exact": sum(r["exact"] for r in results),
              "valid": sum(r["valid"] for r in results),
              "mean_evidence_f1": statistics.mean(r["evidence_f1"] for r in results),
              "unknown_exact": sum(r["exact"] for r in unknown), "unknown_n": len(unknown),
              "known_exact": sum(r["exact"] for r in known), "known_n": len(known),
              "latency_p50_s": statistics.median(latency), "latency_p95_s": latency[min(len(latency)-1, int(.95*len(latency)))],
              "input_sha256": fingerprint(rows), "results": results,
              "limits": "Small source-selection benchmark; not malware recall, false-positive rate, or a prompt-injection guarantee."}
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(canonical({k: v for k, v in report.items() if k != "results"}))


if __name__ == "__main__":
    main()
