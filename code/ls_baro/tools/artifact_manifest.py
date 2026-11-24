"""Write or verify SHA-256 entries for the distributed artifact bytes."""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
from pathlib import Path
import subprocess
import json

REPOSITORY = Path(__file__).resolve().parents[3]
MANIFEST = "MANIFEST_SHA256.csv"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify(root, content, read):
    errors = []
    for row in csv.DictReader(io.StringIO(content.decode("utf-8-sig"))):
        relative = Path(row["path"])
        target = (root / relative).resolve()
        if relative.is_absolute() or not target.is_relative_to(root):
            raise ValueError(f"Manifest path escapes artifact root: {relative}")
        try:
            data = read(row["path"])
        except (FileNotFoundError, subprocess.CalledProcessError):
            errors.append(f"Missing file: {row['path']}"); continue
        if len(data) != int(row["bytes"]) or digest(data).lower() != row["sha256"].lower():
            errors.append(f"Size/hash mismatch: {row['path']}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPOSITORY)
    parser.add_argument("--write", action="store_true", help="Regenerate from tracked working-tree files after staging new files")
    parser.add_argument("--revision", help="Verify Git blob bytes at this revision instead of the working tree")
    args = parser.parse_args()
    root = args.root.resolve()
    def git(*options):
        return subprocess.check_output(["git", *options], cwd=root)
    if args.write:
        if args.revision:
            parser.error("--write and --revision cannot be combined")
        evidence = []
        for path in sorted((root/"results/core_evidence").rglob("*")):
            if path.is_file():
                data = path.read_bytes()
                evidence.append(dict(snapshot=path.relative_to(root).as_posix(), bytes=len(data), sha256=digest(data)))
        with (root/"provenance/evidence_index.csv").open("w",encoding="utf-8",newline="") as stream:
            writer=csv.DictWriter(stream,fieldnames=["snapshot","bytes","sha256"],lineterminator="\n")
            writer.writeheader(); writer.writerows(evidence)
        (root/"provenance/figure_table_sources.json").write_text(
            json.dumps({r["snapshot"]:r["sha256"] for r in evidence},indent=2)+"\n",encoding="utf-8",newline="\n")
        names = sorted(p.decode() for p in git("ls-files", "-z").split(b"\0") if p and p.decode() != MANIFEST)
        rows = []
        for name in names:
            data = (root/name).read_bytes()
            if b"\r\n" in data:
                raise ValueError(f"Expected LF distribution bytes: {name}")
            rows.append(dict(path=Path(name).as_posix(), bytes=len(data), sha256=digest(data)))
        with (root/MANIFEST).open("w",encoding="utf-8",newline="") as stream:
            writer=csv.DictWriter(stream,fieldnames=["path","bytes","sha256"],lineterminator="\n")
            writer.writeheader(); writer.writerows(rows)
        print(f"Wrote {len(rows)} entries (manifest excludes itself).")
    read = (lambda name: git("show", args.revision+":"+name)) if args.revision else (lambda name: (root/name).read_bytes())
    errors = verify(root, read(MANIFEST), read)
    if errors:
        raise SystemExit("\n".join(errors))
    print("All manifest sizes and SHA-256 hashes match.")


if __name__ == "__main__":
    main()
