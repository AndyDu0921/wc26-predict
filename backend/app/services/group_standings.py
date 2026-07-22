"""Group Standings Service — real-time World Cup group table calculation.

Computes group standings from wc26_schedule match results, ranks teams
within groups, and calculates third-place qualification ranking.

Design principles:
- Pure functions: no DB writes, only reads from wc26_schedule
- Deterministic tie-breaking matches FIFA 2026 rules
- Used by Match Importance calculator for EI scoring
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.sqlite_paths import current_sync_sqlite_path

logger = logging.getLogger(__name__)

# FIFA 2026 tiebreaker order
# 1. Points
# 2. Goal difference
# 3. Goals scored
# 4. Head-to-head points (not yet implemented — needs match-level H2H lookup)
# 5. Fair play score (yellow/red cards — data unavailable)
# 6. FIFA World Ranking (available via Elo ratings)


@dataclass
class TeamStanding:
    """A team's position in its group."""
    team_name: str
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    goals_for: int = 0
    goals_against: int = 0
    goal_diff: int = 0
    points: int = 0
    group_name: str = ""


@dataclass
class GroupTable:
    """Complete standings for one group."""
    group_name: str
    teams: list[TeamStanding] = field(default_factory=list)
    # Qualification status per team
    qualified: set[str] = field(default_factory=set)  # already qualified (top-2 locked)
    eliminated: set[str] = field(default_factory=set)  # mathematically eliminated


