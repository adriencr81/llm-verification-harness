"""Bind the README/CHANGELOG numeric REQ-* claim to the registry cardinality.

Brique 6, senior-review finding: v1.0 shipped a wrong "13 REQ-* enforced"
claim in README.md (actual registry cardinality: 17). This is precisely
the *declared-vs-enforced* drift class the project exists to catch —
letting the count drift silently makes the frozen-registry claim itself
declared-not-enforced. This test refuses to let the surface number and
the registry disagree without a human touching both.

Falsifiable: pass iff every ``N `REQ-*``` claim in README.md and
CHANGELOG.md matches ``len(re.findall(r'^### `REQ-...``, ...))`` on
docs/REQUIREMENTS.md.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

REGISTRY_HEADING_RE = re.compile(r"^### `(REQ-[A-Z]+-\d+)`", re.MULTILINE)

# Anything of the form "N `REQ-*`" in prose. Kept narrow: matches only a
# numeric claim about REQ-* headings, not incidental numbers.
CLAIM_RE = re.compile(r"(\d+)\s+`REQ-\*`")


def _registry_cardinality() -> int:
    text = (REPO_ROOT / "docs" / "REQUIREMENTS.md").read_text(encoding="utf-8")
    return len(REGISTRY_HEADING_RE.findall(text))


def _extract_claims(path: Path) -> list[tuple[int, int]]:
    """Return list of ``(line_number, claimed_count)`` for prose claims."""
    text = path.read_text(encoding="utf-8")
    out: list[tuple[int, int]] = []
    for m in CLAIM_RE.finditer(text):
        line_no = text.count("\n", 0, m.start()) + 1
        out.append((line_no, int(m.group(1))))
    return out


def test_registry_cardinality_is_at_least_the_b6_baseline():
    """Sentinel: v1.0 ships 17 REQ-*. A later brique may add but never
    remove without a corresponding registry bump — this test would fail
    on a silent shrink."""
    assert _registry_cardinality() >= 17


def test_readme_numeric_claim_matches_registry():
    claims = _extract_claims(REPO_ROOT / "README.md")
    assert claims, "no 'N `REQ-*`' claim found in README.md — expected at least one"
    cardinality = _registry_cardinality()
    for line, count in claims:
        assert count == cardinality, (
            f"README.md:{line} claims {count} `REQ-*`, "
            f"registry has {cardinality} (in docs/REQUIREMENTS.md). "
            f"Update one or the other — the frozen-registry story is only "
            f"credible if the surface number matches the source."
        )


def test_changelog_numeric_claim_matches_registry():
    claims = _extract_claims(REPO_ROOT / "CHANGELOG.md")
    if not claims:
        # CHANGELOG.md may legitimately not carry the aggregate claim in
        # every future entry — this test is about consistency when the
        # claim IS present, not about mandating it in every entry.
        return
    cardinality = _registry_cardinality()
    for line, count in claims:
        assert count == cardinality, (
            f"CHANGELOG.md:{line} claims {count} `REQ-*`, "
            f"registry has {cardinality}."
        )
