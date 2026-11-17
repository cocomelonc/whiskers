"""Acquire a NEW public snapshot without credentials or overwriting frozen sources."""
import argparse
import hashlib
import io
import json
import tarfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RULE_COMMIT = "eddaf26a30c6daff1b2350f52d0b5c11767f7552"


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "whiskers-mini-ti-research/0.1"})
    with urllib.request.urlopen(request, timeout=180) as response:
        content = response.read(50 * 1024 * 1024 + 1)
    if len(content) > 50 * 1024 * 1024:
        raise ValueError("Source exceeds the bounded download size")
    return content


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="Fresh raw snapshot directory")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    urls = {
        "malpedia-families.json": "https://malpedia.caad.fkie.fraunhofer.de/api/get/families",
        "malpedia-version.json": "https://malpedia.caad.fkie.fraunhofer.de/api/get/version",
        "rules.tar.gz": f"https://github.com/malpedia/signator-rules/archive/{RULE_COMMIT}.tar.gz",
    }
    manifest = {"retrieved_utc": datetime.now(timezone.utc).isoformat(), "anonymous": True,
                "rules_commit": RULE_COMMIT, "files": {}, "snapshot_and_version_are_atomic": False}
    for name, url in urls.items():
        content = fetch(url)
        if name.endswith(".json"):
            json.loads(content)  # Do not accept a partial response or HTML error as data.
        (out / name).write_bytes(content)
        manifest["files"][name] = {"url": url, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
    (out / "rules").mkdir()
    # Read only regular rule members. No tar paths or links are extracted onto disk.
    with tarfile.open(fileobj=io.BytesIO((out / "rules.tar.gz").read_bytes()), mode="r:gz") as archive:
        for member in archive.getmembers():
            path = Path(member.name)
            if member.isfile() and path.suffix == ".yar" and path.parent.name == "rules":
                if member.size > 1024 * 1024:
                    raise ValueError("Oversized rule")
                (out / "rules" / path.name).write_bytes(archive.extractfile(member).read())
    (out / "acquisition.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
