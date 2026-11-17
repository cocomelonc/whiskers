#!/usr/bin/env bash
# Run from the Whiskers checkout; each run uses a fresh output directory.
set -euo pipefail
if [ "$#" -ne 2 ]; then
  printf 'Usage: bash slm/training/run_l20.sh BASE_MODEL FRESH_WORK_DIRECTORY\n' >&2
  exit 2
fi
whiskers_repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
whiskers_base="$1"
whiskers_work="$2"
whiskers_train_python="${WHISKERS_TRAIN_PYTHON:-/opt/train/venv/bin/python}"
whiskers_convert_python="${WHISKERS_CONVERT_PYTHON:-/opt/slm/venv/bin/python}"
whiskers_llama="${WHISKERS_LLAMA_CPP:-/opt/slm/llama.cpp}"
if [ -e "$whiskers_work" ]; then
  printf 'Work directory already exists; choose a fresh directory.\n' >&2
  exit 2
fi
if [ "$(git -C "$whiskers_llama" rev-parse HEAD)" != 6f1e6543704bc5b080c7a98e7b6f707b31f6ed51 ]; then
  printf 'llama.cpp revision differs from the recorded converter/runtime pin.\n' >&2
  exit 2
fi
mkdir -p "$whiskers_work/data"
for whiskers_split in train validation test; do
  gzip -dc "$whiskers_repo/slm/data/$whiskers_split.jsonl.gz" > "$whiskers_work/data/$whiskers_split.jsonl"
done
cp "$whiskers_repo/slm/data/manifest.json" "$whiskers_work/dataset-manifest.json"
cp "$whiskers_repo/slm/data/base-model.json" "$whiskers_work/expected-base.json"
"$whiskers_train_python" -m pip freeze > "$whiskers_work/training-environment.txt"
nvidia-smi --query-gpu=name,memory.total,memory.free,driver_version --format=csv > "$whiskers_work/gpu-before.csv"
"$whiskers_train_python" - "$whiskers_work" "$whiskers_base" "$whiskers_repo" <<'PY'
import hashlib,json,sys
from pathlib import Path
work,base,repo=map(Path,sys.argv[1:])
manifest=json.loads((work/'dataset-manifest.json').read_text())
if hashlib.sha256((repo/'slm/protocol.py').read_bytes()).hexdigest() != manifest['protocol_sha256']:
    raise SystemExit('Protocol changed since dataset preparation')
for split in ('train','validation','test'):
    actual=hashlib.sha256((work/f'data/{split}.jsonl').read_bytes()).hexdigest()
    if actual != manifest['sha256'][split]:raise SystemExit('Dataset hash mismatch: '+split)
files={str(p.relative_to(base)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(base.glob('*')) if p.is_file()}
expected=json.loads((work/'expected-base.json').read_text())
for name,digest in expected['weight_files'].items():
    if not digest or files.get(name) != digest:raise SystemExit('Not the pinned original Qwen weights: '+name)
(work/'base-files-sha256.json').write_text(json.dumps(files,indent=2)+'\n')
(work/'source-files-sha256.json').write_text(json.dumps({str(p.relative_to(repo)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((repo/'slm').rglob('*.py')) if '__pycache__' not in p.parts},indent=2)+'\n')
PY
"$whiskers_train_python" "$whiskers_repo/slm/training/train.py" --base "$whiskers_base" --work "$whiskers_work" --epochs 3 --ctx 2048 2>&1 | tee "$whiskers_work/train.log"
# Preserve the base tokenizer serialization for the pinned GGUF converter.
for whiskers_file in tokenizer.json tokenizer_config.json special_tokens_map.json added_tokens.json vocab.json merges.txt; do
  if [ -f "$whiskers_base/$whiskers_file" ]; then
    cp "$whiskers_base/$whiskers_file" "$whiskers_work/merged/$whiskers_file"
  fi
done
"$whiskers_convert_python" "$whiskers_llama/convert_hf_to_gguf.py" "$whiskers_work/merged" --outfile "$whiskers_work/whiskers-ti-0.6b-F16.gguf" --outtype f16
"$whiskers_llama/build/bin/llama-quantize" "$whiskers_work/whiskers-ti-0.6b-F16.gguf" "$whiskers_work/whiskers-ti-0.6b-Q4_K_M.gguf" Q4_K_M
sha256sum "$whiskers_work/whiskers-ti-0.6b-Q4_K_M.gguf" > "$whiskers_work/GGUF.sha256"
printf 'Whiskers training/export completed. Evaluate the GGUF before presenting neural results.\n'
