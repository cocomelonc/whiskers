"""Seed a new, separate SQLite database. Never overwrite the live corpus."""
import argparse
import json
import os
from contextlib import closing
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="slm/demo/whiskers-demo.db")
    args = ap.parse_args()
    path = Path(args.out)
    if path.exists():
        raise SystemExit("Refusing to overwrite an existing database; choose a new --out path")
    path.parent.mkdir(parents=True, exist_ok=True)
    os.environ["WHISKERS_DB"] = str(path)
    os.environ["WHISKERS_OFFLINE"] = "1"
    import main as app
    app.init_db()
    fixture = json.loads(Path(__file__).with_name("families.json").read_text())
    with closing(app._db()) as c, c:
        for name, meta in fixture.items():
            c.execute("INSERT INTO families VALUES(?,?,?,?)", (name, meta.get("common_name", name), meta.get("updated", ""), json.dumps(meta)))
            c.execute("INSERT INTO search(name,common_name,aliases,description,attribution,refs) VALUES(?,?,?,?,?,?)",
                      (name, meta.get("common_name", name), " ".join(meta.get("alt_names") or []),
                       meta.get("description", ""), " ".join(meta.get("attribution") or []),
                       " ".join(meta.get("urls") or [])))
    print(f"Seeded {len(fixture)} public-source families in {path}")


if __name__ == "__main__":
    main()
