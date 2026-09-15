"""Regression: engine/ stays domain-agnostic; app/ must not embed pack concept ids."""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
APP = ROOT / "app"

ENGINE_FORBIDDEN = (
    "sd:",
    "dsa:",
    "go:",
    "system-design",
    "ambulance",
    "hospital",
    "pantry",
    "medic",
)

# Pack concept namespaces in string literals (backends like go_exec are OK).
APP_PACK_LITERAL = re.compile(
    r"""['"](?:sd:|dsa:|go:)[^'"]*['"]"""
)


def _py_sources(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(directory.glob("*.py"))


class TestEngineContentBoundary(unittest.TestCase):
    def test_engine_has_no_pack_facts(self):
        hits: list[str] = []
        for path in _py_sources(ENGINE):
            text = path.read_text()
            for needle in ENGINE_FORBIDDEN:
                if needle in text:
                    hits.append(f"{path.relative_to(ROOT)}: contains {needle!r}")
        self.assertEqual(hits, [], "\n".join(hits))

    def test_app_has_no_pack_concept_literals(self):
        hits: list[str] = []
        for path in _py_sources(APP):
            text = path.read_text()
            for match in APP_PACK_LITERAL.finditer(text):
                hits.append(
                    f"{path.relative_to(ROOT)}: pack concept literal {match.group()}")
        self.assertEqual(hits, [], "\n".join(hits))


if __name__ == "__main__":
    unittest.main(verbosity=2)
