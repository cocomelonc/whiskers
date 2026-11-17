"""Prepare independent Whiskers Android assets from frozen source data and a verified run."""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from slm.protocol import SYSTEM, canonical, family_evidence, fingerprint


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", type=Path)
    ap.add_argument("--training-summary", type=Path)
    args = ap.parse_args()
    assets = ROOT / "android/app/src/main/assets"
    families = json.loads((ROOT / "slm/demo/families.json").read_text())
    cards = []
    for name, meta in families.items():
        evidence = family_evidence(name, meta)
        cards.append({"id": name, "name": meta.get("common_name") or name, "updated": meta.get("updated", ""),
                      "source": "https://malpedia.caad.fkie.fraunhofer.de/details/" + name,
                      "evidence": evidence, "canonical_evidence": canonical(evidence), "evidence_sha256": fingerprint(evidence),
                      "rules": meta.get("yara", [])})
    (assets / "kit.json").write_text(json.dumps(cards, ensure_ascii=False, indent=2) + "\n")
    (assets / "system.txt").write_text(SYSTEM)
    manifest = json.loads((ROOT / "slm/data/manifest.json").read_text())
    (assets / "dataset-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    test = ROOT / "android/app/src/test/resources"
    test.mkdir(parents=True, exist_ok=True)
    golden = []
    for card in cards:
        question = "Which actors are listed in this record?"
        golden.append({"card": card, "question": question, "payload": canonical({"evidence": card["evidence"], "question": question})})
    (test / "protocol-golden.json").write_text(json.dumps(golden, ensure_ascii=False, indent=2) + "\n")
    if args.model:
        if not args.training_summary:
            raise SystemExit("A Whiskers training summary is required for bundling model weights")
        summary = json.loads(args.training_summary.read_text())
        if summary.get("project") != "whiskers-mini-ti" or summary["data_sha256"]["train"] != manifest["sha256"]["train"]:
            raise SystemExit("Model run is not the independent Whiskers dataset")
        with args.model.open("rb") as source:
            if source.read(4) != b"GGUF":
                raise SystemExit("Model is not GGUF")
        digest = hashlib.sha256(args.model.read_bytes()).hexdigest()
        info = {"id": "whiskers-ti-0.6b-v0.1", "sha256": digest, "bytes": args.model.stat().st_size,
                "dataset_sha256": manifest["sha256"]["train"], "base": "Qwen/Qwen3-0.6B", "independent_whiskers_run": True}
        (assets / "model-info.json").write_text(json.dumps(info, indent=2) + "\n")
        shutil.copyfile(args.model, assets / "whiskers-ti-0.6b-Q4_K_M.gguf")
        print(json.dumps(info))
    else:
        print("Prepared source assets only; no model identity or weights substituted")


if __name__ == "__main__":
    main()