class GroupStandingsService:
    """Read wc26_schedule and compute live group standings.

    Usage::

        svc = GroupStandingsService()
        table = svc.compute_standings("A")
        print(f"Group A leader: {table.teams[0].team_name}")
        third_place = svc.compute_third_place_ranking()
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = current_sync_sqlite_path()
        self._db_path = str(Path(db_path).expanduser().resolve())

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------

    def compute_standings(self, group_name: str) -> GroupTable:
        """Compute current standings for a single group."""
        matches = self._get_finished_matches(group_name)
        table = self._build_table(group_name, matches)
        self._sort_table(table)
        self._classify_qualification(table)
        return table

    def compute_all_standings(self) -> dict[str, GroupTable]:
        """Compute standings for all 12 groups."""
        groups = {}
        for grp in [chr(ord("A") + i) for i in range(12)]:  # A-L
            groups[grp] = self.compute_standings(grp)
        return groups

    def compute_third_place_ranking(self) -> list[TeamStanding]:
        """Rank the 12 third-place teams.

        Returns list sorted by qualification criteria (best first).
        Top 8 advance to Round of 32.
        """
        all_tables = self.compute_all_standings()
        third_place_teams = []
        for grp, table in all_tables.items():
            if len(table.teams) >= 3:
                third_place_teams.append(table.teams[2])  # index 2 = 3rd place

        # Sort by: points DESC, goal_diff DESC, goals_for DESC
        third_place_teams.sort(
            key=lambda t: (t.points, t.goal_diff, t.goals_for),
            reverse=True,
        )
        return third_place_teams

    def get_team_status(self, team_name: str) -> dict[str, Any]:
        """Get detailed status for a single team.

        Returns:
            dict with keys: group, position, points, played, goal_diff,
            qualified (bool), eliminated (bool), scenarios (list[str])
        """
        all_tables = self.compute_all_standings()
        for grp, table in all_tables.items():
            for i, team in enumerate(table.teams):
                if team.team_name.lower() == team_name.lower():
                    scenarios = self._compute_scenarios(team, table, i)
                    return {
                        "group": grp,
                        "position": i + 1,
                        "points": team.points,
                        "played": team.played,
                        "goal_diff": team.goal_diff,
                        "goals_for": team.goals_for,
                        "qualified": team.team_name in table.qualified,
                        "eliminated": team.team_name in table.eliminated,
                        "scenarios": scenarios,
                    }
        return {"group": "?", "position": 0, "scenarios": ["team not found"]}

    def get_match_matchday(self, home_team: str, away_team: str) -> int | None:
        """Determine which matchday (1/2/3) a match belongs to.

        Looks up how many prior matches each team has played in their group.
        """
        import sqlite3
        conn = sqlite3.connect(self._db_path)
        cur = conn.cursor()

        # Find the group for this match
        cur.execute("""
            SELECT group_name FROM wc26_schedule
            WHERE home_team = ? AND away_team = ?
            LIMIT 1
        """, (home_team, away_team))
        row = cur.fetchone()
        if not row:
            conn.close()
            return None
        group = row[0]

        # Count how many finished matches each team has in this group before this match
        cur.execute("""
            SELECT match_date FROM wc26_schedule
            WHERE home_team = ? AND away_team = ?
              AND group_name = ?
            LIMIT 1
        """, (home_team, away_team, group))
        match_date_row = cur.fetchone()
        match_date = match_date_row[0] if match_date_row else None

        if not match_date:
            conn.close()
            return None

        # Count finished matches for either team in this group
        cur.execute("""
            SELECT COUNT(*) FROM wc26_schedule
            WHERE group_name = ?
              AND match_status = 'FINISHED'
              AND (home_team = ? OR away_team = ?)
              AND match_date < ?
        """, (group, home_team, home_team, match_date))
        home_played = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM wc26_schedule
            WHERE group_name = ?
              AND match_status = 'FINISHED'
              AND (home_team = ? OR away_team = ?)
              AND match_date < ?
        """, (group, away_team, away_team, match_date))
        away_played = cur.fetchone()[0]

        conn.close()

        # Matchday = max(prior_matches) + 1
        matchday = max(home_played, away_played) + 1
        return matchday if matchday <= 3 else None

    # ------------------------------------------------------------------
    #  Internals
    # ------------------------------------------------------------------

    def _get_finished_matches(self, group_name: str) -> list[dict]:
        """Get all finished matches for a group."""
        import sqlite3
        conn = sqlite3.connect(self._db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT home_team, away_team, home_goals, away_goals, match_date
            FROM wc26_schedule
            WHERE group_name = ? AND match_status = 'FINISHED'
              AND home_goals IS NOT NULL AND away_goals IS NOT NULL
            ORDER BY match_date
        """, (group_name,))
        rows = cur.fetchall()
        conn.close()
        return [
            {"home": r[0], "away": r[1], "hg": r[2], "ag": r[3], "date": r[4]}
            for r in rows
        ]

    @staticmethod
    def _build_table(group_name: str, matches: list[dict]) -> GroupTable:
        """Build an unsorted GroupTable from match results."""
        teams: dict[str, TeamStanding] = {}

        for m in matches:
            home = m["home"]
            away = m["away"]
            hg = m["hg"]
            ag = m["ag"]

            if home not in teams:
                teams[home] = TeamStanding(team_name=home, group_name=group_name)
            if away not in teams:
                teams[away] = TeamStanding(team_name=away, group_name=group_name)

            home_s = teams[home]
            away_s = teams[away]

            home_s.played += 1
            away_s.played += 1
            home_s.goals_for += hg
            home_s.goals_against += ag
            away_s.goals_for += ag
            away_s.goals_against += hg
            home_s.goal_diff = home_s.goals_for - home_s.goals_against
            away_s.goal_diff = away_s.goals_for - away_s.goals_against

            if hg > ag:
                home_s.won += 1
                home_s.points += 3
                away_s.lost += 1
            elif hg == ag:
                home_s.drawn += 1
                away_s.drawn += 1
                home_s.points += 1
                away_s.points += 1
            else:
                away_s.won += 1
                away_s.points += 3
                home_s.lost += 1

        return GroupTable(group_name=group_name, teams=list(teams.values()))

    @staticmethod
    def _sort_table(table: GroupTable) -> None:
        """Sort teams by FIFA tiebreaker order: points, GD, GF."""
        table.teams.sort(
            key=lambda t: (t.points, t.goal_diff, t.goals_for),
            reverse=True,
        )

    @staticmethod
    def _classify_qualification(table: GroupTable) -> None:
        """Classify teams as qualified or eliminated.

        Simple heuristic based on points gap after 2 matches:
        - If a team has 6 points and 4th place has 0, top team is qualified
        - If a team has 0 points and 1st place has 6, bottom team is eliminated
        """
        teams = table.teams
        if len(teams) < 4:
            return

        # After 2 matches played: max possible remaining = 3
        max_remaining = 3  # one match remaining for each team

        top = teams[0]
        bottom = teams[3]

        # Top team qualified if they have >= 4 point lead over 3rd place
        # (3rd place could max get 3 more points)
        third = teams[2]
        if top.points > third.points + max_remaining:
            table.qualified.add(top.team_name)
        # Also check 2nd place
        second = teams[1]
        if second.points > third.points + max_remaining:
            table.qualified.add(second.team_name)

        # Bottom team eliminated if points gap to 2nd > 3
        if bottom.points + max_remaining < second.points:
            table.eliminated.add(bottom.team_name)
        # Also check 3rd place
        if third.points + max_remaining < second.points:
            table.eliminated.add(third.team_name)

    @staticmethod
    def _compute_scenarios(team: TeamStanding, table: GroupTable, position: int) -> list[str]:
        """Generate simple qualification scenarios for a team."""
        scenarios = []
        teams = table.teams

        if len(teams) < 4:
            return scenarios

        if team.team_name in table.qualified:
            scenarios.append("✅ Already qualified for Round of 32")
            if position == 0:
                scenarios.append("   Secured group winner position")
            return scenarios

        if team.team_name in table.eliminated:
            scenarios.append("❌ Mathematically eliminated")
            return scenarios

        # Still in contention — analyze what's needed
        if position == 0:
            scenarios.append("🏆 Group leader — needs 1 point to secure")
        elif position == 1:
            gap_to_third = team.points - teams[2].points
            if gap_to_third >= 1:
                scenarios.append(f"✅ Draw secures top-2 (currently +{gap_to_third}pts over 3rd)")
            else:
                scenarios.append("⚔️ Must match or beat 3rd place result in final round")
        elif position == 2:
            gap = teams[1].points - team.points
            if gap <= 0:
                scenarios.append("⚔️ Win jumps to 2nd place")
            scenarios.append("⚠️ Fighting for top-2 or 3rd-place qualification")
        else:  # position 3
            gap = teams[2].points - team.points
            if gap <= 1:
                scenarios.append(f"🆙 Need win to overtake 3rd place ({gap}pt gap)")
            else:
                scenarios.append("❌ Need multiple results to go their way")

        return scenarios
