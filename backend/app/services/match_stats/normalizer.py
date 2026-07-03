"""Match statistics normalizer: maps provider-specific fields to unified schema."""

from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Field alias map — each canonical field maps to a list of provider aliases
# ---------------------------------------------------------------------------
FIELD_ALIASES: Dict[str, List[str]] = {
    "xg": ["xg", "expected_goals", "expectedGoals", "expected_goals_xg", "xG"],
    "shots_total": ["shots", "total_shots", "shots_total", "shots-total", "SHOTS_TOTAL", "sh", "Sh"],
    "shots_on_target": [
        "shots_on_target", "shotsOnTarget", "shots-on-target",
        "SHOTS_ON_TARGET", "sot", "SoT", "shots_on_target_pct",
    ],
    "shots_inside_box": ["shots_inside_box", "shotsInsideBox", "shots_inside_area"],
    "possession_pct": [
        "possession", "ball_possession", "possession_pct",
        "BALL_POSSESSION", "possession_percentage",
    ],
    "corners": ["corners", "corner_kicks", "CORNERS", "corner_kicks_total"],
    "passes_attempted": [
        "passes", "total_passes", "passes_attempted", "passes_total",
        "totalPasses", "passesCompleted",
    ],
    "pass_accuracy_pct": [
        "pass_accuracy", "pass_accuracy_pct", "pass_accuracy_percentage",
        "passCompletionRate", "pass_completion",
    ],
    "final_third_entries": ["final_third_entries", "attacking_third_passes", "finalThirdEntries"],
    "fouls": ["fouls", "FOULS", "fouls_committed", "fls", "Fls"],
    "yellow_cards": ["yellow_cards", "yellowcards", "crdy", "CrdY", "yellowCards"],
    "red_cards": ["red_cards", "redcards", "crdr", "CrdR", "redCards"],
    "saves": ["saves", "goalkeeper_saves", "Saves", "goalkeeperSaves"],
    "clearances": ["clearances", "clearances_total", "total_clearances"],
    "interceptions": ["interceptions", "int", "Int", "total_interceptions"],
    "tackles": ["tackles", "tklw", "TklW", "tackles_won", "total_tackles"],
    "goals": ["goals", "gls", "Gls", "goals_scored", "gf", "GF"],
    "big_chances": ["big_chances", "bigChances", "big_chances_created"],
    "penalties_awarded": ["penalties_awarded", "pkatt", "PKatt", "penalties"],
    "penalties_scored": ["penalties_scored", "pk", "PK", "penalty_goals"],
    "own_goals": ["own_goals", "og", "OG", "ownGoals"],
}


def resolve_field(raw_stats: Dict[str, Any], canonical_name: str) -> Optional[Any]:
    """Resolve a canonical field name from raw provider data using alias matching.

    Args:
        raw_stats: Raw provider stats dictionary.
        canonical_name: Canonical field name (e.g. 'xg', 'shots_total').

    Returns:
        The matched value or None.
    """
    # O(keys × aliases) — first match wins.  Heuristic is acceptable
    # because raw provider payloads are small (typically 30-80 keys).
    aliases = FIELD_ALIASES.get(canonical_name, [canonical_name])
    for alias in aliases:
        # Direct key match
        if alias in raw_stats:
            return raw_stats[alias]
        # Case-insensitive match
        for k, v in raw_stats.items():
            if k.lower().replace(" ", "_").replace("-", "_") == alias.lower():
                return v
    return None


def resolve_all(raw_stats: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve all canonical fields from raw stats.

    Returns:
        Dict of canonical_name → value (None if not found).
    """
    result: Dict[str, Any] = {}
    for canonical in FIELD_ALIASES:
        result[canonical] = resolve_field(raw_stats, canonical)
    return result


def safe_float(value: Any) -> Optional[float]:
    """Convert value to float, stripping % signs."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        s = str(value).replace("%", "").strip()
        return float(s)
    except (ValueError, TypeError):
        return None


def safe_int(value: Any) -> Optional[int]:
    """Convert value to int."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == value:  # not NaN
        return int(value)
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None
