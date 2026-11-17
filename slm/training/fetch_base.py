"""Fetch the pinned original base through the shared HF cache, never a TI adapter."""
import argparse
import json
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache-dir", default=None)
    args = ap.parse_args()
    from huggingface_hub import snapshot_download
    manifest = json.loads(Path(__file__).parents[1].joinpath("data/base-model.json").read_text())
    path = snapshot_download(repo_id=manifest["id"], revision=manifest["revision"], cache_dir=args.cache_dir,
                             allow_patterns=["*.json", "*.safetensors", "*.txt", "*.jinja", "LICENSE", "README.md"])
    print(path)


if __name__ == "__main__":
    main()
