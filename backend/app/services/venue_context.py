"""Venue context detection — home/neutral advantage analysis for WC26.

Detects when a nominally "neutral" venue confers meaningful home advantage:
- Host country advantage (venue in team's home country)
- Home-like venue (national stadium, frequent home venue)
- High altitude (>1500m physiological impact)
- High crowd pressure (>70k capacity, host team playing)

Background:
    MEX-ECU at Estadio Azteca was the driver.  FIFA treats all WC matches as
    neutral (is_neutral=True), but Azteca is Mexico's national stadium at
    2,240m altitude with 87,523 capacity.  Mexico trains there, plays all
    home qualifiers there, and the crowd is 90%+ Mexican.  From a model
    perspective, this is NOT neutral — it's a de-facto home match.

    This module provides venue_context analysis that feeds into:
    - prediction preflight gates (warn on venue advantage)
    - snapshot risk_tags (record the context)
    - process evaluator (flag venue advantage as MODEL_INPUT_ERROR)
    - failure classifier (consider venue context in attribution)
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── WC26 Team → Country mapping (all 48 qualified teams) ──────────────

WC26_TEAM_COUNTRY: dict[str, str] = {
    # Group A
    "Canada": "Canada",
    "Cape Verde": "Cape Verde",
    "Germany": "Germany",
    "Paraguay": "Paraguay",
    # Group B
    "Brazil": "Brazil",
    "Haiti": "Haiti",
    "South Africa": "South Africa",
    "Turkey": "Turkey",
    # Group C
    "France": "France",
    "Iraq": "Iraq",
    "New Zealand": "New Zealand",
    "Sweden": "Sweden",
    # Group D
    "Argentina": "Argentina",
    "Austria": "Austria",
    "Jordan": "Jordan",
    "Saudi Arabia": "Saudi Arabia",
    # Group E
    "Algeria": "Algeria",
    "Belgium": "Belgium",
    "Norway": "Norway",
    "Senegal": "Senegal",
    # Group F
    "Colombia": "Colombia",
    "Czech Republic": "Czech Republic",
    "Costa Rica": "Costa Rica",
    "Uzbekistan": "Uzbekistan",
    # Group G
    "DR Congo": "DR Congo",
    "England": "England",
    "Ghana": "Ghana",
    "Iran": "Iran",
    # Group H
    "Spain": "Spain",
    "Uruguay": "Uruguay",
    "Côte d'Ivoire": "Côte d'Ivoire",
    "Ivory Coast": "Côte d'Ivoire",
    "Cote d'Ivoire": "Côte d'Ivoire",
    # Group I
    "Italy": "Italy",
    "Japan": "Japan",
    "Netherlands": "Netherlands",
    "Nigeria": "Nigeria",
    # Group J
    "Croatia": "Croatia",
    "Panama": "Panama",
    "Portugal": "Portugal",
    "Ukraine": "Ukraine",
    # Group K
    "Mexico": "Mexico",
    "Ecuador": "Ecuador",
    "South Korea": "South Korea",
    "Switzerland": "Switzerland",
    # Group L
    "Denmark": "Denmark",
    "Egypt": "Egypt",
    "Morocco": "Morocco",
    "Russia": "Russia",
    # Additional (non-WC teams that may appear in training data)
    "United States": "United States",
    "USA": "United States",
    "Bosnia and Herzegovina": "Bosnia and Herzegovina",
    "Bosnia": "Bosnia and Herzegovina",
    "BIH": "Bosnia and Herzegovina",
    "Côte d’Ivoire": "Côte d’Ivoire",  # curly-apostrophe variant
    "El Salvador": "El Salvador",
    "Peru": "Peru",
    "Chile": "Chile",
    "Bolivia": "Bolivia",
    "Venezuela": "Venezuela",
}

# ── Venue metadata (all 12 WC26 host stadiums + known venues) ────────

@dataclass(frozen=True)
class VenueMeta:
    """Static metadata for a stadium."""
    name: str
    city: str
    country: str  # "Mexico" / "United States" / "Canada"
    capacity: int
    altitude_m: int = 0
    is_national_stadium: bool = False
    typical_home_teams: tuple[str, ...] = ()

VENUE_DB: dict[str, VenueMeta] = {
    # ── Mexico ──
    "Estadio Azteca": VenueMeta(
        name="Estadio Azteca",
        city="Mexico City",
        country="Mexico",
        capacity=87523,
        altitude_m=2240,
        is_national_stadium=True,
        typical_home_teams=("Mexico",),
    ),
    "Estadio Akron": VenueMeta(
        name="Estadio Akron",
        city="Guadalajara",
        country="Mexico",
        capacity=49850,
        altitude_m=1560,
        typical_home_teams=("Mexico",),
    ),
    "Estadio BBVA": VenueMeta(
        name="Estadio BBVA",
        city="Monterrey",
        country="Mexico",
        capacity=53500,
        altitude_m=537,
        typical_home_teams=("Mexico",),
    ),
    # ── United States ──
    "MetLife Stadium": VenueMeta(
        name="MetLife Stadium",
        city="East Rutherford, NJ",
        country="United States",
        capacity=82500,
        altitude_m=3,
        typical_home_teams=("United States",),
    ),
    "AT&T Stadium": VenueMeta(
        name="AT&T Stadium",
        city="Arlington, TX",
        country="United States",
        capacity=80000,
        altitude_m=175,
        typical_home_teams=("United States", "Mexico"),
    ),
    "SoFi Stadium": VenueMeta(
        name="SoFi Stadium",
        city="Inglewood, CA",
        country="United States",
        capacity=70500,
        altitude_m=38,
        typical_home_teams=("United States", "Mexico"),
    ),
    "Mercedes-Benz Stadium": VenueMeta(
        name="Mercedes-Benz Stadium",
        city="Atlanta, GA",
        country="United States",
        capacity=71000,
        altitude_m=305,
        typical_home_teams=("United States",),
    ),
    "NRG Stadium": VenueMeta(
        name="NRG Stadium",
        city="Houston, TX",
        country="United States",
        capacity=72220,
        altitude_m=13,
        typical_home_teams=("United States", "Mexico"),
    ),
    "Levi's Stadium": VenueMeta(
        name="Levi's Stadium",
        city="Santa Clara, CA",
        country="United States",
        capacity=68500,
        altitude_m=3,
        typical_home_teams=("United States", "Mexico"),
    ),
    "Gillette Stadium": VenueMeta(
        name="Gillette Stadium",
        city="Foxborough, MA",
        country="United States",
        capacity=65878,
        altitude_m=80,
        typical_home_teams=("United States",),
    ),
    "Lumen Field": VenueMeta(
        name="Lumen Field",
        city="Seattle, WA",
        country="United States",
        capacity=68740,
        altitude_m=52,
        typical_home_teams=("United States",),
    ),
    "Hard Rock Stadium": VenueMeta(
        name="Hard Rock Stadium",
        city="Miami Gardens, FL",
        country="United States",
        capacity=65326,
        altitude_m=2,
        typical_home_teams=("United States",),
    ),
    # ── Canada ──
    "BC Place": VenueMeta(
        name="BC Place",
        city="Vancouver, BC",
        country="Canada",
        capacity=54320,
        altitude_m=4,
        typical_home_teams=("Canada",),
    ),
}

# ── Thresholds ──

HIGH_ALTITUDE_THRESHOLD_M = 1500   # Physiological impact on both teams
HIGH_CROWD_CAPACITY = 70000        # Large stadium threshold


@dataclass
class VenueContext:
    """Result of venue context analysis for a match."""

    venue_name: str = ""
    venue_country: str = ""
    venue_city: str = ""
    venue_capacity: int = 0
    altitude_m: int = 0

    # ── Detection flags ──
    is_high_altitude: bool = False
    """Altitude >= 1500m — physiological impact on both teams."""

    home_in_host_country: bool = False
    """Venue is in the home team's country."""

    away_in_host_country: bool = False
    """Venue is in the away team's country (rare but possible)."""

    is_home_like_venue: bool = False
    """Venue is a known home stadium / national stadium for the home team."""

    is_high_crowd_pressure: bool = False
    """Capacity >= 70k AND one of the teams is from the host country."""

    # ── Composite assessment ──
    effective_home_advantage: float = 0.0
    """Composite score 0.0 (truly neutral) to 1.0 (de-facto home match)."""

    advantage_team: str = ""
    """Which team benefits from the venue advantage ("" = neither)."""

    is_effectively_neutral: bool = True
    """False when the venue confers meaningful advantage to either team."""

    # ── Risk tags for downstream consumers ──
    risk_tags: list[str] = field(default_factory=list)
    """Machine-readable risk tags for snapshot/learning log."""

    warnings: list[str] = field(default_factory=list)
    """Human-readable warnings for reports."""


