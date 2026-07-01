#!/usr/bin/env python3
"""Enforce the ANSSI corpus non-alteration contract.

Reads ``corpus/manifest.yaml``, fetches missing PDFs when a direct URL is
available, and verifies every file's SHA256 against the manifest. Any divergence
aborts the run with a non-zero exit code and a message identifying the culprit.
The script is idempotent: a fully conformant corpus results in a no-op.

Design intent: the manifest is a written contract; this script is its automated
verification. Together they materialize the "corpus verrouillé par empreinte
cryptographique" property cited by the VCD (Brique 7). Silent drift is refused
by construction — any change must be either restored or explicitly acknowledged
by bumping the manifest.

Upstream requirement: corpus non-alteration (to be catalogued as REQ-CORPUS-01
in Brique 5; cited by VCD §corpus in Brique 7).
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = REPO_ROOT / "corpus" / "manifest.yaml"
PDF_DIR = REPO_ROOT / "corpus" / "pdfs"

USER_AGENT = (
    "llm-verification-harness/0.1 "
    "(+https://github.com/adriencr81/llm-verification-harness)"
)
DOWNLOAD_TIMEOUT_S = 30
CHUNK_SIZE_BYTES = 64 * 1024


class CorpusError(RuntimeError):
    """Base class for any failure raised by the corpus verification pipeline.

    Consumers (notably the VCD generator in Brique 7) can catch this single type
    to react to "any corpus contract violation" without having to enumerate every
    subclass.
    """


class CorpusIntegrityError(CorpusError):
    """A PDF on disk diverges from the SHA256 declared in the manifest."""


class MissingSourceError(CorpusError):
    """A PDF is absent and cannot be fetched automatically."""


@dataclass
class VerificationReport:
    """Aggregated result of a corpus verification pass.

    ``.ok`` is the single boolean the caller checks to decide whether to
    continue; the two lists carry human-readable messages for logging and for
    inclusion in the VCD evidence appendix.
    """

    integrity_errors: list[str] = field(default_factory=list)
    missing_errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.integrity_errors and not self.missing_errors


def sha256_of_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK_SIZE_BYTES), b""):
            hasher.update(block)
    return hasher.hexdigest()


def load_manifest(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def fetch(url: str, destination: Path) -> None:
    """Fetch ``url`` to ``destination`` atomically via a ``.tmp`` sidecar.

    A partial write from a broken connection stays confined to the sidecar and
    is cleaned up before the exception propagates, so the next run never sees
    a truncated PDF and never confuses "download interrupted" with "corpus
    altered".
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = destination.with_name(destination.name + ".tmp")
    try:
        with requests.get(
            url,
            stream=True,
            timeout=DOWNLOAD_TIMEOUT_S,
            headers={"User-Agent": USER_AGENT},
            allow_redirects=True,
        ) as response:
            response.raise_for_status()
            with tmp_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE_BYTES):
                    handle.write(chunk)
        tmp_path.replace(destination)
    finally:
        tmp_path.unlink(missing_ok=True)


def verify_document(doc: dict, pdf_dir: Path) -> None:
    """Verify a single document against the manifest contract.

    Reads from ``doc`` the keys ``doc_id``, ``filename``, ``sha256`` (all
    required), plus ``download_url`` and ``landing_page`` (both optional; they
    drive the fetch behaviour when the file is missing).

    Raises :class:`CorpusIntegrityError` when the on-disk SHA256 diverges from
    the declared hash. Raises :class:`MissingSourceError` when the file is
    absent and cannot be fetched (either no direct URL, or the fetch itself
    failed).

    A file freshly downloaded whose SHA256 does not match the manifest is left
    on disk for inspection, not silently re-downloaded. The user must
    explicitly delete or restore it — this preserves the evidence trail.
    """
    doc_id = doc["doc_id"]
    filename = doc["filename"]
    expected_hash = doc["sha256"]
    local_path = pdf_dir / filename

    if not local_path.exists():
        download_url = doc.get("download_url")
        if not download_url:
            raise MissingSourceError(
                f"[{doc_id}] file missing and no direct download_url available.\n"
                f"  Fetch manually from: {doc.get('landing_page')}\n"
                f"  Save as: {local_path}\n"
                f"  Then re-run this script."
            )
        print(f"[{doc_id}] missing → downloading from {download_url}")
        try:
            fetch(download_url, local_path)
        except requests.RequestException as err:
            raise MissingSourceError(
                f"[{doc_id}] download failed ({err.__class__.__name__}: {err}).\n"
                f"  URL: {download_url}\n"
                f"  Fetch manually and save as: {local_path}\n"
                f"  Then re-run this script."
            ) from err

    actual_hash = sha256_of_file(local_path)
    if actual_hash != expected_hash:
        raise CorpusIntegrityError(
            f"[{doc_id}] SHA256 mismatch — corpus altered.\n"
            f"  file:     {local_path}\n"
            f"  expected: {expected_hash}\n"
            f"  actual:   {actual_hash}\n"
            f"  Refusing to proceed. Either restore the original file or bump "
            f"the manifest deliberately."
        )


def verify_all(documents: Iterable[dict], pdf_dir: Path) -> VerificationReport:
    """Verify each document; accumulate failures instead of stopping at the first.

    Returning the full list matters for IVVQ evidence: a partial report is
    less useful than a complete inventory of every violation.
    """
    report = VerificationReport()
    for doc in documents:
        doc_id = doc["doc_id"]
        try:
            verify_document(doc, pdf_dir)
            print(f"[{doc_id}] OK ({doc['sha256'][:12]}…)")
        except CorpusIntegrityError as err:
            report.integrity_errors.append(str(err))
            print(f"[{doc_id}] FAIL — integrity", file=sys.stderr)
        except MissingSourceError as err:
            report.missing_errors.append(str(err))
            print(f"[{doc_id}] FAIL — missing source", file=sys.stderr)
    return report


def main() -> int:
    manifest = load_manifest(MANIFEST_PATH)
    documents = manifest["documents"]
    print(f"Verifying {len(documents)} document(s) against {MANIFEST_PATH}")

    report = verify_all(documents, PDF_DIR)

    if report.integrity_errors:
        print("\n=== INTEGRITY FAILURES ===", file=sys.stderr)
        for err in report.integrity_errors:
            print(err, file=sys.stderr)
    if report.missing_errors:
        print("\n=== MISSING SOURCES ===", file=sys.stderr)
        for err in report.missing_errors:
            print(err, file=sys.stderr)

    if not report.ok:
        return 1
    print("\nAll documents conform to the manifest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
