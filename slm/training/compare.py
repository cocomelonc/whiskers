"""Paired original-base/tuned Q4 evaluation, using only owned server subprocesses."""
import argparse
import hashlib
import json
import socket
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--work", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--llama", default="/opt/slm/llama.cpp")
    ap.add_argument("--convert-python", default="/opt/slm/venv/bin/python")
    ap.add_argument("--wait-seconds", type=int, default=1800)
    ap.add_argument("--results-dir", required=True, help="Fresh directory for canonical-base comparison")
    args = ap.parse_args()
    work, llama = Path(args.work), Path(args.llama)
    deadline = time.monotonic() + args.wait_seconds
    while not (work / "GGUF.sha256").exists():
        if time.monotonic() > deadline:
            raise SystemExit("Training/export completion marker did not arrive")
        time.sleep(2)
    base_digest = json.loads(Path("slm/data/base-model.json").read_text())["weight_files"]
    for name, expected in base_digest.items():
        if hashlib.sha256((Path(args.base) / name).read_bytes()).hexdigest() != expected:
            raise SystemExit("Baseline is not the pinned original base")
    results = Path(args.results_dir)
    results.mkdir(parents=True, exist_ok=False)
    # Use the same Transformers load/save path as the merged model. In particular,
    # omit redundant tied lm_head serialization before quantizing both deployments.
    import torch
    from transformers import AutoModelForCausalLM
    normalized = results / "original-base-hf"
    base_model = AutoModelForCausalLM.from_pretrained(args.base, local_files_only=True, trust_remote_code=False,
                                                     use_safetensors=True, dtype=torch.bfloat16)
    base_model.save_pretrained(normalized, safe_serialization=True)
    del base_model
    for name in ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "added_tokens.json", "vocab.json", "merges.txt"):
        path = Path(args.base) / name
        if path.exists():
            shutil.copyfile(path, normalized / name)
    base_f16, base_q4 = results / "original-base-F16.gguf", results / "original-base-Q4_K_M.gguf"
    subprocess.run([args.convert_python, str(llama / "convert_hf_to_gguf.py"), str(normalized),
                    "--outtype", "f16", "--outfile", str(base_f16)], check=True)
    subprocess.run([str(llama / "build/bin/llama-quantize"), str(base_f16), str(base_q4), "Q4_K_M"], check=True)
    rows = []
    for alias, model, port in [("whiskers-base-0.6b", base_q4, 18083),
                               ("whiskers-ti-0.6b-v0.1", work / "whiskers-ti-0.6b-Q4_K_M.gguf", 18082)]:
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise SystemExit(f"Refusing to replace an existing server on port {port}")
        log = (results / f"{alias}-server.log").open("wb")
        server = subprocess.Popen([str(llama / "build/bin/llama-server"), "-m", str(model), "--alias", alias,
                                   "--host", "127.0.0.1", "--port", str(port), "--ctx-size", "4096",
                                   "--jinja", "--chat-template-kwargs", '{"enable_thinking":false}',
                                   "-ngl", "99", "-t", "8"], stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 120
            while True:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                        if json.loads(response.read()).get("status") == "ok":
                            break
                except (OSError, ValueError):
                    pass
                if server.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(f"Model server failed: {alias}")
                time.sleep(.5)
            for split in ("manual", "test"):
                out = results / f"eval-{alias}-{split}.json"
                subprocess.run([sys.executable, "-m", "slm.training.evaluate", "--backend", "slm", "--model", alias,
                                "--url", f"http://127.0.0.1:{port}", "--split", split, "--out", str(out)], check=True)
                report = json.loads(out.read_text())
                rows.append({k: v for k, v in report.items() if k != "results"})
        finally:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
            log.close()
    for split in ("manual", "test"):
        pair = [r for r in rows if r["split"] == split]
        if len({r["input_sha256"] for r in pair}) != 1:
            raise RuntimeError("Paired model inputs differed")
    report = {"comparison": rows, "both_models": "Q4_K_M, same prompts/schema/evidence/questions, thinking disabled",
              "limits": "GPU evaluation; not Android CPU timing or malware detection accuracy",
              "model_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in (base_q4, work / "whiskers-ti-0.6b-Q4_K_M.gguf")}}
    (results / "comparison-summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
