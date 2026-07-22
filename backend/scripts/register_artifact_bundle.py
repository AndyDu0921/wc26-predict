#!/usr/bin/env python3
"""Register exact local model files as an immutable active bundle."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.artifact_bundle import ACTIVE_BUNDLE_PATH, sha256_file  # noqa: E402


def _promotion_evidence(path_text: str) -> dict[str, object]:
    if not path_text:
        raise ValueError("--promotion-evidence is required for status=promoted")
    path = Path(path_text).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        results = [payload]
    passed = [
        item
        for item in results
        if isinstance(item, dict)
        and bool((item.get("gate_decision") or {}).get("passed"))
        and int(item.get("n_samples", 0) or 0) >= 30
        and bool(item.get("required_model_cohort"))
        and bool(item.get("sample_registry_hash"))
    ]
    if not passed:
        raise ValueError(
            "Promotion evidence must contain a same-cohort candidate with "
            "gate_decision.passed=true and n_samples>=30"
        )
    try:
        relative = path.relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("Promotion evidence must live inside the repository") from exc
    return {
        "path": relative,
        "sha256": sha256_file(path),
        "candidate_names": sorted({str(item.get("candidate_name")) for item in passed}),
        "model_cohorts": sorted({str(item.get("required_model_cohort")) for item in passed}),
        "sample_registry_hashes": sorted({str(item.get("sample_registry_hash")) for item in passed}),
        "manual_activation_required": True,
    }


def _component(path_text: str) -> dict[str, object]:
    path = Path(path_text).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        relative = path.relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"Artifact must live inside repository: {path}") from exc
    return {
        "path": relative,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Register immutable model artifact bundle")
    parser.add_argument("--dc", required=True)
    parser.add_argument("--enhancer", required=True)
    parser.add_argument("--elo", required=True)
    parser.add_argument("--pi", required=True)
    parser.add_argument("--weibull", default="")
    parser.add_argument("--calibrator", default="")
    parser.add_argument("--wc-calibrator", default="")
    parser.add_argument("--stacking", default="")
    parser.add_argument("--conformal", default="")
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--training-cutoff", default="unknown")
    parser.add_argument("--training-fingerprint", default="unknown")
    parser.add_argument(
        "--promotion-evidence",
        default="",
        help="Paired same-cohort experiment JSON required for promoted status.",
    )
    parser.add_argument(
        "--status",
        choices=("legacy_active_unvalidated", "shadow_validated", "promoted"),
        default="legacy_active_unvalidated",
    )
    parser.add_argument("--output", default=str(ACTIVE_BUNDLE_PATH))
    args = parser.parse_args()

    promotion_evidence: bool | dict[str, object] = False
    if args.status == "promoted":
        promotion_evidence = _promotion_evidence(args.promotion_evidence)

    components = {
        "dixon_coles": _component(args.dc),
        "tabular_enhancer": _component(args.enhancer),
        "elo": _component(args.elo),
        "pi_rating": _component(args.pi),
    }
    if args.weibull:
        components["weibull"] = _component(args.weibull)
    for name, path_text in (
        ("calibrator", args.calibrator),
        ("calibrator_wc", args.wc_calibrator),
        ("stacking_meta_learner", args.stacking),
        ("conformal_predictor", args.conformal),
    ):
        if path_text:
            components[name] = _component(path_text)
    payload = {
        "schema_version": "model_artifact_bundle.v1",
        "bundle_id": args.bundle_id,
        "registered_at": datetime.now(UTC).isoformat(),
        "status": args.status,
        "promotion_evidence": promotion_evidence,
        "training_data": {
            "cutoff": args.training_cutoff,
            "fingerprint": args.training_fingerprint,
            "provenance_complete": (
                args.training_cutoff != "unknown" and args.training_fingerprint != "unknown"
            ),
        },
        "components": components,
        "notes": (
            "Legacy bundle registration does not prove predictive improvement. "
            "Promotion still requires temporal paired gates."
        ),
    }
    output = Path(args.output)
    if args.status == "shadow_validated" and output.resolve() == ACTIVE_BUNDLE_PATH.resolve():
        raise ValueError("shadow_validated bundles cannot replace active_bundle.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "bundle_id": args.bundle_id, "status": args.status}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
