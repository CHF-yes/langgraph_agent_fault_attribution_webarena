#!/usr/bin/env python3
"""Write experiments/HAR_MANIFEST.tsv: path, size and SHA-256 of every HAR.

The captures themselves are deliberately not committed.  The full set is
6.74 GiB across 896 files and this repository has no git-lfs configured, so
committing them would push the repository past GitHub's size guidance and make
a later history rewrite necessary to undo.  The manifest keeps the inventory and
integrity check reproducible without the bulk:

    python3 scripts/har_manifest.py            # rewrite the manifest
    sha256sum -c <(awk -F'\t' '!/^#/{print $3"  "$1}' experiments/HAR_MANIFEST.tsv)

Use ``--require-all`` in CI-like checks to fail when a HAR listed in the
manifest has gone missing.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "experiments/HAR_MANIFEST.tsv"


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--require-all", action="store_true",
                    help="also verify that every previously listed HAR still exists")
    args = ap.parse_args()
    target = Path(args.manifest)

    previous: dict[str, str] = {}
    if args.require_all and target.exists():
        for line in target.read_text().splitlines():
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                previous[parts[0]] = parts[2]

    files = sorted((REPO / "experiments").rglob("*.har"))
    lines = [
        "# HAR inventory.  The captures themselves are not committed: the full set is",
        "# about 6.7 GiB and the repository has no git-lfs.  Regenerate this file with",
        "# scripts/har_manifest.py.",
        f"# generated {datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}",
        f"# files {len(files)}",
        "# path\tsize_bytes\tsha256",
    ]
    total = 0
    seen: set[str] = set()
    for path in files:
        rel = str(path.relative_to(REPO))
        size = path.stat().st_size
        lines.append(f"{rel}\t{size}\t{sha256(path)}")
        total += size
        seen.add(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n")
    print(f"wrote {target.relative_to(REPO)}: {len(files)} HARs, {total / 1024**3:.2f} GiB")

    if args.require_all:
        missing = sorted(set(previous) - seen)
        if missing:
            print(f"MISSING {len(missing)} HAR(s) listed in the previous manifest:")
            for rel in missing[:20]:
                print("  ", rel)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