# ── Public API ──────────────────────────────────────────────────────

def detect_venue_context(
    venue: str | None,
    home_team: str,
    away_team: str,
    *,
    is_neutral: bool = True,
    team_country_map: dict[str, str] | None = None,
) -> VenueContext:
    """Analyze venue context for a match.

    Args:
        venue: Venue/stadium name (e.g. "Estadio Azteca").
        home_team: Home team name (must match WC26_TEAM_COUNTRY keys).
        away_team: Away team name.
        is_neutral: Whether the match is nominally neutral (FIFA flag).
        team_country_map: Optional custom team→country mapping.
            Falls back to WC26_TEAM_COUNTRY if not provided.

    Returns:
        VenueContext with all detection flags and risk tags.

    Usage:
        ctx = detect_venue_context("Estadio Azteca", "Mexico", "Ecuador")
        if not ctx.is_effectively_neutral:
            print(f"WARNING: {ctx.warnings}")
    """
    countries = team_country_map or WC26_TEAM_COUNTRY
    ctx = VenueContext()

    # ── Resolve venue metadata ──
    if not venue:
        if not is_neutral:
            ctx.risk_tags.append("venue_unknown_non_neutral")
            ctx.warnings.append("Non-neutral match but venue unknown — cannot assess advantage")
        return ctx

    ctx.venue_name = venue

    # Fuzzy match venue name (handle "Estadio Azteca (Estadio Banorte)" variants)
    meta = _resolve_venue(venue)
    if meta is None:
        if not is_neutral:
            ctx.risk_tags.append("venue_unknown_non_neutral")
            ctx.warnings.append(f"Venue '{venue}' not in venue database — cannot assess advantage")
        return ctx

    ctx.venue_country = meta.country
    ctx.venue_city = meta.city
    ctx.venue_capacity = meta.capacity
    ctx.altitude_m = meta.altitude_m

    # ── Resolve team countries ──
    home_country = _team_country(home_team, countries)
    away_country = _team_country(away_team, countries)

    # ── Detection: High Altitude ──
    if meta.altitude_m >= HIGH_ALTITUDE_THRESHOLD_M:
        ctx.is_high_altitude = True
        ctx.risk_tags.append("high_altitude")
        ctx.warnings.append(
            f"{meta.name} altitude {meta.altitude_m}m — "
            f"physiological impact on both teams (≥{HIGH_ALTITUDE_THRESHOLD_M}m threshold)"
        )

    # ── Detection: Host Country Advantage ──
    if home_country and meta.country == home_country:
        ctx.home_in_host_country = True
        ctx.risk_tags.append("host_country_home")
        ctx.warnings.append(
            f"Venue in {meta.country} — {home_team}'s home country. "
            f"FIFA is_neutral=True is misleading for model purposes."
        )

    if away_country and meta.country == away_country:
        ctx.away_in_host_country = True
        ctx.risk_tags.append("host_country_away")
        ctx.warnings.append(
            f"Venue in {meta.country} — {away_team}'s home country."
        )

    # ── Detection: Home-Like Venue ──
    if home_team in meta.typical_home_teams:
        ctx.is_home_like_venue = True
        ctx.risk_tags.append("home_like_venue")
        if meta.is_national_stadium:
            ctx.warnings.append(
                f"{meta.name} is {home_team}'s national stadium — "
                f"de-facto home match despite FIFA neutral designation."
            )
        else:
            ctx.warnings.append(
                f"{meta.name} is a frequent {home_team} home venue."
            )

    # ── Detection: High Crowd Pressure ──
    host_team_involved = (
        (home_country and meta.country == home_country) or
        (away_country and meta.country == away_country)
    )
    if meta.capacity >= HIGH_CROWD_CAPACITY and host_team_involved:
        ctx.is_high_crowd_pressure = True
        ctx.risk_tags.append("high_crowd_pressure")
        ctx.warnings.append(
            f"{meta.name} capacity {meta.capacity:,} with host team involved — "
            f"crowd advantage significant."
        )

    # ── Composite: Effective Home Advantage ──
    score = 0.0
    advantage_components: list[str] = []

    if ctx.home_in_host_country:
        score += 0.40
        advantage_components.append("host_country")
    if ctx.is_home_like_venue:
        score += 0.25
        advantage_components.append("home_like")
    if ctx.is_high_altitude:
        score += 0.15
        advantage_components.append("altitude")
    if ctx.is_high_crowd_pressure:
        score += 0.10
        advantage_components.append("crowd")

    # Altitude + host country = multiplicative effect (acclimatization)
    if ctx.is_high_altitude and ctx.home_in_host_country:
        score += 0.10  # bonus for acclimatization advantage

    ctx.effective_home_advantage = min(1.0, score)
    ctx.advantage_team = home_team if score > 0.15 else (
        away_team if ctx.away_in_host_country else ""
    )
    ctx.is_effectively_neutral = score < 0.15

    if not ctx.is_effectively_neutral:
        ctx.risk_tags.append("effective_home_advantage")
        ctx.warnings.append(
            f"Effective home advantage: {ctx.effective_home_advantage:.0%} "
            f"({', '.join(advantage_components)}) — treat as non-neutral."
        )

    return ctx


