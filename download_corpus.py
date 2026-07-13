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

Upstream requirements (see docs/REQUIREMENTS.md, registre gelé depuis la
Brique 5, cité par le VCD §corpus en Brique 7) :
- REQ-CORPUS-01 : non-altération binaire (SHA256) — enforcée à chaque run.
- REQ-CORPUS-03 : sanity check de taille (bytes) — opt-in, exécuté avant
  SHA256 quand le champ `bytes` est présent au manifest. Le sous-volet
  schéma (`bytes`/`pages` doivent être des int, `sha256` un hex64) est
  vérifié en amont par `validate_manifest_schema` — Brique 5.
"""

from __future__ import annotations

import hashlib
import re
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


class CorpusSizeError(CorpusError):
    """A PDF's on-disk size diverges from the ``bytes`` declared in the manifest.

    Detected before the SHA256 pass — cheap sanity check, especially useful
    for the 3 docs behind a ``signed_url`` where the download is manual and
    a truncated file or the wrong file can be silently deposited.

    Distinct from :class:`CorpusIntegrityError` on purpose: a size mismatch
    says "you deposited a different file", a SHA256 mismatch says "you
    modified this file's content". Different IVVQ signals, different
    triage.

    Upstream requirement: REQ-CORPUS-03 (see docs/REQUIREMENTS.md).
    """


class MissingSourceError(CorpusError):
    """A PDF is absent and cannot be fetched automatically."""


class CorpusSchemaError(CorpusError):
    """A manifest entry violates the field-type contract.

    Closes the schema-validation debt flagged by REQ-CORPUS-03 (see
    docs/REQUIREMENTS.md): a YAML ``bytes: "123"`` (quoted string) would
    otherwise slide through ``verify_document`` and silently poison the
    size check downstream (``str != int`` never matches, so the check
    just never fires — a much more confusing failure than a schema
    error caught at the manifest boundary). Brique 5.
    """


_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_INT_FIELDS = ("bytes", "pages")


def _validate_entry_schema(entry: dict, label: str) -> list[str]:
    """Type-check the contracted fields of one manifest entry.

    Applies to both ``documents[]`` and ``derived_artifacts.*`` — both
    sections carry the same ``sha256``/``bytes`` contract.
    """
    errors: list[str] = []
    for name in _INT_FIELDS:
        if name in entry and not isinstance(entry[name], int):
            errors.append(
                f"[{label}] '{name}' must be an int, got "
                f"{type(entry[name]).__name__} ({entry[name]!r})"
            )
    sha256 = entry.get("sha256")
    if sha256 is not None and not _SHA256_RE.fullmatch(str(sha256)):
        errors.append(
            f"[{label}] 'sha256' must be a 64-char lowercase hex string, "
            f"got {sha256!r}"
        )
    return errors


def validate_manifest_schema(manifest: dict) -> None:
    """Type-check every ``documents[]`` and ``derived_artifacts.*`` entry.

    Raises :class:`CorpusSchemaError` listing every violation found —
    same accumulate-don't-stop-at-first-failure posture as
    :func:`verify_all`. Call this before :func:`verify_all` so a
    malformed manifest is refused before any disk I/O or network call.
    """
    errors: list[str] = []
    for doc in manifest.get("documents", []):
        errors.extend(_validate_entry_schema(doc, doc.get("doc_id", "<unknown>")))
    for name, entry in manifest.get("derived_artifacts", {}).items():
        errors.extend(_validate_entry_schema(entry, f"derived_artifacts.{name}"))
    if errors:
        raise CorpusSchemaError(
            "Manifest schema violation(s):\n" + "\n".join(errors)
        )


@dataclass
class VerificationReport:
    """Aggregated result of a corpus verification pass.

    ``.ok`` is the single boolean the caller checks to decide whether to
    continue; the two lists carry human-readable messages for logging and for
    inclusion in the VCD evidence appendix.
    """

    integrity_errors: list[str] = field(default_factory=list)
    missing_errors: list[str] = field(default_factory=list)
    size_errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (
            self.integrity_errors or self.missing_errors or self.size_errors
        )


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

    Raises :class:`CorpusSizeError` when ``bytes`` is declared in the manifest
    and diverges from the actual file size — checked before SHA256 so that a
    wrong-file swap is caught by the cheap check rather than the expensive
    one. The size check is opt-in: a manifest entry without ``bytes`` skips
    it silently.

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

    # REQ-CORPUS-03 — opt-in fail-fast filter (catches wrong-file swap /
    # truncation without paying for a full hash). Never a substitute for
    # the SHA256 pass below: two files of identical length can still
    # differ byte for byte.
    declared_size = doc.get("bytes")
    if declared_size is not None:
        actual_size = local_path.stat().st_size
        if actual_size != declared_size:
            raise CorpusSizeError(
                f"[{doc_id}] size mismatch — declared {declared_size} bytes, "
                f"file on disk is {actual_size} bytes.\n"
                f"  file:     {local_path}\n"
                f"  Restore the original file or bump the manifest "
                f"(bytes + sha256) deliberately."
            )

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
        except CorpusSizeError as err:
            report.size_errors.append(str(err))
            print(f"[{doc_id}] FAIL — size", file=sys.stderr)
        except MissingSourceError as err:
            report.missing_errors.append(str(err))
            print(f"[{doc_id}] FAIL — missing source", file=sys.stderr)
    return report


def main() -> int:
    manifest = load_manifest(MANIFEST_PATH)
    documents = manifest["documents"]

    try:
        validate_manifest_schema(manifest)
    except CorpusSchemaError as err:
        print(f"\n=== SCHEMA FAILURES ===\n{err}", file=sys.stderr)
        return 1

    print(f"Verifying {len(documents)} document(s) against {MANIFEST_PATH}")

    report = verify_all(documents, PDF_DIR)

    if report.integrity_errors:
        print("\n=== INTEGRITY FAILURES ===", file=sys.stderr)
        for err in report.integrity_errors:
            print(err, file=sys.stderr)
    if report.size_errors:
        print("\n=== SIZE FAILURES ===", file=sys.stderr)
        for err in report.size_errors:
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
