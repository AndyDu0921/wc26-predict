"""Match Importance Calculator — Csató & Gyimesi (2025) six-type classification.

Quantifies how critical a match is to tournament progression, using:
1. Group standings (from GroupStandingsService)
2. Matchday detection (MD1/MD2/MD3)
3. Six-type classification: Unimportant, Offensive, Antagonistic,
   Defensive, Offensive Asymmetric, Defensive Asymmetric

Reference: Csató & Gyimesi (2025), European Journal of Operational Research
  "Before the Opening Whistle: How Mathematics Shape the Incentives
   in Group-Stage Matches in the Reformed FIFA World Cup"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from app.services.group_standings import GroupStandingsService, GroupTable

logger = logging.getLogger(__name__)


class MatchType(str, Enum):
    """Six-type classification from Csató & Gyimesi (2025)."""
    UNIMPORTANT = "unimportant"  # Both indifferent — qualification already decided
    OFFENSIVE_ASYMMETRIC = "offensive_asymmetric"  # One indifferent, other must attack
    DEFENSIVE_ASYMMETRIC = "defensive_asymmetric"  # One indifferent, other must protect draw
    OFFENSIVE = "offensive"  # Both must attack — most exciting
    ANTAGONISTIC = "antagonistic"  # One satisfied with draw, one must win
    DEFENSIVE = "defensive"  # Both satisfied with 0-0 — collusion risk


@dataclass
class MatchImportanceResult:
    """Complete match importance analysis."""
    home_team: str
    away_team: str
    matchday: int
    group_name: str
    match_type: MatchType
    # Home/away motivation strength (0-1)
    home_motivation: float
    away_motivation: float
    # EI score (0-1): how much this match affects final standings
    ei_score: float
    # Probability adjustments
    home_win_adj: float  # adjustment to home_win_prob (e.g. +0.05 = +5pp)
    draw_adj: float  # adjustment to draw_prob
    away_win_adj: float  # adjustment to away_win_prob
    # Risk flags
    collusion_risk: float  # 0-1, risk of both teams settling for draw
    rotation_risk_home: float  # 0-1, risk home team rests players
    rotation_risk_away: float  # 0-1, risk away team rests players
    # Narrative
    explanation: str


class MatchImportanceCalculator:
    """Calculate match importance and motivation factors.

    Usage::

        from app.services.group_standings import GroupStandingsService
        standings = GroupStandingsService()

        calc = MatchImportanceCalculator()
        result = calc.analyze("Scotland", "Brazil", standings)
        print(f"Match type: {result.match_type.value}")
        print(f"Home adj: {result.home_win_adj:+.1%}")
    """

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        home_team: str,
        away_team: str,
        standings_svc: GroupStandingsService | None = None,
    ) -> MatchImportanceResult:
        """Full match importance analysis.

        Args:
            home_team: Home team name
            away_team: Away team name
            standings_svc: Optional standings service (created if None)

        Returns:
            MatchImportanceResult with classification and adjustments
        """
        if standings_svc is None:
            standings_svc = GroupStandingsService()

        # Determine matchday and group
        matchday = standings_svc.get_match_matchday(home_team, away_team) or 1

        # Find group for this match
        group_name = self._find_group(standings_svc, home_team, away_team)

        # Get team statuses
        home_status = standings_svc.get_team_status(home_team)
        away_status = standings_svc.get_team_status(away_team)

        # Get group table
        if group_name:
            table = standings_svc.compute_standings(group_name)
        else:
            table = GroupTable(group_name="?", teams=[])

        # Classify match type
        match_type = self._classify(
            home_status, away_status, table, matchday, home_team, away_team
        )

        # Compute motivation strengths
        home_motivation = self._compute_motivation(home_status, match_type, is_home=True)
        away_motivation = self._compute_motivation(away_status, match_type, is_home=False)

        # Compute EI score (0-1)
        ei_score = self._compute_ei_score(home_status, away_status, table, match_type)

        # Compute collusion risk
        collusion_risk = self._compute_collusion_risk(match_type, home_status, away_status)

        # Compute rotation risks
        rotation_risk_home = self._compute_rotation_risk(home_status, matchday)
        rotation_risk_away = self._compute_rotation_risk(away_status, matchday)

        # Compute probability adjustments
        home_adj, draw_adj, away_adj = self._compute_adjustments(
            match_type, home_motivation, away_motivation, collusion_risk
        )

        # Generate explanation
        explanation = self._explain(
            match_type, home_status, away_status, home_adj, draw_adj, away_adj
        )

        return MatchImportanceResult(
            home_team=home_team,
            away_team=away_team,
            matchday=matchday,
            group_name=group_name,
            match_type=match_type,
            home_motivation=round(home_motivation, 2),
            away_motivation=round(away_motivation, 2),
            ei_score=round(ei_score, 3),
            home_win_adj=round(home_adj, 3),
            draw_adj=round(draw_adj, 3),
            away_win_adj=round(away_adj, 3),
            collusion_risk=round(collusion_risk, 2),
            rotation_risk_home=round(rotation_risk_home, 2),
            rotation_risk_away=round(rotation_risk_away, 2),
            explanation=explanation,
        )

    # ------------------------------------------------------------------
    #  Classification
    # ------------------------------------------------------------------

    @staticmethod
    def _classify(
        home_status: dict,
        away_status: dict,
        table: GroupTable,
        matchday: int,
        home_team: str,
        away_team: str,
    ) -> MatchType:
        """Classify match into one of six types.

        Logic based on Csató & Gyimesi (2025) Table 2, adapted for
        2026 format where 3rd place can also advance.
        """
        # Only MD3 matches have meaningful strategic behavior
        if matchday < 3:
            # MD1/MD2: all teams still in contention (mostly)
            # Check if either team is already eliminated after MD2
            h_eliminated = home_status.get("eliminated", False)
            a_eliminated = away_status.get("eliminated", False)
            h_qualified = home_status.get("qualified", False)
            a_qualified = away_status.get("qualified", False)

            if (h_qualified or h_eliminated) and (a_qualified or a_eliminated):
                return MatchType.UNIMPORTANT
            if h_qualified and not a_qualified:
                return MatchType.OFFENSIVE_ASYMMETRIC
            if a_qualified and not h_qualified:
                return MatchType.OFFENSIVE_ASYMMETRIC
            # Default for MD1/MD2: both teams have something to play for
            return MatchType.OFFENSIVE

        # ── MD3: Third round strategic analysis ──
        h_qualified = home_status.get("qualified", False)
        a_qualified = away_status.get("qualified", False)
        h_eliminated = home_status.get("eliminated", False)
        a_eliminated = away_status.get("eliminated", False)
        h_pos = home_status.get("position", 3)
        a_pos = away_status.get("position", 3)

        # Case 1: Both already qualified → Unimportant (dead rubber)
        if h_qualified and a_qualified:
            return MatchType.UNIMPORTANT

        # Case 2: Both eliminated → Unimportant
        if h_eliminated and a_eliminated:
            return MatchType.UNIMPORTANT

        # Case 3: One qualified, one fighting
        if h_qualified and not a_qualified:
            if a_eliminated:
                return MatchType.UNIMPORTANT
            # Away team needs a result
            if a_pos == 3:
                # Away team is 3rd — needs to climb
                return MatchType.OFFENSIVE_ASYMMETRIC
            return MatchType.OFFENSIVE_ASYMMETRIC

        if a_qualified and not h_qualified:
            if h_eliminated:
                return MatchType.UNIMPORTANT
            if h_pos == 3:
                return MatchType.OFFENSIVE_ASYMMETRIC
            return MatchType.OFFENSIVE_ASYMMETRIC

        # Case 4: One eliminated, one fighting
        if h_eliminated and not a_eliminated:
            if a_qualified:
                return MatchType.UNIMPORTANT
            return MatchType.OFFENSIVE_ASYMMETRIC

        if a_eliminated and not h_eliminated:
            if h_qualified:
                return MatchType.UNIMPORTANT
            return MatchType.OFFENSIVE_ASYMMETRIC

        # Case 5: Neither qualified nor eliminated — both fighting
        # Classification based on Csató & Gyimesi (2025) six-type taxonomy.
        # Priority order: DEFENSIVE (collusion risk) > DEFENSIVE_ASYMMETRIC >
        #   ANTAGONISTIC > OFFENSIVE.  Each type requires specific conditions;
        #   fall through to the most general type when conditions don't match.

        h_pts = home_status.get("points", 0)
        a_pts = away_status.get("points", 0)

        # ── DEFENSIVE: a draw satisfies both teams ──
        # Condition: both in top-2 positions and a draw would secure
        # qualification for both (or at least protect current standing).
        # Collusion risk marker.
        both_high = h_pos <= 2 and a_pos <= 2
        if both_high and h_pts > 0 and a_pts > 0:
            # Check if draw truly benefits both: each team's current position
            # would be maintained or improved by a shared point
            # If the weaker of the two has at most a 1-pt gap above 3rd,
            # a draw won't necessarily save them — they need to win.
            # If both have >= 1 pt cushion over 3rd, a draw is mutually beneficial.
            pts_above_3rd_h = h_pts - (table.teams[2].points if len(table.teams) > 2 else 0)
            pts_above_3rd_a = a_pts - (table.teams[2].points if len(table.teams) > 2 else 0)
            if pts_above_3rd_h >= 0 and pts_above_3rd_a >= 0:
                # Both at least even on points with 3rd — draw is safe harbor
                return MatchType.DEFENSIVE

        # ── DEFENSIVE_ASYMMETRIC: one team would accept a draw, the other must win ──
        # The "satisfied" team plays conservatively; the "desperate" team attacks.
        if h_pos == 1 or h_pos == 2:
            if a_pos >= 3 and a_pts < h_pts:
                # Home can accept draw (solid position), away must win
                return MatchType.DEFENSIVE_ASYMMETRIC
        if a_pos == 1 or a_pos == 2:
            if h_pos >= 3 and h_pts < a_pts:
                # Away can accept draw, home must win
                return MatchType.DEFENSIVE_ASYMMETRIC

        # ── ANTAGONISTIC: one satisfied with draw, one must win ──
        # Team in a better position can settle for draw; the other must attack.
        if h_pos == 1:
            close_gap = h_pts <= a_pts + 1
            if close_gap:
                return MatchType.ANTAGONISTIC
            return MatchType.OFFENSIVE
        if a_pos == 1:
            close_gap = a_pts <= h_pts + 1
            if close_gap:
                return MatchType.ANTAGONISTIC
            return MatchType.OFFENSIVE

        # ── OFFENSIVE: both must attack ──
        # Both in positions 2-4, both need a result
        return MatchType.OFFENSIVE

    # ------------------------------------------------------------------
    #  Motivation & Adjustment
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_motivation(status: dict, match_type: MatchType, is_home: bool) -> float:
        """Compute motivation strength (0-1) for a team."""
        if status.get("qualified", False):
            return 0.1  # already qualified — low motivation
        if status.get("eliminated", False):
            return 0.15  # eliminated — pride only

        position = status.get("position", 3)
        # Higher motivation when in higher position (protecting lead)
        # or in lower position (must fight)
        base = 0.5
        if position == 1:
            base = 0.7  # protecting top spot
        elif position == 2:
            base = 0.8  # fighting for qualification
        elif position == 3:
            base = 0.7  # fighting for top-2 or 3rd-place slot
        else:
            base = 0.6  # last place, need miracle

        # Adjust based on match type
        if match_type == MatchType.OFFENSIVE:
            base = min(1.0, base + 0.15)  # both must attack → high motivation
        elif match_type == MatchType.ANTAGONISTIC:
            base = base + 0.1  # higher stakes
        elif match_type == MatchType.DEFENSIVE:
            base = max(0.3, base - 0.2)  # both happy with draw → lower motivation to attack

        return min(1.0, max(0.0, base))

    @staticmethod
    def _compute_ei_score(
        home_status: dict,
        away_status: dict,
        table: GroupTable,
        match_type: MatchType,
    ) -> float:
        """Compute Event Importance score (0-1).

        Simplified version: based on positional volatility.
        Full EI would require Monte Carlo simulation of remaining matches.
        """
        if match_type == MatchType.UNIMPORTANT:
            return 0.05  # nearly zero importance

        if match_type == MatchType.DEFENSIVE:
            return 0.6  # important because of collusion risk

        if match_type == MatchType.OFFENSIVE:
            return 0.85  # highest importance

        if match_type == MatchType.ANTAGONISTIC:
            return 0.75

        # Asymmetric: moderate importance
        return 0.5

    @staticmethod
    def _compute_collusion_risk(
        match_type: MatchType, home_status: dict, away_status: dict
    ) -> float:
        """Compute collusion risk (0-1).

        Collusion risk: both teams benefit from a draw.
        Named after the 1982 "Disgrace of Gijón" (West Germany-Austria).
        """
        if match_type != MatchType.DEFENSIVE:
            return 0.0

        # Both teams not yet qualified, but a draw helps both
        h_points = home_status.get("points", 0)
        a_points = away_status.get("points", 0)
        h_pos = home_status.get("position", 3)
        a_pos = away_status.get("position", 3)

        # High risk: both in top-2 positions
        if h_pos in (1, 2) and a_pos in (1, 2):
            return 0.8

        # Medium risk: both on positive points
        if h_points >= 4 and a_points >= 4:
            return 0.6

        return 0.3

    @staticmethod
    def _compute_rotation_risk(status: dict, matchday: int) -> float:
        """Compute rotation risk: how likely a team rests starters.

        Based on Csató & Gyimesi finding: 91% probability top teams
        rest players when already qualified for dead rubber matches.
        """
        if matchday < 3:
            return 0.0  # no rotation in MD1/MD2

        if status.get("qualified", False):
            return 0.85  # very likely to rotate

        if status.get("eliminated", False):
            return 0.4  # may give bench players experience

        # Fighting for qualification — no rotation
        return 0.05

    # ------------------------------------------------------------------
    #  Adjustment calculation
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_adjustments(
        match_type: MatchType,
        home_motivation: float,
        away_motivation: float,
        collusion_risk: float,
    ) -> tuple[float, float, float]:
        """Compute probability adjustments based on match context.

        Returns (home_adj, draw_adj, away_adj) in probability point units.
        Positive = increase that outcome's probability, negative = decrease.

        Adjustment magnitudes are conservative (max ±8pp) based on
        research findings. Larger adjustments only for extreme cases
        (Defensive type with collusion risk).
        """
        home_adj = 0.0
        draw_adj = 0.0
        away_adj = 0.0

        # Motivation differential drives win probability shifts
        motivation_diff = home_motivation - away_motivation

        if abs(motivation_diff) > 0.3:
            # Significant motivation gap → shift win probabilities
            shift = motivation_diff * 0.10  # max ±0.10 = ±10pp
            shift = max(-0.08, min(0.08, shift))
            home_adj += shift
            away_adj -= shift * 0.7  # don't fully reverse — draw probability also affected
            draw_adj -= shift * 0.3

        # Match type specific adjustments
        if match_type == MatchType.UNIMPORTANT:
            # Reduce favorite's win probability (uncertainty increases)
            if home_motivation < 0.3:
                home_adj -= 0.05  # home team unmotivated
            if away_motivation < 0.3:
                away_adj -= 0.05
            draw_adj += abs(home_adj) * 0.3 + abs(away_adj) * 0.3

        elif match_type == MatchType.OFFENSIVE:
            # Both must attack → goals more likely, draw less likely
            draw_adj -= 0.02
            # Motivated team gets a small boost
            home_adj += home_motivation * 0.03
            away_adj += away_motivation * 0.03

        elif match_type == MatchType.ANTAGONISTIC:
            # Attacking team (higher motivation) gets boost
            if home_motivation > away_motivation:
                home_adj += 0.04
                draw_adj -= 0.01
            else:
                away_adj += 0.04
                draw_adj -= 0.01

        elif match_type == MatchType.DEFENSIVE:
            # Both satisfied with draw → DRAW MORE LIKELY
            draw_adj += 0.06
            home_adj -= 0.03
            away_adj -= 0.03

            # Collusion amplification
            if collusion_risk > 0.7:
                draw_adj += 0.06  # total +12pp for draw
                home_adj -= 0.03
                away_adj -= 0.03

        elif match_type == MatchType.OFFENSIVE_ASYMMETRIC:
            # Motivated team gets stronger boost
            if home_motivation > away_motivation:
                home_adj += 0.06
                draw_adj -= 0.03
                away_adj -= 0.03
            else:
                away_adj += 0.06
                draw_adj -= 0.03
                home_adj -= 0.03

        elif match_type == MatchType.DEFENSIVE_ASYMMETRIC:
            # One team wants draw, other indifferent
            draw_adj += 0.03
            if home_motivation < away_motivation:
                away_adj += 0.02
            else:
                home_adj += 0.02

        # Normalize adjustments to zero-sum and clamp to ±8pp
        total = home_adj + draw_adj + away_adj
        if total != 0:
            correction = total / 3.0
            home_adj -= correction
            draw_adj -= correction
            away_adj -= correction

        # Safety clamp: no single adjustment exceeds ±8pp
        home_adj = max(-0.08, min(0.08, home_adj))
        draw_adj = max(-0.08, min(0.08, draw_adj))
        away_adj = max(-0.08, min(0.08, away_adj))

        # Re-normalize after clamp
        total = home_adj + draw_adj + away_adj
        if total != 0:
            correction = total / 3.0
            home_adj -= correction
            draw_adj -= correction
            away_adj -= correction

        return (
            round(home_adj, 4),
            round(draw_adj, 4),
            round(away_adj, 4),
        )

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_group(
        standings_svc: GroupStandingsService, home_team: str, away_team: str
    ) -> str:
        """Find the group name for a match."""
        import sqlite3
        db_path = standings_svc._db_path
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT group_name FROM wc26_schedule
            WHERE home_team = ? AND away_team = ?
            LIMIT 1
        """, (home_team, away_team))
        row = cur.fetchone()
        conn.close()
        return row[0] if row else "?"

    @staticmethod
    def _explain(
        match_type: MatchType,
        home_status: dict,
        away_status: dict,
        home_adj: float,
        draw_adj: float,
        away_adj: float,
    ) -> str:
        """Generate a human-readable explanation of the match context."""
        type_descriptions = {
            MatchType.UNIMPORTANT: "Both teams have little at stake — qualification already decided",
            MatchType.OFFENSIVE: "Both teams must attack — high stakes for both",
            MatchType.ANTAGONISTIC: "One team satisfied with draw, the other must win",
            MatchType.DEFENSIVE: "⚠️ Both teams benefit from a draw — collusion risk",
            MatchType.OFFENSIVE_ASYMMETRIC: "One team with much to gain/lose, the other indifferent",
            MatchType.DEFENSIVE_ASYMMETRIC: "One team protecting a draw, the other with less at stake",
        }

        desc = type_descriptions.get(match_type, "Standard match")
        adj_desc = ""
        if abs(home_adj) > 0.01 or abs(draw_adj) > 0.01:
            adj_desc = (
                f" Adj: home{home_adj:+.1%} draw{draw_adj:+.1%} away{away_adj:+.1%}"
            )

        return f"[{match_type.value}] {desc}.{adj_desc}"
