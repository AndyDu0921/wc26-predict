"""Immutable model artifact bundle loading and integrity verification."""

from __future__ import annotations

import hashlib
import hmac
import json
import pickle
from pathlib import Path
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[2]
ACTIVE_BUNDLE_PATH = BACKEND_DIR / "artifacts" / "active_bundle.json"
ACTIVE_STATUSES = {"legacy_active_unvalidated", "promoted"}


def load_active_bundle(path: str | Path = ACTIVE_BUNDLE_PATH) -> dict[str, Any]:
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"Active artifact bundle is missing: {manifest_path}. "
            "Run register_artifact_bundle.py or train_models.py."
        )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "model_artifact_bundle.v1":
        raise ValueError("Unsupported active artifact bundle schema")
    status = str(payload.get("status") or "")
    if status not in ACTIVE_STATUSES:
        raise ValueError(
            f"Artifact bundle status {status!r} is not eligible for production loading"
        )
    if status == "promoted" and not isinstance(payload.get("promotion_evidence"), dict):
        raise ValueError("Promoted artifact bundle is missing structured promotion evidence")
    if not isinstance(payload.get("components"), dict):
        raise ValueError("Artifact bundle has no component map")
    return payload


def verified_artifact_path(
    component: str,
    *,
    bundle_path: str | Path = ACTIVE_BUNDLE_PATH,
) -> Path:
    candidate, record, expected_hash = _component_record(component, bundle_path)
    actual_hash = sha256_file(candidate)
    if not hmac.compare_digest(actual_hash, expected_hash):
        raise RuntimeError(
            f"Artifact integrity mismatch for {component}: "
            f"expected={expected_hash}, actual={actual_hash}"
        )
    expected_size = record.get("size_bytes")
    if expected_size is not None and int(expected_size) != candidate.stat().st_size:
        raise RuntimeError(f"Artifact size mismatch for {component}")
    return candidate


def load_verified_pickle(
    component: str,
    *,
    bundle_path: str | Path = ACTIVE_BUNDLE_PATH,
) -> Any:
    """Deserialize a local artifact only after in-memory SHA-256 verification.

    The bytes are read and hashed before deserialization, avoiding the
    verify-then-reopen race that a path-only check would permit. The manifest
    itself remains trusted deployment configuration and must not be writable by
    an untrusted runtime identity.
    """
    candidate, record, expected_hash = _component_record(component, bundle_path)
    payload = candidate.read_bytes()
    expected_size = record.get("size_bytes")
    if expected_size is not None and int(expected_size) != len(payload):
        raise RuntimeError(f"Artifact size mismatch for {component}")
    actual_hash = hashlib.sha256(payload).hexdigest()
    if not hmac.compare_digest(actual_hash, expected_hash):
        raise RuntimeError(
            f"Artifact integrity mismatch for {component}: "
            f"expected={expected_hash}, actual={actual_hash}"
        )
    # Pickle is required for the fitted sklearn/stats model graph. The payload
    # is local, path-confined, size-checked, and SHA-256 verified above.
    return pickle.loads(payload)  # nosec B301


def _component_record(
    component: str,
    bundle_path: str | Path,
) -> tuple[Path, dict[str, Any], str]:
    bundle = load_active_bundle(bundle_path)
    record = (bundle.get("components") or {}).get(component)
    if not isinstance(record, dict):
        raise KeyError(f"Component {component!r} is not registered in the active bundle")
    relative_path = str(record.get("path") or "").strip()
    expected_hash = str(record.get("sha256") or "").strip().lower()
    if not relative_path or len(expected_hash) != 64:
        raise ValueError(f"Component {component!r} has incomplete path/hash metadata")
    try:
        int(expected_hash, 16)
    except ValueError as exc:
        raise ValueError(f"Component {component!r} has a non-hex SHA-256 digest") from exc
    candidate = (BACKEND_DIR.parent / relative_path).resolve()
    repo_root = BACKEND_DIR.parent.resolve()
    if repo_root not in candidate.parents:
        raise ValueError(f"Component {component!r} escapes the repository root")
    if not candidate.is_file():
        raise FileNotFoundError(f"Registered component is missing: {candidate}")
    return candidate, record, expected_hash


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def active_bundle_provenance(path: str | Path = ACTIVE_BUNDLE_PATH) -> dict[str, Any]:
    bundle = load_active_bundle(path)
    return {
        "bundle_id": bundle.get("bundle_id"),
        "status": bundle.get("status"),
        "promotion_evidence": bool(bundle.get("promotion_evidence")),
        "training_data": bundle.get("training_data", {}),
        "components": {
            name: {
                "path": record.get("path"),
                "sha256": record.get("sha256"),
            }
            for name, record in (bundle.get("components") or {}).items()
            if isinstance(record, dict)
        },
    }
