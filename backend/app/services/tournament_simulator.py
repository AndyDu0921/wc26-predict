"""Monte Carlo World Cup Tournament Simulator for WC26.

Reads the WC26 schedule and match probabilities to simulate the full
tournament (104 matches) N times via Monte Carlo, producing per-team
advancement probabilities.

Usage (as a library):
    sim = TournamentSimulator(runs=10_000)
    sim.load_schedule("data/local_stage2.db")
    sim.set_match_probability("France", "Senegal", {"home_win": 0.55, "draw": 0.25, "away_win": 0.20})
    sim.run()
    logger.info(sim.summary())
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── Round-stages and how the bracket resolves ─────────────────────────

GROUPS = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L"]

# Group-stage matchups within each group (home_slot, away_slot)
GROUP_MATCHUPS = [(1, 2), (3, 4), (1, 3), (2, 4), (1, 4), (2, 3)]

# Round of 32: a list of (home_spec, away_spec) where each spec is
# either a literal team name or a bracket reference like "W_A" (group winner),
# "RU_A" (runner-up), or "3rd_N" (Nth-best third-place).
R32_SPECS: list[tuple[str, str]] = [
    ("W_A", "3rd_1"),
    ("W_B", "3rd_2"),
    ("W_C", "3rd_3"),
    ("W_D", "3rd_4"),
    ("W_E", "3rd_5"),
    ("W_F", "3rd_6"),
    ("W_G", "3rd_7"),
    ("W_H", "3rd_8"),
    ("W_I", "RU_J"),
    ("W_J", "RU_I"),
    ("W_K", "RU_L"),
    ("W_L", "RU_K"),
    ("RU_A", "RU_C"),
    ("RU_B", "RU_D"),
    ("RU_E", "RU_G"),
    ("RU_F", "RU_H"),
]

# Round of 16: R32 match indices pair up
R16_PAIRS: list[tuple[int, int]] = [(0, 1), (2, 3), (4, 5), (6, 7),
                                     (8, 9), (10, 11), (12, 13), (14, 15)]

# Quarter-finals
QF_PAIRS: list[tuple[int, int]] = [(0, 1), (2, 3), (4, 5), (6, 7)]

# Semi-finals
SF_PAIRS: list[tuple[int, int]] = [(0, 1), (2, 3)]

# ROUND_NAMES ordered for readability
ROUND_NAMES = ["Group Stage", "Round of 32", "Round of 16",
               "Quarter-final", "Semi-final", "Final"]

# Tournament stage names that correspond to each round
STAGE_TO_ROUND = {
    "Group Stage":    "group",
    "Round of 32":    "r32",
    "Round of 16":    "r16",
    "Quarter-final":  "qf",
    "Semi-final":     "sf",
    "Final":          "final",
}

# Knockout rounds without third-place
KNOCKOUT_ROUNDS = ["Round of 32", "Round of 16", "Quarter-final",
                   "Semi-final", "Final"]


# ── Public data class ─────────────────────────────────────────────────


@dataclass
class TeamProbabilities:
    """Round-advancement probabilities accumulated over N simulations."""

    group_win_prob: float = 0.0
    advance_prob: float = 0.0
    round_of_32_prob: float = 0.0
    round_of_16_prob: float = 0.0
    quarter_final_prob: float = 0.0
    semi_final_prob: float = 0.0
    final_prob: float = 0.0
    champion_prob: float = 0.0


# ── Core simulator ────────────────────────────────────────────────────


def _conditional_score_rate(w: float) -> float:
    """Research-only score rate used after an H/D/A outcome is sampled."""
    return 0.8 + 0.6 * w


class TournamentSimulator:
    """Monte Carlo World Cup tournament simulator.

    Usage:
        sim = TournamentSimulator(runs=10_000)
        sim.load_schedule("data/local_stage2.db")
        # Option A: load match probabilities from DB predictions
        # Option B: manually set per-match probabilities
        sim.set_match_probability("France", "Senegal", {"home_win": 0.55, ...})
        results = sim.run()
        logger.info(sim.summary())
    """

    def __init__(self, runs: int = 10_000, seed: Optional[int] = None) -> None:
        self.runs = runs
        self._rng = np.random.default_rng(seed)

        # Schedule data: list of dicts keyed by match_number
        self.schedule: dict[int, dict[str, Any]] = {}

        # Match probabilities are stage-aware so group evidence cannot silently
        # become a knockout forecast for the reverse fixture.
        self._probs: dict[tuple[str, str, bool], dict[str, float]] = {}
        self._probability_resolver: (
            Callable[[str, str, bool], dict[str, float]] | None
        ) = None

        # Group team assignments: group_name -> [team1, team2, team3, team4]
        self.groups: dict[str, list[str]] = {}

        # All teams discovered
        self.all_teams: set[str] = set()

        # Accumulated results
        self._results: dict[str, TeamProbabilities] = {}

    # ── Schedule loading ─────────────────────────────────────────────

    def load_schedule(self, db_path: str) -> None:
        """Load WC26 schedule and group assignments from the SQLite database."""
        path = Path(db_path)
        if not path.exists():
            raise FileNotFoundError(f"Database not found: {path}")

        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row

        # Load schedule
        cursor = conn.execute(
            "SELECT * FROM wc26_schedule ORDER BY match_number"
        )
        schedule_rows = cursor.fetchall()
        if not schedule_rows:
            conn.close()
            raise ValueError("wc26_schedule table is empty")

        self.schedule = {}
        for row in schedule_rows:
            self.schedule[row["match_number"]] = dict(row)

        # Load group assignments
        self.groups = {}
        cursor = conn.execute(
            "SELECT group_name, slot, team_name "
            "FROM wc26_groups ORDER BY group_name, slot"
        )
        group_rows = cursor.fetchall()
        for row in group_rows:
            g = row["group_name"]
            if g not in self.groups:
                self.groups[g] = [None, None, None, None]
            if row["team_name"]:
                self.groups[g][row["slot"] - 1] = row["team_name"]
                self.all_teams.add(row["team_name"])

        conn.close()

        # Validate schedule count
        if len(self.schedule) != 104:
            raise ValueError(
                f"Expected 104 matches in schedule, got {len(self.schedule)}"
            )

        # If all group slots are NULL, defer team loading to load_teams_from_json
        if self.groups and all(
            t is None for teams in self.groups.values() for t in teams
        ):
            self.groups = {}

    # ── Team loading ─────────────────────────────────────────────────

    def load_teams(self, groups: dict[str, list[str]]) -> None:
        """Load team assignments directly as a dict.

        Args:
            groups: Dict mapping group_name -> [team1, team2, team3, team4].
        """
        self.groups = {}
        for g, teams in groups.items():
            if len(teams) != 4:
                raise ValueError(
                    f"Group {g} must have exactly 4 teams, got {len(teams)}"
                )
            self.groups[g] = list(teams)
            self.all_teams.update(t for t in teams if t)

    def load_teams_from_json(self, json_path: str) -> None:
        """Load team assignments from the team_tournament_status.json file.

        Reads group_name from each team entry and reconstructs the
        12 groups x 4 teams structure.
        """
        import json
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        teams_by_group: dict[str, list[str]] = {}
        for name, info in data.get("teams", {}).items():
            raw = info.get("group_name", "")
            # Normalise "Group A" -> "A", "Group B" -> "B", etc.
            g = raw.replace("Group ", "").strip()
            if g:
                teams_by_group.setdefault(g, []).append(name)

        groups: dict[str, list[str]] = {}
        for g in GROUPS:
            if g in teams_by_group:
                groups[g] = teams_by_group[g]
                self.all_teams.update(teams_by_group[g])

        if len(groups) != 12:
            raise ValueError(
                f"Expected 12 groups from JSON, got {len(groups)}"
            )
        for g, teams in groups.items():
            if len(teams) != 4:
                raise ValueError(
                    f"Group {g} has {len(teams)} teams in JSON, expected 4"
                )

        self.groups = groups

    # ── Match probability setup ──────────────────────────────────────

    def set_match_probability(
        self,
        home: str,
        away: str,
        probs: dict[str, float],
        *,
        is_group: bool = True,
    ) -> None:
        """Set 3-way outcome probabilities for (home, away).

        Args:
            home: Home team name.
            away: Away team name.
            probs: Dict with keys 'home_win', 'draw', 'away_win' summing to 1.0.
        """
        values = [float(probs.get(k, 0.0)) for k in ("home_win", "draw", "away_win")]
        if any(not np.isfinite(value) or value < 0.0 or value > 1.0 for value in values):
            raise ValueError(f"Probabilities must be finite values in [0, 1], got {values}")
        total = sum(values)
        if not abs(total - 1.0) < 0.01:
            raise ValueError(
                f"Probabilities must sum to ~1.0, got {total:.4f}"
            )
        self._probs[(home, away, is_group)] = {
            "home_win": values[0] / total,
            "draw": values[1] / total,
            "away_win": values[2] / total,
        }

    def set_probability_resolver(
        self,
        resolver: Callable[[str, str, bool], dict[str, float]],
    ) -> None:
        """Resolve and cache previously unseen stage-specific matchups."""
        self._probability_resolver = resolver

    def load_probabilities_from_db(self, db_path: str) -> None:
        """Load pre-computed match probabilities from a predictions table.

        Expects a table 'wc26_match_predictions' with columns:
            home_team, away_team, home_win_prob, draw_prob, away_win_prob
        """
        path = Path(db_path)
        if not path.exists():
            raise FileNotFoundError(f"Database not found: {path}")

        conn = sqlite3.connect(str(path))
        cursor = conn.execute(
            "SELECT home_team, away_team, home_win_prob, draw_prob, "
            "away_win_prob FROM wc26_match_predictions "
            "WHERE home_team IS NOT NULL AND away_team IS NOT NULL"
        )
        count = 0
        for row in cursor.fetchall():
            self.set_match_probability(
                row[0], row[1],
                {"home_win": row[2], "draw": row[3], "away_win": row[4]},
            )
            count += 1
        conn.close()

        if count == 0:
            logger.info("  Warning: no predictions loaded from wc26_match_predictions")

    def load_probabilities_from_json(self, json_path: str) -> None:
        """Load match probabilities from a JSON file.

        Expected format:
            {"matches": [{"home": "...", "away": "...",
                          "home_win": 0.5, "draw": 0.25, "away_win": 0.25}, ...]}
        """
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for entry in data.get("matches", []):
            self.set_match_probability(
                entry["home"], entry["away"],
                {
                    "home_win": entry["home_win"],
                    "draw": entry["draw"],
                    "away_win": entry["away_win"],
                },
            )

    # ── Internal simulation helpers ──────────────────────────────────

    def _get_3way(self, home: str, away: str, *, is_group: bool) -> dict[str, float]:
        """Return stage-specific probabilities or fail closed."""
        key = (home, away, is_group)
        if key in self._probs:
            return self._probs[key]
        # Try reverse
        rev = (away, home, is_group)
        if rev in self._probs:
            p = self._probs[rev]
            return {
                "home_win": p["away_win"],
                "draw": p["draw"],
                "away_win": p["home_win"],
            }
        if self._probability_resolver is None:
            raise KeyError(
                f"No {'group' if is_group else 'knockout'} probability for "
                f"{home} vs {away}; placeholder probabilities are prohibited"
            )
        resolved = self._probability_resolver(home, away, is_group)
        self.set_match_probability(home, away, resolved, is_group=is_group)
        return self._probs[key]

    def _simulate_match(
        self, home: str, away: str, is_group: bool = True
    ) -> tuple[int, int]:
        """Draw a scoreline from 3-way probabilities using a simple model.

        Uses the probabilities to derive approximate Poisson XG rates,
        then samples a scoreline.

        Returns (home_goals, away_goals).
        """
        probs = self._get_3way(home, away, is_group=is_group)

        hw, dr, aw = probs["home_win"], probs["draw"], probs["away_win"]
        outcome = int(self._rng.choice(3, p=[hw, dr, aw]))
        lam = _conditional_score_rate(hw)
        mu = _conditional_score_rate(aw)
        if outcome == 1:
            goals = int(self._rng.poisson((lam + mu) / 2.0))
            return goals, goals
        if outcome == 0:
            ag = int(self._rng.poisson(mu))
            margin = 1 + int(self._rng.poisson(max(0.1, lam - mu)))
            return ag + margin, ag
        hg = int(self._rng.poisson(lam))
        margin = 1 + int(self._rng.poisson(max(0.1, mu - lam)))
        ag = hg + margin
        return int(hg), int(ag)

    def _simulate_knockout_match(
        self, home: str, away: str
    ) -> str:
        """Simulate a knockout match and return the winner.

        Draws are resolved via 'penalties' (coin flip).
        """
        hg, ag = self._simulate_match(home, away, is_group=False)
        if hg > ag:
            return home
        if ag > hg:
            return away
        # Penalties
        return home if self._rng.random() > 0.5 else away

    def _rank_group(self, teams: list[str], results: dict) -> list[str]:
        """Rank 4 teams by points, GD, GF (all group matches played)."""
        def sort_key(team: str) -> tuple[int, int, int]:
            r = results[team]
            return (r["pts"], r["gd"], r["gf"])
        return sorted(teams, key=sort_key, reverse=True)

    def _resolve_bracket_ref(
        self, spec: str, group_results: dict[str, list[str]],
        third_place_ranking: list[tuple[str, str]]
    ) -> str:
        """Resolve a bracket reference like 'W_A', 'RU_B', '3rd_1' to a team name."""
        if spec.startswith("W_"):
            g = spec[2]
            return group_results[g][0]
        if spec.startswith("RU_"):
            g = spec[3]
            return group_results[g][1]
        if spec.startswith("3rd_"):
            idx = int(spec[4]) - 1  # 1-indexed in spec
            if 0 <= idx < len(third_place_ranking):
                return third_place_ranking[idx][1]
            raise ValueError(f"Invalid third-place index: {spec}")
        # It's already a team name
        return spec

    # ── Main simulation loop ─────────────────────────────────────────

    def run(self) -> dict[str, TeamProbabilities]:
        """Run the Monte Carlo simulation.

        Returns a dict mapping team_name -> TeamProbabilities.
        """
        if not self.schedule:
            raise RuntimeError("No schedule loaded. Call load_schedule() first.")
        if not self.groups:
            raise RuntimeError(
                "No group assignments loaded. Call load_teams(), "
                "load_teams_from_json(), or ensure wc26_groups table has team names."
            )

        # Initialise accumulators
        counts: dict[str, Counter] = {
            team: Counter()
            for team in self.all_teams
        }

        logger.info(f"  Running {self.runs:,} simulations...")

        for sim_idx in range(self.runs):
            if self.runs >= 1000 and (sim_idx + 1) % max(1, self.runs // 10) == 0:
                pct = (sim_idx + 1) / self.runs * 100
                logger.info(f"    {sim_idx + 1:,}/{self.runs:,} ({pct:.0f}%)")

            # --- Group stage ---
            # Track standings within each group
            group_standings: dict[str, dict[str, dict]] = {}
            for g, teams in self.groups.items():
                group_standings[g] = {
                    t: {"pts": 0, "gd": 0, "gf": 0, "ga": 0}
                    for t in teams
                }

            for g, teams in self.groups.items():
                for home_slot, away_slot in GROUP_MATCHUPS:
                    home = teams[home_slot - 1]
                    away = teams[away_slot - 1]
                    hg, ag = self._simulate_match(home, away, is_group=True)

                    hs = group_standings[g][home]
                    hs["gf"] += hg
                    hs["ga"] += ag
                    hs["gd"] += hg - ag
                    if hg > ag:
                        hs["pts"] += 3
                    elif hg == ag:
                        hs["pts"] += 1
                    # else: 0 points

                    aws = group_standings[g][away]
                    aws["gf"] += ag
                    aws["ga"] += hg
                    aws["gd"] += ag - hg
                    if ag > hg:
                        aws["pts"] += 3
                    elif ag == hg:
                        aws["pts"] += 1

            # Rank each group
            group_rankings: dict[str, list[str]] = {}
            for g, teams in self.groups.items():
                def key_fn(t: str) -> tuple[int, int, int]:
                    st = group_standings[g][t]
                    return (st["pts"], st["gd"], st["gf"])
                group_rankings[g] = sorted(teams, key=key_fn, reverse=True)

            # Determine third-place ranking across all groups
            third_entries: list[tuple[int, int, int, str, str]] = []
            for g in GROUPS:
                third_team = group_rankings[g][2]  # 3rd place (0-indexed)
                st = group_standings[g][third_team]
                third_entries.append(
                    (st["pts"], st["gd"], st["gf"], g, third_team)
                )
            # Sort by pts, GD, GF descending; take top 8
            third_entries.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
            top_third: list[tuple[str, str]] = [
                (g, t) for _, _, _, g, t in third_entries[:8]
            ]

            # --- Knockout stage: Round of 32 ---
            r32_winners: list[str] = []
            for home_spec, away_spec in R32_SPECS:
                home = self._resolve_bracket_ref(
                    home_spec, group_rankings, top_third
                )
                away = self._resolve_bracket_ref(
                    away_spec, group_rankings, top_third
                )
                winner = self._simulate_knockout_match(home, away)
                r32_winners.append(winner)
                for team in (home, away):
                    if team in counts:
                        counts[team]["r32"] += 1

            # --- Round of 16 ---
            r16_winners: list[str] = []
            for i, j in R16_PAIRS:
                home = r32_winners[i]
                away = r32_winners[j]
                winner = self._simulate_knockout_match(home, away)
                r16_winners.append(winner)
                for team in (home, away):
                    if team in counts:
                        counts[team]["r16"] += 1

            # --- Quarter-finals ---
            qf_winners: list[str] = []
            for i, j in QF_PAIRS:
                home = r16_winners[i]
                away = r16_winners[j]
                winner = self._simulate_knockout_match(home, away)
                qf_winners.append(winner)
                for team in (home, away):
                    if team in counts:
                        counts[team]["qf"] += 1

            # --- Semi-finals ---
            sf_winners: list[str] = []
            for i, j in SF_PAIRS:
                home = qf_winners[i]
                away = qf_winners[j]
                winner = self._simulate_knockout_match(home, away)
                sf_winners.append(winner)
                for team in (home, away):
                    if team in counts:
                        counts[team]["sf"] += 1

            # --- Final ---
            finalist_1, finalist_2 = sf_winners[0], sf_winners[1]
            champion = self._simulate_knockout_match(finalist_1, finalist_2)
            for team in (finalist_1, finalist_2):
                if team in counts:
                    counts[team]["final"] += 1
            if champion in counts:
                counts[champion]["champion"] += 1

            # Track group winners and advancement (top 2 per group + best 3rd)
            for g in GROUPS:
                ranked = group_rankings[g]
                # Group winner
                if ranked[0] in counts:
                    counts[ranked[0]]["group_win"] += 1
                # Top 2 advance + 3rd if among best 8
                for pos, team in enumerate(ranked):
                    if team in counts:
                        if pos < 2:
                            counts[team]["advance"] += 1
                        elif pos == 2:
                            # 3rd place - check if in top 8 third places
                            if any(tg == g for tg, _ in top_third):
                                counts[team]["advance"] += 1

        # Normalise counts to probabilities
        results: dict[str, TeamProbabilities] = {}
        for team in self.all_teams:
            c = counts[team]
            results[team] = TeamProbabilities(
                group_win_prob=c["group_win"] / self.runs,
                advance_prob=c["advance"] / self.runs,
                round_of_32_prob=c["r32"] / self.runs,
                round_of_16_prob=c["r16"] / self.runs,
                quarter_final_prob=c["qf"] / self.runs,
                semi_final_prob=c["sf"] / self.runs,
                final_prob=c["final"] / self.runs,
                champion_prob=c["champion"] / self.runs,
            )

        self._results = results
        return results

    # ── Output ────────────────────────────────────────────────────────

    def summary(self) -> str:
        """Return a formatted summary table of probabilities."""
        if not self._results:
            return "(No results yet. Call run() first.)"

        lines: list[str] = []
        lines.append(f"{'='*80}")
        lines.append(f"  WC26 Monte Carlo Simulation ({self.runs:,} runs)")
        lines.append(f"{'='*80}")
        header = (
            f"{'Team':<22} {'GroupW':>7} {'Advnc':>7} {'R32':>7} "
            f"{'R16':>7} {'QF':>7} {'SF':>7} {'Final':>7} {'Champ':>7}"
        )
        lines.append(header)
        lines.append("-" * len(header))

        sorted_teams = sorted(
            self._results.items(),
            key=lambda x: x[1].champion_prob,
            reverse=True,
        )
        for team, tp in sorted_teams:
            lines.append(
                f"{team:<22} {tp.group_win_prob:>6.1%} {tp.advance_prob:>6.1%} "
                f"{tp.round_of_32_prob:>6.1%} {tp.round_of_16_prob:>6.1%} "
                f"{tp.quarter_final_prob:>6.1%} {tp.semi_final_prob:>6.1%} "
                f"{tp.final_prob:>6.1%} {tp.champion_prob:>6.1%}"
            )
        lines.append("-" * len(header))
        return "\n".join(lines)

    def to_dict(self) -> dict[str, dict[str, float]]:
        """Return results as a JSON-serialisable dict."""
        return {
            team: {
                "group_win_prob": tp.group_win_prob,
                "advance_prob": tp.advance_prob,
                "round_of_32_prob": tp.round_of_32_prob,
                "round_of_16_prob": tp.round_of_16_prob,
                "quarter_final_prob": tp.quarter_final_prob,
                "semi_final_prob": tp.semi_final_prob,
                "final_prob": tp.final_prob,
                "champion_prob": tp.champion_prob,
            }
            for team, tp in self._results.items()
        }

    def save_json(self, path: str) -> None:
        """Save simulation results to a JSON file."""
        output = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "n_simulations": self.runs,
            "teams": self.to_dict(),
        }
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(output, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
