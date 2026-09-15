"""Where packs and verify trees live.

Engine never names a domain. Data is discovered, not imported:

1. TUTOR_PACKS / TUTOR_VERIFY if set
2. sibling ../tutor-content/{packs,verify} (private data repo)
3. ./content/{packs,verify} if present (local checkout overlay)
4. tests/seed/{packs,verify} so a public clone still runs the engine
"""
from __future__ import annotations

import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _existing_dir(path: pathlib.Path) -> pathlib.Path | None:
    if path.is_dir() and any(path.iterdir()):
        return path
    return None


def packs_dir() -> pathlib.Path:
    env = os.environ.get("TUTOR_PACKS")
    if env:
        return pathlib.Path(env)
    for candidate in (
        ROOT.parent / "tutor-content" / "packs",
        ROOT / "content" / "packs",
        ROOT / "tests" / "seed" / "packs",
    ):
        found = _existing_dir(candidate)
        if found is not None:
            return found
    return ROOT / "tests" / "seed" / "packs"


def verify_dir() -> pathlib.Path:
    env = os.environ.get("TUTOR_VERIFY")
    if env:
        return pathlib.Path(env)
    for candidate in (
        ROOT.parent / "tutor-content" / "verify",
        ROOT / "content" / "verify",
        ROOT / "tests" / "seed" / "verify",
    ):
        found = _existing_dir(candidate)
        if found is not None:
            return found
    return ROOT / "tests" / "seed" / "verify"


def has_authored_packs() -> bool:
    """True when a packs tree has more than one namespaced prefix."""
    root = packs_dir()
    if not root.is_dir():
        return False
    prefixes = set()
    for pack in root.iterdir():
        concepts = pack / "concepts.json"
        if not concepts.is_file():
            continue
        try:
            import json
            data = json.loads(concepts.read_text())
        except (OSError, ValueError):
            continue
        for cid in data:
            if ":" in cid:
                prefixes.add(cid.split(":", 1)[0])
    return len(prefixes) >= 2
