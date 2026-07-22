"""Shadow candidate experiment runner.

This module evaluates candidate probability distributions against the current
snapshot probabilities using paired proper scoring rules.  It never updates
production weights or model artifacts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import numpy as np

from app.services.evaluation_registry import build_evaluation_registry
from app.services.shadow_candidate_models import build_shadow_candidate_prediction, candidate_family


OUTCOMES = ("home", "draw", "away")
UNIFORM = {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}


@dataclass(frozen=True)
class CandidateExperimentConfig:
    candidate_name: str = "uniform_baseline"
    min_sample_count: int = 30
    competition: str = "FIFA World Cup 2026"
    champion_name: str = "current_fusion"
    include_predictions: bool = False
    required_model_cohort: str | None = None


def run_candidate_experiment(
    db_path: str,
    *,
    config: CandidateExperimentConfig | None = None,
) -> dict[str, Any]:
    """Run a paired candidate experiment against current snapshot probs."""
    cfg = config or CandidateExperimentConfig()
    registry = build_evaluation_registry(db_path, competition=cfg.competition)
    eligible = [row for row in registry["samples"] if row["eligible_for_backtest"]]
    if cfg.required_model_cohort:
        eligible = [
            row for row in eligible
            if row.get("model_cohort") == cfg.required_model_cohort
        ]

    if len(eligible) < cfg.min_sample_count:
        return {
            "schema_version": "candidate_experiment.v2",
            "experiment_id": str(uuid4()),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "candidate_name": cfg.candidate_name,
            "candidate_family": candidate_family(cfg.candidate_name),
            "champion_name": cfg.champion_name,
            "required_model_cohort": cfg.required_model_cohort,
            "sample_registry_hash": registry["registry_hash"],
            "sample_registry_summary": registry["summary"],
            "sample_quality_summary": _sample_quality_summary(registry["samples"]),
            "status": "rejected",
            "reason": f"eligible sample count {len(eligible)} < {cfg.min_sample_count}",
            "n_samples": len(eligible),
            "leakage_checks": _leakage_summary(registry["samples"]),
            "gate_decision": {
                "status": "shadow_rejected",
                "passed": False,
                "reasons": ["insufficient_eligible_samples"],
            },
        }

    paired_rows = []
    candidate_availability: dict[str, int] = {"available": 0, "unavailable": 0}
    unavailable_reasons: dict[str, int] = {}
    for row in eligible:
        actual_idx = _actual_index(row["actual_home_goals"], row["actual_away_goals"])
        current = row["current_probs"]
        candidate_result = build_shadow_candidate_prediction(
            cfg.candidate_name,
            row,
            db_path=db_path,
            registry_rows=registry["samples"],
        )
        if not candidate_result.available:
            candidate_availability["unavailable"] += 1
            unavailable_reasons[candidate_result.reason] = unavailable_reasons.get(candidate_result.reason, 0) + 1
            continue
        candidate_availability["available"] += 1
        candidate = candidate_result.probs
        if actual_idx is None or current is None or candidate is None:
            continue
        paired_rows.append(
            {
                "sample_id": row["sample_id"],
                "stage": row["stage"],
                "model_cohort": row.get("model_cohort", "unknown"),
                "probability_quality_issues": row.get("probability_quality_issues", []),
                "actual_idx": actual_idx,
                "current_probs": current,
                "candidate_probs": candidate,
                "score_matrix": row.get("score_matrix"),
                "candidate_score_matrix": candidate_result.score_matrix,
                "actual_home_goals": row["actual_home_goals"],
                "actual_away_goals": row["actual_away_goals"],
                "candidate_payload": candidate_result.payload,
            }
        )

    if len(paired_rows) < cfg.min_sample_count:
        return {
            "schema_version": "candidate_experiment.v2",
            "experiment_id": str(uuid4()),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "candidate_name": cfg.candidate_name,
            "candidate_family": candidate_family(cfg.candidate_name),
            "champion_name": cfg.champion_name,
            "required_model_cohort": cfg.required_model_cohort,
            "sample_registry_hash": registry["registry_hash"],
            "sample_registry_summary": registry["summary"],
            "sample_quality_summary": _sample_quality_summary(registry["samples"]),
            "status": "rejected",
            "reason": f"paired sample count {len(paired_rows)} < {cfg.min_sample_count}",
            "n_samples": len(paired_rows),
            "candidate_availability": candidate_availability,
            "unavailable_reasons": unavailable_reasons,
            "leakage_checks": _leakage_summary(registry["samples"]),
            "gate_decision": {
                "status": "shadow_rejected",
                "passed": False,
                "reasons": ["insufficient_paired_samples"],
            },
        }

    current_metrics = _aggregate_metrics(
        [row["current_probs"] for row in paired_rows],
        [row["actual_idx"] for row in paired_rows],
        paired_rows,
        score_matrix_key="score_matrix",
    )
    candidate_metrics = _aggregate_metrics(
        [row["candidate_probs"] for row in paired_rows],
        [row["actual_idx"] for row in paired_rows],
        paired_rows,
        score_matrix_key="candidate_score_matrix",
    )
    paired_deltas = _paired_deltas(paired_rows)
    group_metrics = _group_metrics(paired_rows)
    robustness_metrics = _robustness_metrics(paired_rows)
    status = "completed"
    gate_decision = _shadow_gate_decision(
        paired_deltas,
        group_metrics,
        robustness_metrics,
    )

    return {
        "schema_version": "candidate_experiment.v2",
        "experiment_id": str(uuid4()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_name": cfg.candidate_name,
        "candidate_family": candidate_family(cfg.candidate_name),
        "champion_name": cfg.champion_name,
        "required_model_cohort": cfg.required_model_cohort,
        "sample_registry_hash": registry["registry_hash"],
        "sample_registry_summary": registry["summary"],
        "sample_quality_summary": _sample_quality_summary(registry["samples"]),
        "status": status,
        "n_samples": len(paired_rows),
        "candidate_availability": candidate_availability,
        "unavailable_reasons": unavailable_reasons,
        "metrics_current": current_metrics,
        "metrics_candidate": candidate_metrics,
        "robustness_metrics": robustness_metrics,
        "paired_deltas": paired_deltas,
        "score_paired_evidence": _score_paired_evidence(paired_rows),
        "group_metrics": group_metrics,
        "leakage_checks": _leakage_summary(registry["samples"]),
        "gate_decision": gate_decision,
        "candidate_predictions": _candidate_prediction_rows(paired_rows) if cfg.include_predictions else [],
        "notes": "Shadow-only experiment; no production weights or artifacts were modified.",
    }


def _candidate_prediction_rows(paired_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = []
    labels = ("home", "draw", "away")
    for row in paired_rows:
        payload.append(
            {
                "sample_id": row["sample_id"],
                "actual_result": labels[int(row["actual_idx"])],
                "current_probs": row["current_probs"],
                "candidate_probs": row["candidate_probs"],
                "component_payload": row.get("candidate_payload") or {},
                "candidate_score_matrix_available": isinstance(
                    row.get("candidate_score_matrix"), list
                ),
            }
        )
    return payload


def _sample_quality_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [row for row in rows if row.get("eligible_for_backtest")]
    return {
        "total_samples": len(rows),
        "eligible_samples": len(eligible),
        "strict_samples": sum(1 for row in rows if row.get("sample_status") == "strict"),
        "diagnostic_samples": sum(1 for row in rows if row.get("sample_status") == "diagnostic"),
        "rejected_samples": sum(1 for row in rows if row.get("sample_status") == "rejected"),
        "clean_leakage_samples": sum(1 for row in rows if row.get("leakage_status") == "clean"),
        "current_probability_samples": sum(1 for row in rows if isinstance(row.get("current_probs"), dict)),
        "score_matrix_samples": sum(
            1 for row in rows if bool((row.get("data_availability") or {}).get("score_matrix"))
        ),
        "process_eval_samples": sum(
            1 for row in rows if bool((row.get("data_availability") or {}).get("process_eval"))
        ),
        "avg_data_completeness_score": _mean([
            float(row.get("data_completeness_score") or 0.0)
            for row in rows
        ]),
        "exact_zero_probability_samples": sum(
            1 for row in rows
            if "exact_zero_probability" in row.get("probability_quality_issues", [])
        ),
        "strict_model_cohort_counts": _count_values(
            row.get("model_cohort", "unknown")
            for row in eligible
        ),
    }


def _count_values(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _robustness_metrics(paired_rows: list[dict[str, Any]]) -> dict[str, Any]:
    clean_boundary = [
        row for row in paired_rows
        if not row.get("probability_quality_issues")
    ]
    cohort_groups: dict[str, list[dict[str, Any]]] = {}
    for row in paired_rows:
        cohort_groups.setdefault(str(row.get("model_cohort") or "unknown"), []).append(row)
    return {
        "excluding_boundary_probabilities": {
            "n": len(clean_boundary),
            "paired_deltas": _paired_deltas(clean_boundary) if clean_boundary else None,
            "current": _aggregate_metrics(
                [row["current_probs"] for row in clean_boundary],
                [row["actual_idx"] for row in clean_boundary],
                clean_boundary,
                score_matrix_key="score_matrix",
            ) if clean_boundary else None,
            "candidate": _aggregate_metrics(
                [row["candidate_probs"] for row in clean_boundary],
                [row["actual_idx"] for row in clean_boundary],
                clean_boundary,
                score_matrix_key="candidate_score_matrix",
            ) if clean_boundary else None,
        },
        "by_model_cohort": {
            cohort: {
                "n": len(rows),
                "current": _aggregate_metrics(
                    [row["current_probs"] for row in rows],
                    [row["actual_idx"] for row in rows],
                    rows,
                    score_matrix_key="score_matrix",
                ),
                "candidate": _aggregate_metrics(
                    [row["candidate_probs"] for row in rows],
                    [row["actual_idx"] for row in rows],
                    rows,
                    score_matrix_key="candidate_score_matrix",
                ),
            }
            for cohort, rows in sorted(cohort_groups.items())
        },
    }


def _actual_index(home_goals: int | None, away_goals: int | None) -> int | None:
    if home_goals is None or away_goals is None:
        return None
    if home_goals > away_goals:
        return 0
    if home_goals == away_goals:
        return 1
    return 2


def _aggregate_metrics(
    probs_list: list[dict[str, float]],
    actuals: list[int],
    paired_rows: list[dict[str, Any]],
    *,
    score_matrix_key: str | None = None,
) -> dict[str, float | int | None]:
    briers = [_brier(probs, actual) for probs, actual in zip(probs_list, actuals)]
    loglosses = [_logloss(probs, actual) for probs, actual in zip(probs_list, actuals)]
    rps_values = [_rps(probs, actual) for probs, actual in zip(probs_list, actuals)]
    directions = [
        1 if int(np.argmax(_vec(probs))) == actual else 0
        for probs, actual in zip(probs_list, actuals)
    ]
    score_summary = _score_logloss_summary(paired_rows, matrix_key=score_matrix_key)
    return {
        "n": len(probs_list),
        "brier": _mean(briers),
        "logloss": _mean(loglosses),
        "rps": _mean(rps_values),
        "direction_accuracy": _mean(directions),
        "ece": _ece(probs_list, actuals),
        "score_logloss": score_summary["mean"],
        "score_logloss_n": score_summary["n"],
        "score_out_of_support_count": score_summary["out_of_support_count"],
    }


def _paired_deltas(paired_rows: list[dict[str, Any]]) -> dict[str, Any]:
    metric_deltas: dict[str, list[float]] = {"brier": [], "logloss": [], "rps": []}
    for row in paired_rows:
        actual = row["actual_idx"]
        current = row["current_probs"]
        candidate = row["candidate_probs"]
        metric_deltas["brier"].append(_brier(candidate, actual) - _brier(current, actual))
        metric_deltas["logloss"].append(_logloss(candidate, actual) - _logloss(current, actual))
        metric_deltas["rps"].append(_rps(candidate, actual) - _rps(current, actual))
    return {
        metric: {
            "mean_delta": _mean(values),
            "ci95": _bootstrap_ci95(values),
            "ci_method": "paired_bootstrap_percentile_v1",
            "n": len(values),
            "lower_is_better": True,
        }
        for metric, values in metric_deltas.items()
    }


def _group_metrics(paired_rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {"group_stage": [], "knockout": []}
    for row in paired_rows:
        stage = str(row.get("stage") or "")
        key = "knockout" if _is_knockout(stage) else "group_stage"
        groups[key].append(row)
    result = {}
    for key, rows in groups.items():
        if not rows:
            result[key] = {"n": 0}
            continue
        result[key] = {"n": len(rows), "metrics": _group_metric_block(rows)}
    return result


def _group_metric_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = {}
    for metric_name, fn in (("brier", _brier), ("logloss", _logloss), ("rps", _rps)):
        current_values = [fn(row["current_probs"], row["actual_idx"]) for row in rows]
        candidate_values = [fn(row["candidate_probs"], row["actual_idx"]) for row in rows]
        deltas = [candidate - current for candidate, current in zip(candidate_values, current_values)]
        metrics[metric_name] = {
            "current": _mean(current_values),
            "candidate": _mean(candidate_values),
            "candidate_minus_current": _mean(deltas),
            "ci95": _bootstrap_ci95(deltas),
            "ci_method": "paired_bootstrap_percentile_v1",
        }
    return metrics


def _shadow_gate_decision(
    paired_deltas: dict[str, Any],
    group_metrics: dict[str, Any] | None = None,
    robustness_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    worsened = [
        metric for metric, payload in paired_deltas.items()
        if float(payload["mean_delta"]) > 0
    ]
    if worsened:
        return {
            "status": "shadow_rejected",
            "passed": False,
            "reasons": [f"{metric}_worsened" for metric in worsened],
        }
    supported_improvements = [
        metric for metric, payload in paired_deltas.items()
        if float(payload["mean_delta"]) <= -0.001
        and float(payload.get("ci95", [0, 0])[1]) <= 0
    ]
    inconclusive = [
        metric for metric, payload in paired_deltas.items()
        if float(payload["mean_delta"]) <= -0.001
        and float(payload.get("ci95", [0, 0])[1]) > 0
    ]
    if inconclusive:
        return {
            "status": "shadow_needs_more_evidence",
            "passed": False,
            "reasons": [f"{metric}_ci_crosses_zero" for metric in inconclusive],
        }
    if len(supported_improvements) < 2:
        return {
            "status": "shadow_rejected",
            "passed": False,
            "reasons": ["fewer_than_two_supported_core_metric_improvements"],
        }
    degraded_groups = _degraded_groups(group_metrics or {})
    if degraded_groups:
        return {
            "status": "shadow_rejected",
            "passed": False,
            "reasons": [f"{item}_group_degraded" for item in degraded_groups],
        }
    boundary_reasons = _boundary_robustness_reasons(robustness_metrics or {})
    if boundary_reasons:
        return {
            "status": "shadow_rejected",
            "passed": False,
            "reasons": boundary_reasons,
        }
    return {
        "status": "shadow_candidate_only",
        "passed": True,
        "reasons": ["two_plus_supported_core_metric_improvements", "manual_review_required"],
    }


def _degraded_groups(group_metrics: dict[str, Any]) -> list[str]:
    degraded = []
    for group_name, payload in group_metrics.items():
        if int(payload.get("n", 0) or 0) < 5:
            continue
        metrics = payload.get("metrics") or {}
        for metric_name in ("brier", "logloss", "rps"):
            metric = metrics.get(metric_name) or {}
            if float(metric.get("candidate_minus_current", 0.0) or 0.0) > 0.02:
                degraded.append(f"{group_name}_{metric_name}")
    return degraded


def _leakage_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total_samples": len(rows),
        "snapshot_after_kickoff": sum(1 for row in rows if row["snapshot_before_kickoff"] is False),
        "snapshot_time_unknown": sum(1 for row in rows if row["snapshot_before_kickoff"] is None),
        "result_conflicts": sum(1 for row in rows if row["source_result_conflict"]),
        "excluded_samples": sum(1 for row in rows if not row["eligible_for_backtest"]),
    }


def _brier(probs: dict[str, float], actual: int) -> float:
    vec = _vec(probs)
    target = np.zeros(3)
    target[actual] = 1.0
    return float(((vec - target) ** 2).sum())


def _logloss(probs: dict[str, float], actual: int) -> float:
    return float(-math.log(max(_vec(probs)[actual], 1e-12)))


def _rps(probs: dict[str, float], actual: int) -> float:
    vec = _vec(probs)
    target = np.zeros(3)
    target[actual] = 1.0
    return float(((np.cumsum(vec) - np.cumsum(target)) ** 2).sum() / 2.0)


def _ece(probs_list: list[dict[str, float]], actuals: list[int], n_bins: int = 10) -> float:
    if not probs_list:
        return 0.0
    vectors = [_vec(probs) for probs in probs_list]
    conf = np.array([float(vec.max()) for vec in vectors])
    corr = np.array([
        1.0 if int(np.argmax(vec)) == actual else 0.0
        for vec, actual in zip(vectors, actuals)
    ])
    edges = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    for idx in range(n_bins):
        mask = (conf >= edges[idx]) & (conf < edges[idx + 1])
        if mask.sum() == 0:
            continue
        ece += float(mask.sum() / len(conf) * abs(corr[mask].mean() - conf[mask].mean()))
    return round(ece, 6)


def _score_logloss_summary(
    paired_rows: list[dict[str, Any]],
    *,
    matrix_key: str | None,
) -> dict[str, float | int | None]:
    if matrix_key is None:
        return {"mean": None, "n": 0, "out_of_support_count": 0}
    values: list[float] = []
    out_of_support_count = 0
    for row in paired_rows:
        matrix = row.get(matrix_key)
        hg = row.get("actual_home_goals")
        ag = row.get("actual_away_goals")
        if not isinstance(matrix, list) or hg is None or ag is None:
            continue
        if hg < len(matrix) and isinstance(matrix[hg], list) and ag < len(matrix[hg]):
            probability = max(float(matrix[hg][ag]), 1e-12)
        else:
            # A truncated matrix with no explicit tail assigned zero mass to
            # this outcome. Penalize it instead of excluding high-score games.
            probability = 1e-12
            out_of_support_count += 1
        values.append(-math.log(probability))
    return {
        "mean": _mean(values) if values else None,
        "n": len(values),
        "out_of_support_count": out_of_support_count,
    }


def _boundary_robustness_reasons(robustness_metrics: dict[str, Any]) -> list[str]:
    clean = robustness_metrics.get("excluding_boundary_probabilities") or {}
    if not clean:
        return []
    clean_n = int(clean.get("n", 0) or 0)
    by_cohort = robustness_metrics.get("by_model_cohort") or {}
    total_n = sum(int((payload or {}).get("n", 0) or 0) for payload in by_cohort.values())
    required_clean = max(20, math.ceil(total_n * 0.70))
    if clean_n < required_clean:
        return [f"boundary_clean_samples_{clean_n}_below_{required_clean}"]
    deltas = clean.get("paired_deltas") or {}
    worsened = [
        metric
        for metric, payload in deltas.items()
        if float((payload or {}).get("mean_delta", 0.0) or 0.0) > 0
    ]
    if worsened:
        return [f"boundary_clean_{metric}_worsened" for metric in worsened]
    supported = [
        metric
        for metric, payload in deltas.items()
        if float((payload or {}).get("mean_delta", 0.0) or 0.0) <= -0.001
        and float(((payload or {}).get("ci95") or [0.0, 1.0])[1]) <= 0
    ]
    if len(supported) < 2:
        return ["boundary_clean_slice_lacks_two_supported_improvements"]
    return []


def _score_paired_evidence(paired_rows: list[dict[str, Any]]) -> dict[str, Any]:
    deltas: list[float] = []
    for row in paired_rows:
        current = _score_probability_for_actual(row, "score_matrix")
        candidate = _score_probability_for_actual(row, "candidate_score_matrix")
        if current is None or candidate is None:
            continue
        deltas.append(-math.log(max(candidate, 1e-12)) + math.log(max(current, 1e-12)))
    return {
        "n": len(deltas),
        "mean_score_logloss_delta": _mean(deltas) if deltas else None,
        "ci95": _bootstrap_ci95(deltas) if deltas else None,
        "lower_is_better": True,
        "status": (
            "supported_improvement"
            if deltas and _mean(deltas) < 0 and _bootstrap_ci95(deltas)[1] <= 0
            else "inconclusive_or_worse"
        ),
    }


def _score_probability_for_actual(row: dict[str, Any], key: str) -> float | None:
    matrix = row.get(key)
    home_goals = row.get("actual_home_goals")
    away_goals = row.get("actual_away_goals")
    if not isinstance(matrix, list) or home_goals is None or away_goals is None:
        return None
    home = int(home_goals)
    away = int(away_goals)
    if home >= len(matrix) or not isinstance(matrix[home], list) or away >= len(matrix[home]):
        return 1e-12
    return max(float(matrix[home][away]), 1e-12)


def _vec(probs: dict[str, float]) -> np.ndarray:
    vec = np.array([float(probs[key]) for key in OUTCOMES], dtype=float)
    total = vec.sum()
    if total <= 0:
        return np.array([1 / 3, 1 / 3, 1 / 3], dtype=float)
    return vec / total


def _mean(values: list[float] | list[int]) -> float:
    return round(float(np.mean(values)), 6) if values else 0.0


def _bootstrap_ci95(values: list[float]) -> list[float]:
    if not values:
        return [0.0, 0.0]
    arr = np.array(values, dtype=float)
    if len(arr) == 1:
        val = float(arr[0])
        return [round(val, 6), round(val, 6)]
    seed = int(abs(float(arr.mean())) * 1_000_000) + len(arr) * 7919
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(arr), size=(2000, len(arr)))
    boot_means = arr[idx].mean(axis=1)
    low, high = np.percentile(boot_means, [2.5, 97.5])
    return [round(float(low), 6), round(float(high), 6)]


def _is_knockout(stage: str) -> bool:
    lowered = stage.lower()
    return any(token in lowered for token in ("round of", "quarter", "semi", "final", "third place"))
