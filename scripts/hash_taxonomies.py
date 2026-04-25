#!/usr/bin/env python3
"""Recompute the agentic taxonomy composite hash for taxonomies.lock.json.

Algorithm (matches the seller-side implementation so cross-repo drift
detection works):

    1. Walk every regular file under
       data/taxonomies/agentic-audiences-draft-2026-01/spec/.
    2. For each file, compute its sha256.
    3. Build a manifest line per file:  "<relpath>\t<sha256>\n"
       where <relpath> is relative to
       data/taxonomies/agentic-audiences-draft-2026-01/.
    4. Sort the manifest lines lexicographically by <relpath>.
    5. Concatenate them and take sha256 of the result. That is the
       composite "agentic.sha256" recorded in taxonomies.lock.json.

The per-file map is also emitted under "agentic.files" so a refresher
can see exactly which file changed when the composite changes.

Usage (from the repo root):

    python3 scripts/hash_taxonomies.py            # print JSON to stdout
    python3 scripts/hash_taxonomies.py --check    # diff against lock file (exit 1 on mismatch)
    python3 scripts/hash_taxonomies.py --write    # update taxonomies.lock.json in place
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TAXONOMIES_DIR = REPO_ROOT / "data" / "taxonomies"
AGENTIC_DIR_NAME = "agentic-audiences-draft-2026-01"
AGENTIC_BASE = TAXONOMIES_DIR / AGENTIC_DIR_NAME
SPEC_ROOT = AGENTIC_BASE / "spec"
LOCK_PATH = TAXONOMIES_DIR / "taxonomies.lock.json"

SHA256_METHOD = "sha256(sorted lines of '<relpath>\\t<sha256>\\n')"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_agentic_hashes() -> dict:
    """Return {'sha256': <composite>, 'files': {<relpath>: <sha256>, ...}}.

    Per-file paths are relative to AGENTIC_BASE (so they include the
    leading 'spec/'), matching the seller convention.
    """
    if not SPEC_ROOT.is_dir():
        raise SystemExit(f"spec directory missing: {SPEC_ROOT}")

    per_file: list[tuple[str, str]] = []
    for root, dirs, fnames in os.walk(SPEC_ROOT):
        dirs.sort()
        for fname in sorted(fnames):
            full = Path(root) / fname
            rel = full.relative_to(AGENTIC_BASE).as_posix()
            per_file.append((rel, _sha256_file(full)))

    per_file.sort(key=lambda x: x[0])
    manifest = "".join(f"{rel}\t{h}\n" for rel, h in per_file)
    composite = hashlib.sha256(manifest.encode("utf-8")).hexdigest()

    return {
        "sha256": composite,
        "sha256_method": SHA256_METHOD,
        "files": dict(per_file),
    }


def _load_lock() -> dict:
    with LOCK_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_lock(data: dict) -> None:
    text = json.dumps(data, indent=2) + "\n"
    LOCK_PATH.write_text(text, encoding="utf-8")


def _merge_into_lock(lock: dict, computed: dict) -> dict:
    agentic = dict(lock.get("agentic", {}))
    agentic["sha256"] = computed["sha256"]
    agentic["sha256_method"] = computed["sha256_method"]
    agentic["files"] = computed["files"]
    lock["agentic"] = agentic
    return lock


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare computed hashes to taxonomies.lock.json and exit 1 on mismatch.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Update taxonomies.lock.json in place with the computed hashes.",
    )
    args = parser.parse_args()

    computed = compute_agentic_hashes()

    if args.check:
        lock = _load_lock()
        agentic = lock.get("agentic", {})
        ok = (
            agentic.get("sha256") == computed["sha256"]
            and agentic.get("files") == computed["files"]
        )
        if not ok:
            print("MISMATCH between computed agentic hashes and taxonomies.lock.json", file=sys.stderr)
            print(f"  computed sha256: {computed['sha256']}", file=sys.stderr)
            print(f"  lock     sha256: {agentic.get('sha256')}", file=sys.stderr)
            return 1
        print("agentic hashes match taxonomies.lock.json")
        return 0

    if args.write:
        lock = _load_lock()
        _merge_into_lock(lock, computed)
        _write_lock(lock)
        print(f"updated {LOCK_PATH} with composite {computed['sha256']}")
        return 0

    print(json.dumps(computed, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
