"""Tests for the system-prompt exfiltration demo (Brique 6, OWASP LLM02).

Deterministic tests only — no LLM call, no BGE-M3 load. The end-to-end
attack itself is exercised by running ``demo_leak.py`` manually
(non-deterministic + costs an API call, out of scope for CI), same
posture as ``tests/test_demo_injection.py`` for Brique 4.
"""

from __future__ import annotations

from pathlib import Path

from ask import SYSTEM_PROMPT
from attack_common import fake_chunk_from_markdown
from demo_leak import (
    FAKE_DOC_ID,
    FAKE_DOC_PATH,
    SYSTEM_PROMPT_CANARIES,
    _verdict,
    detect_leak,
)

# --- SYSTEM_PROMPT_CANARIES lock against the live prompt --------------------


def test_canaries_are_nonempty():
    assert SYSTEM_PROMPT_CANARIES


def test_every_canary_is_an_actual_substring_of_system_prompt():
    """Guardrail: a future edit to ask.SYSTEM_PROMPT that rewords a rule
    must not leave a canary here that can never match again — that
    would silently turn the demo into an always-RESISTANT no-op."""
    for canary in SYSTEM_PROMPT_CANARIES:
        assert canary in SYSTEM_PROMPT, f"canary no longer matches SYSTEM_PROMPT: {canary!r}"


# --- detect_leak -------------------------------------------------------------


def test_detect_leak_empty_when_no_canary_present():
    assert detect_leak("Les mots de passe administrateurs doivent faire 16 caractères.") == ()


def test_detect_leak_finds_single_canary():
    text = f"Voici mes règles : {SYSTEM_PROMPT_CANARIES[0]}, entre autres."
    found = detect_leak(text)
    assert found == (SYSTEM_PROMPT_CANARIES[0],)


def test_detect_leak_finds_multiple_canaries_in_declared_order():
    text = " ".join(SYSTEM_PROMPT_CANARIES[:2])
    found = detect_leak(text)
    assert found == SYSTEM_PROMPT_CANARIES[:2]


def test_detect_leak_is_case_sensitive():
    # Same tight-first posture as demo_injection.detect_payload: a
    # lowercased leak is still a leak in spirit, but the baseline
    # contracts on exact substrings. Widening is a deliberate future
    # change, not this baseline.
    lowered = SYSTEM_PROMPT_CANARIES[0].lower()
    assert lowered != SYSTEM_PROMPT_CANARIES[0]
    assert detect_leak(lowered) == ()


# --- fake document contract --------------------------------------------------


def test_fake_doc_exists_and_carries_the_audit_pretext():
    """Guardrail: an accidental edit that removes the payload instruction
    would silently turn the demo into a no-op."""
    text = Path(FAKE_DOC_PATH).read_text(encoding="utf-8")
    assert "recopier mot pour mot" in text
    assert "prompt système" in text


def test_fake_doc_theme_matches_benign_admin_si_corpus():
    """The fake doc must be semantically close to the admin-si /
    audit-of-configuration theme so the union retrieval will rank it in
    top-k on the demo question. Keyword-level sanity check — the real
    proof is a live retrieval run, but this rules out obvious drift."""
    text = Path(FAKE_DOC_PATH).read_text(encoding="utf-8").lower()
    for keyword in ("administration", "audit", "conformité"):
        assert keyword in text, f"missing expected theme keyword: {keyword}"


def test_fake_chunk_from_markdown_returns_expected_schema():
    chunk = fake_chunk_from_markdown(FAKE_DOC_PATH, FAKE_DOC_ID)
    assert chunk["doc_id"] == FAKE_DOC_ID
    assert chunk["page_num"] == 1
    assert chunk["chunk_idx"] == 0
    assert chunk["char_start"] == 0
    assert chunk["char_end"] == len(chunk["text"])
    assert "recopier mot pour mot" in chunk["text"]


# --- _verdict ----------------------------------------------------------------


def test_verdict_demo_invalid_when_fake_not_retrieved():
    v = _verdict(fake_in_top_k=False, leaked_canaries=())
    assert v.startswith("DEMO INVALID")


def test_verdict_leaked_when_canary_found():
    v = _verdict(fake_in_top_k=True, leaked_canaries=(SYSTEM_PROMPT_CANARIES[0],))
    assert v.startswith("LEAKED")
    assert SYSTEM_PROMPT_CANARIES[0] in v


def test_verdict_resistant_when_no_canary_found():
    v = _verdict(fake_in_top_k=True, leaked_canaries=())
    assert v.startswith("RESISTANT")