def venue_altitude(venue: str | None) -> int:
    """Quick lookup: altitude in meters for a venue. Returns 0 if unknown."""
    if not venue:
        return 0
    meta = _resolve_venue(venue)
    return meta.altitude_m if meta else 0


def venue_country(venue: str | None) -> str:
    """Quick lookup: host country of a venue. Returns '' if unknown."""
    if not venue:
        return ""
    meta = _resolve_venue(venue)
    return meta.country if meta else ""


def venue_capacity(venue: str | None) -> int:
    """Quick lookup: capacity of a venue. Returns 0 if unknown."""
    if not venue:
        return 0
    meta = _resolve_venue(venue)
    return meta.capacity if meta else 0


# ── Internal helpers ─────────────────────────────────────────────────

def _team_country(team: str, countries: dict[str, str]) -> str:
    """Resolve a team name to its country. Case-insensitive."""
    # Direct match
    if team in countries:
        return countries[team]
    # Case-insensitive
    team_lower = team.lower()
    for k, v in countries.items():
        if k.lower() == team_lower:
            return v
    # Try partial match (e.g. "Côte d'Ivoire" vs "Cote d'Ivoire")
    team_ascii = team_lower.replace("'", "").replace("’", "").replace("ô", "o").replace("é", "e")
    for k, v in countries.items():
        k_ascii = k.lower().replace("'", "").replace("’", "").replace("ô", "o").replace("é", "e")
        if k_ascii == team_ascii:
            return v
    return ""


def _resolve_venue(name: str) -> VenueMeta | None:
    """Resolve a venue name to its VenueMeta. Handles name variants."""
    if not name:
        return None

    # Direct match
    if name in VENUE_DB:
        return VENUE_DB[name]

    # Case-insensitive
    name_lower = name.lower()
    for k, v in VENUE_DB.items():
        if k.lower() == name_lower:
            return v

    # Substring match (handle "Estadio Azteca (Estadio Banorte)" variants)
    for k, v in VENUE_DB.items():
        if k.lower() in name_lower or name_lower in k.lower():
            return v

    # Contains match
    for k, v in VENUE_DB.items():
        # Check if any word from the key appears in the name
        key_words = set(k.lower().split())
        name_words = set(name_lower.split())
        if key_words & name_words:
            return v

    return None
