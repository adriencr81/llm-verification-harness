"""Tests for download_corpus.py — validates the corpus integrity contract.

The critical test is ``test_single_byte_alteration_raises_integrity_error``:
if a single byte of a PDF can change without the script noticing, the IVVQ
premise of the project collapses. This test is the falsifiability check the
VCD will cite as evidence that the contract is enforced, not merely declared.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import requests
import yaml

import download_corpus


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nfake content for integrity tests\n")
    return pdf_path


@pytest.fixture
def sample_manifest_entry(sample_pdf: Path) -> dict:
    return {
        "doc_id": "sample",
        "filename": sample_pdf.name,
        "title": "Test document",
        "download_url": None,
        "landing_page": "https://example.invalid/sample",
        "signed_url": False,
        "sha256": hashlib.sha256(sample_pdf.read_bytes()).hexdigest(),
        "downloaded_at": "2026-07-01",
    }


def test_conformant_file_passes(sample_pdf: Path, sample_manifest_entry: dict) -> None:
    download_corpus.verify_document(sample_manifest_entry, sample_pdf.parent)


def test_single_byte_alteration_raises_integrity_error(
    sample_pdf: Path, sample_manifest_entry: dict
) -> None:
    # VCD-cite: falsifiabilité du contrat corpus (Brique 7, §corpus, REQ-CORPUS-01).
    content = bytearray(sample_pdf.read_bytes())
    content[0] ^= 0xFF
    sample_pdf.write_bytes(bytes(content))

    with pytest.raises(download_corpus.CorpusIntegrityError) as excinfo:
        download_corpus.verify_document(sample_manifest_entry, sample_pdf.parent)
    message = str(excinfo.value)
    assert "SHA256 mismatch" in message
    assert sample_manifest_entry["doc_id"] in message
    assert sample_manifest_entry["sha256"] in message


def test_integrity_error_is_a_corpus_error(
    sample_pdf: Path, sample_manifest_entry: dict
) -> None:
    """Brique 7 VCD consumer relies on the CorpusError umbrella."""
    content = bytearray(sample_pdf.read_bytes())
    content[0] ^= 0xFF
    sample_pdf.write_bytes(bytes(content))

    with pytest.raises(download_corpus.CorpusError):
        download_corpus.verify_document(sample_manifest_entry, sample_pdf.parent)


def test_missing_file_without_url_raises_missing_source_error(
    sample_pdf: Path, sample_manifest_entry: dict
) -> None:
    sample_pdf.unlink()

    with pytest.raises(download_corpus.MissingSourceError) as excinfo:
        download_corpus.verify_document(sample_manifest_entry, sample_pdf.parent)
    assert sample_manifest_entry["landing_page"] in str(excinfo.value)


def test_verify_all_returns_ok_report_on_conformant_corpus(
    sample_pdf: Path, sample_manifest_entry: dict
) -> None:
    report = download_corpus.verify_all([sample_manifest_entry], sample_pdf.parent)
    assert report.ok
    assert report.integrity_errors == []
    assert report.missing_errors == []


def test_verify_all_accumulates_errors_across_documents(tmp_path: Path) -> None:
    good_pdf = tmp_path / "good.pdf"
    good_pdf.write_bytes(b"good content")
    bad_pdf = tmp_path / "bad.pdf"
    bad_pdf.write_bytes(b"actual content differs from declared hash")

    documents = [
        {
            "doc_id": "good",
            "filename": "good.pdf",
            "sha256": hashlib.sha256(good_pdf.read_bytes()).hexdigest(),
        },
        {
            "doc_id": "bad",
            "filename": "bad.pdf",
            "sha256": "0" * 64,
        },
        {
            "doc_id": "gone",
            "filename": "gone.pdf",
            "sha256": "0" * 64,
            "download_url": None,
            "landing_page": "https://example.invalid/gone",
        },
    ]

    report = download_corpus.verify_all(documents, tmp_path)

    assert not report.ok
    assert len(report.integrity_errors) == 1
    assert "bad" in report.integrity_errors[0]
    assert len(report.missing_errors) == 1
    assert "gone" in report.missing_errors[0]


def test_fetch_writes_expected_payload_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"%PDF-1.4 fake payload for fetch test"
    destination = tmp_path / "out.pdf"

    class _MockResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int):
            yield payload

    def fake_get(url, **kwargs):
        return _MockResponse()

    monkeypatch.setattr(download_corpus.requests, "get", fake_get)
    download_corpus.fetch("https://example.invalid/x.pdf", destination)

    assert destination.read_bytes() == payload
    assert not destination.with_name(destination.name + ".tmp").exists()


def test_fetch_failure_leaves_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "out.pdf"

    def fake_get(url, **kwargs):
        raise requests.ConnectionError("simulated network failure")

    monkeypatch.setattr(download_corpus.requests, "get", fake_get)
    with pytest.raises(requests.ConnectionError):
        download_corpus.fetch("https://example.invalid/x.pdf", destination)

    assert not destination.exists()
    assert not destination.with_name(destination.name + ".tmp").exists()


def test_declared_bytes_mismatch_raises_size_error(
    sample_pdf: Path, sample_manifest_entry: dict
) -> None:
    """REQ-CORPUS-03 : le champ ``bytes`` est un contrat, pas une annotation.

    Un fichier de bonne longueur peut encore avoir un SHA256 correct — mais
    un fichier de mauvaise longueur ne peut PAS avoir le hash déclaré. On
    déclenche donc l'erreur au check taille (cheap) avant d'ouvrir un
    hachage complet (coûteux).
    """
    sample_manifest_entry["bytes"] = sample_pdf.stat().st_size + 1

    with pytest.raises(download_corpus.CorpusSizeError) as excinfo:
        download_corpus.verify_document(sample_manifest_entry, sample_pdf.parent)
    message = str(excinfo.value)
    assert "size mismatch" in message.lower()
    assert sample_manifest_entry["doc_id"] in message


def test_size_error_is_a_corpus_error(
    sample_pdf: Path, sample_manifest_entry: dict
) -> None:
    """Brique 7 VCD consumer relies on the CorpusError umbrella."""
    sample_manifest_entry["bytes"] = 0

    with pytest.raises(download_corpus.CorpusError):
        download_corpus.verify_document(sample_manifest_entry, sample_pdf.parent)


def test_bytes_absent_from_manifest_skips_size_check(
    sample_pdf: Path, sample_manifest_entry: dict
) -> None:
    """Le check taille est opt-in : un doc sans ``bytes`` passe sans lever.

    Garantit qu'un enrich_manifest.py partiel (ex : le doc vient d'être
    ajouté et pas encore enrichi) ne casse pas la vérification amont.
    """
    assert "bytes" not in sample_manifest_entry
    download_corpus.verify_document(sample_manifest_entry, sample_pdf.parent)


def test_verify_all_accumulates_size_errors(tmp_path: Path) -> None:
    """Un size error alimente ``report.size_errors`` (pas integrity_errors)."""
    good_pdf = tmp_path / "good.pdf"
    good_pdf.write_bytes(b"payload")
    wrong_size_pdf = tmp_path / "wrong.pdf"
    wrong_size_pdf.write_bytes(b"payload")

    documents = [
        {
            "doc_id": "good",
            "filename": "good.pdf",
            "sha256": hashlib.sha256(good_pdf.read_bytes()).hexdigest(),
            "bytes": good_pdf.stat().st_size,
        },
        {
            "doc_id": "wrong-size",
            "filename": "wrong.pdf",
            "sha256": hashlib.sha256(wrong_size_pdf.read_bytes()).hexdigest(),
            "bytes": wrong_size_pdf.stat().st_size + 42,
        },
    ]

    report = download_corpus.verify_all(documents, tmp_path)

    assert not report.ok
    assert report.integrity_errors == []
    assert len(report.size_errors) == 1
    assert "wrong-size" in report.size_errors[0]


def test_validate_manifest_schema_accepts_conformant_documents(
    sample_manifest_entry: dict,
) -> None:
    sample_manifest_entry["bytes"] = 123
    sample_manifest_entry["pages"] = 4
    manifest = {"documents": [sample_manifest_entry]}
    download_corpus.validate_manifest_schema(manifest)  # no raise


def test_validate_manifest_schema_rejects_string_bytes(
    sample_manifest_entry: dict,
) -> None:
    # REQ-CORPUS-03 : dette de validation de schéma fermée en Brique 5 —
    # une valeur YAML mal typée en string ne doit plus glisser en silence.
    sample_manifest_entry["bytes"] = "123"
    manifest = {"documents": [sample_manifest_entry]}
    with pytest.raises(download_corpus.CorpusSchemaError) as excinfo:
        download_corpus.validate_manifest_schema(manifest)
    message = str(excinfo.value)
    assert "bytes" in message
    assert sample_manifest_entry["doc_id"] in message


def test_validate_manifest_schema_rejects_malformed_sha256(
    sample_manifest_entry: dict,
) -> None:
    sample_manifest_entry["sha256"] = "not-a-hash"
    manifest = {"documents": [sample_manifest_entry]}
    with pytest.raises(download_corpus.CorpusSchemaError, match="sha256"):
        download_corpus.validate_manifest_schema(manifest)


def test_validate_manifest_schema_checks_derived_artifacts_too() -> None:
    manifest = {
        "documents": [],
        "derived_artifacts": {"pages_jsonl": {"bytes": "not-an-int", "sha256": "0" * 64}},
    }
    with pytest.raises(download_corpus.CorpusSchemaError, match="derived_artifacts.pages_jsonl"):
        download_corpus.validate_manifest_schema(manifest)


def test_validate_manifest_schema_accumulates_all_violations(
    sample_manifest_entry: dict,
) -> None:
    sample_manifest_entry["bytes"] = "123"
    sample_manifest_entry["sha256"] = "bad-hash"
    manifest = {"documents": [sample_manifest_entry]}
    with pytest.raises(download_corpus.CorpusSchemaError) as excinfo:
        download_corpus.validate_manifest_schema(manifest)
    message = str(excinfo.value)
    assert "bytes" in message
    assert "sha256" in message


def test_validate_manifest_schema_is_a_corpus_error(
    sample_manifest_entry: dict,
) -> None:
    """Brique 7 VCD consumer relies on the CorpusError umbrella."""
    sample_manifest_entry["bytes"] = "123"
    manifest = {"documents": [sample_manifest_entry]}
    with pytest.raises(download_corpus.CorpusError):
        download_corpus.validate_manifest_schema(manifest)


def test_real_manifest_satisfies_the_schema() -> None:
    """Regression guard on the committed corpus/manifest.yaml itself."""
    manifest = download_corpus.load_manifest(download_corpus.MANIFEST_PATH)
    download_corpus.validate_manifest_schema(manifest)  # no raise


def test_load_manifest_roundtrip(tmp_path: Path, sample_manifest_entry: dict) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "license": "Test",
                "source_authority": "TEST",
                "documents": [sample_manifest_entry],
            }
        ),
        encoding="utf-8",
    )
    loaded = download_corpus.load_manifest(manifest_path)
    assert loaded["schema_version"] == 1
    assert loaded["documents"][0]["doc_id"] == "sample"
