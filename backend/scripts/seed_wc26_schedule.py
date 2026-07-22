"""Seed the WC26 tournament schedule into the SQLite database.

Populates:
  - wc26_groups:            12 groups (A-L), 4 slots each (48 entries)
  - wc26_schedule:          104 matches (72 group + 32 knockout)
  - wc26_group_standings:   48 standing rows initialized to zero
  - wc26_knockout_paths:    Advancement links for all 32 knockout matches
  - wc26_third_place_ranking: Placeholder rows for all 12 groups

Idempotent -- deletes any existing wc26_* data before inserting.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BACKEND_DIR / "data" / "local_stage2.db"

# ── Constants ───────────────────────────────────────────────────────────

GROUPS = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L"]

GROUP_MATCHUPS = [
    # (home_slot, away_slot) within a group
    (1, 2),  # Matchday 1
    (3, 4),  # Matchday 1
    (1, 3),  # Matchday 2
    (2, 4),  # Matchday 2
    (1, 4),  # Matchday 3
    (2, 3),  # Matchday 3
]

# Matchday labels for each matchup index
MATCHDAY_LABEL = ["MD1", "MD1", "MD2", "MD2", "MD3", "MD3"]

# Group match dates: 72 matches over 12 days, 6 per day
# MD1 (matches per group 1-2): June 11-14
# MD2 (matches per group 3-4): June 15-18
# MD3 (matches per group 5-6): June 19-22
# Groups are assigned in pairs of 3 per day
MATCHDAY_DATE_MAP: dict[str, dict[str, str]] = {
    # MD1: June 11-14
    "MD1": {"A": "2026-06-11", "B": "2026-06-11", "C": "2026-06-11",
            "D": "2026-06-12", "E": "2026-06-12", "F": "2026-06-12",
            "G": "2026-06-13", "H": "2026-06-13", "I": "2026-06-13",
            "J": "2026-06-14", "K": "2026-06-14", "L": "2026-06-14"},
    # MD2: June 15-18
    "MD2": {"A": "2026-06-15", "B": "2026-06-15", "C": "2026-06-15",
            "D": "2026-06-16", "E": "2026-06-16", "F": "2026-06-16",
            "G": "2026-06-17", "H": "2026-06-17", "I": "2026-06-17",
            "J": "2026-06-18", "K": "2026-06-18", "L": "2026-06-18"},
    # MD3: June 19-22
    "MD3": {"A": "2026-06-19", "B": "2026-06-19", "C": "2026-06-19",
            "D": "2026-06-20", "E": "2026-06-20", "F": "2026-06-20",
            "G": "2026-06-21", "H": "2026-06-21", "I": "2026-06-21",
            "J": "2026-06-22", "K": "2026-06-22", "L": "2026-06-22"},
}

# Kickoff times to cycle through during group stage
KICKOFF_TIMES = ["12:00", "15:00", "18:00", "21:00", "13:00", "16:00"]

# Venues (12 host stadiums across USA, Canada, Mexico)
VENUES = [
    ("MetLife Stadium", "East Rutherford, NJ"),
    ("SoFi Stadium", "Inglewood, CA"),
    ("AT&T Stadium", "Arlington, TX"),
    ("Mercedes-Benz Stadium", "Atlanta, GA"),
    ("NRG Stadium", "Houston, TX"),
    ("BC Place", "Vancouver, BC"),
    ("Estadio Akron", "Guadalajara, JAL"),
    ("Estadio Azteca", "Mexico City, MX"),
    ("Levi's Stadium", "Santa Clara, CA"),
    ("Gillette Stadium", "Foxborough, MA"),
    ("Lumen Field", "Seattle, WA"),
    ("Hard Rock Stadium", "Miami Gardens, FL"),
]

# ── Round of 32 bracket definition ─────────────────────────────────
# Notation:
#   W_X    = winner of group X
#   RU_X   = runner-up of group X
#   3rd_N  = Nth best 3rd-place team (N=1..8)
#
# The bracket pairs:
#   - 8 group winners (A-H) vs 8 best 3rd-place teams
#   - 4 group winners (I-L) vs 4 runners-up from different groups
#   - 8 runners-up vs each other (4 matches)
R32_MATCHES: list[tuple[int, str, str]] = [
    # 8 matches: group winners vs best 3rd-place teams
    (73, "W_A", "3rd_1"),
    (74, "W_B", "3rd_2"),
    (75, "W_C", "3rd_3"),
    (76, "W_D", "3rd_4"),
    (77, "W_E", "3rd_5"),
    (78, "W_F", "3rd_6"),
    (79, "W_G", "3rd_7"),
    (80, "W_H", "3rd_8"),
    # 4 matches: remaining group winners vs runners-up
    (81, "W_I", "RU_J"),
    (82, "W_J", "RU_I"),
    (83, "W_K", "RU_L"),
    (84, "W_L", "RU_K"),
    # 4 matches: remaining runners-up vs each other
    (85, "RU_A", "RU_C"),
    (86, "RU_B", "RU_D"),
    (87, "RU_E", "RU_G"),
    (88, "RU_F", "RU_H"),
]

# Round of 16: winners of R32 matches pair up
R16_MATCHES: list[tuple[int, str, str]] = [
    (89, "W73", "W74"),
    (90, "W75", "W76"),
    (91, "W77", "W78"),
    (92, "W79", "W80"),
    (93, "W81", "W85"),
    (94, "W82", "W86"),
    (95, "W83", "W87"),
    (96, "W84", "W88"),
]

# Quarter-finals
QF_MATCHES: list[tuple[int, str, str]] = [
    (97, "W89", "W90"),
    (98, "W91", "W92"),
    (99, "W93", "W94"),
    (100, "W95", "W96"),
]

# Semi-finals
SF_MATCHES: list[tuple[int, str, str]] = [
    (101, "W97", "W98"),
    (102, "W99", "W100"),
]

# Third Place
TP_MATCHES: list[tuple[int, str, str]] = [
    (103, "L101", "L102"),
]

# Final
FINAL_MATCHES: list[tuple[int, str, str]] = [
    (104, "W101", "W102"),
]

# Knockout dates (reasonable estimates based on published schedule)
KNOCKOUT_DATES = {
    "Round of 32": {
        73: "2026-06-27", 74: "2026-06-27", 75: "2026-06-27", 76: "2026-06-27",
        77: "2026-06-28", 78: "2026-06-28", 79: "2026-06-28", 80: "2026-06-28",
        81: "2026-06-29", 82: "2026-06-29", 83: "2026-06-29", 84: "2026-06-29",
        85: "2026-06-30", 86: "2026-06-30", 87: "2026-06-30", 88: "2026-06-30",
    },
    "Round of 16": {
        89: "2026-07-03", 90: "2026-07-03",
        91: "2026-07-04", 92: "2026-07-04",
        93: "2026-07-05", 94: "2026-07-05",
        95: "2026-07-06", 96: "2026-07-06",
    },
    "Quarter-final": {
        97: "2026-07-09", 98: "2026-07-09",
        99: "2026-07-10", 100: "2026-07-10",
    },
    "Semi-final": {
        101: "2026-07-14", 102: "2026-07-15",
    },
    "Third Place": {103: "2026-07-18"},
    "Final": {104: "2026-07-19"},
}

KNOCKOUT_TIMES = {
    "Round of 32": "17:00",
    "Round of 16": "17:00",
    "Quarter-final": "19:00",
    "Semi-final": "20:00",
    "Third Place": "17:00",
    "Final": "19:00",
}

# Knockout venue rotation (16 host venues/rotations for 32 matches)
KNOCKOUT_VENUES: dict[str, list[tuple[str, str]]] = {
    "Round of 32": [
        ("Estadio Azteca", "Mexico City, MX"),
        ("SoFi Stadium", "Inglewood, CA"),
        ("AT&T Stadium", "Arlington, TX"),
        ("Mercedes-Benz Stadium", "Atlanta, GA"),
        ("NRG Stadium", "Houston, TX"),
        ("BC Place", "Vancouver, BC"),
        ("Estadio Akron", "Guadalajara, JAL"),
        ("MetLife Stadium", "East Rutherford, NJ"),
        ("Levi's Stadium", "Santa Clara, CA"),
        ("Gillette Stadium", "Foxborough, MA"),
        ("Lumen Field", "Seattle, WA"),
        ("Hard Rock Stadium", "Miami Gardens, FL"),
        ("Estadio Azteca", "Mexico City, MX"),
        ("SoFi Stadium", "Inglewood, CA"),
        ("AT&T Stadium", "Arlington, TX"),
        ("Mercedes-Benz Stadium", "Atlanta, GA"),
    ],
    "Round of 16": [
        ("NRG Stadium", "Houston, TX"),
        ("BC Place", "Vancouver, BC"),
        ("Estadio Akron", "Guadalajara, JAL"),
        ("MetLife Stadium", "East Rutherford, NJ"),
        ("Levi's Stadium", "Santa Clara, CA"),
        ("Gillette Stadium", "Foxborough, MA"),
        ("Lumen Field", "Seattle, WA"),
        ("Hard Rock Stadium", "Miami Gardens, FL"),
    ],
    "Quarter-final": [
        ("SoFi Stadium", "Inglewood, CA"),
        ("AT&T Stadium", "Arlington, TX"),
        ("Mercedes-Benz Stadium", "Atlanta, GA"),
        ("Estadio Azteca", "Mexico City, MX"),
    ],
    "Semi-final": [
        ("AT&T Stadium", "Arlington, TX"),
        ("Mercedes-Benz Stadium", "Atlanta, GA"),
    ],
    "Third Place": [("Hard Rock Stadium", "Miami Gardens, FL")],
    "Final": [("MetLife Stadium", "East Rutherford, NJ")],
}


# ── Bracket advancement definitions ─────────────────────────────────

def build_knockout_paths() -> list[tuple[str, int, int | None, int | None]]:
    """Return list of (round, match_number, winner_advances_to, loser_advances_to)."""
    paths: list[tuple[str, int, int | None, int | None]] = []

    # Round of 32 -> Round of 16
    for mn, _hs, _as in R32_MATCHES:
        r16_target = _r16_target_for(mn)
        paths.append(("Round of 32", mn, r16_target, None))

    # Round of 16 -> Quarter-final
    for mn, _hs, _as in R16_MATCHES:
        qf_target = _qf_target_for(mn)
        paths.append(("Round of 16", mn, qf_target, None))

    # Quarter-final -> Semi-final
    for mn, _hs, _as in QF_MATCHES:
        sf_target = _sf_target_for(mn)
        paths.append(("Quarter-final", mn, sf_target, None))

    # Semi-final -> Final (winner) / Third Place (loser)
    for mn, _hs, _as in SF_MATCHES:
        if mn == 101:
            paths.append(("Semi-final", mn, 104, 103))
        else:  # 102
            paths.append(("Semi-final", mn, 104, 103))

    # Third Place (terminal)
    paths.append(("Third Place", 103, None, None))

    # Final (terminal)
    paths.append(("Final", 104, None, None))

    return paths


def _r16_target_for(r32_match: int) -> int:
    """Map R32 match number to R16 match number."""
    if r32_match <= 80:
        # First 8 matches feed into R16 matches 89-92 in pairs
        return 89 + (r32_match - 73) // 2
    else:
        # Matches 81-88 feed into R16 matches 93-96 in defined pairs
        mapping = {81: 93, 82: 94, 83: 95, 84: 96, 85: 93, 86: 94, 87: 95, 88: 96}
        return mapping[r32_match]


def _qf_target_for(r16_match: int) -> int:
    """Map R16 match number to QF match number."""
    if r16_match <= 92:
        return 97 + (r16_match - 89) // 2
    else:
        return 99 + (r16_match - 93) // 2


def _sf_target_for(qf_match: int) -> int:
    """Map QF match number to SF match number."""
    return 101 + (qf_match - 97) // 2


# ── Seeding helpers ──────────────────────────────────────────────────

def seed_groups(cursor: sqlite3.Cursor) -> int:
    """Insert 12 groups x 4 slots. Returns row count."""
    rows = 0
    for group in GROUPS:
        for slot in range(1, 5):
            cursor.execute(
                """INSERT INTO wc26_groups (group_name, slot, qualification_status)
                   VALUES (?, ?, 'TBD')""",
                (group, slot),
            )
            rows += 1
    return rows


def seed_group_matches(cursor: sqlite3.Cursor) -> int:
    """Insert 72 group-stage matches (match_numbers 1-72). Returns row count."""
    rows = 0
    match_number = 1

    for group_idx, group in enumerate(GROUPS):
        for matchup_idx, (home_slot, away_slot) in enumerate(GROUP_MATCHUPS):
            md_label = MATCHDAY_LABEL[matchup_idx]
            match_date = MATCHDAY_DATE_MAP[md_label][group]

            # Cycle through kickoff times
            kickoff = KICKOFF_TIMES[(match_number - 1) % len(KICKOFF_TIMES)]

            # Cycle through venues
            venue, city = VENUES[(match_number - 1) % len(VENUES)]

            home_slot_str = f"{group}{home_slot}"
            away_slot_str = f"{group}{away_slot}"

            cursor.execute(
                """INSERT INTO wc26_schedule
                   (match_number, home_slot, away_slot, stage, group_name,
                    match_date, kickoff_time, venue, city, match_status)
                   VALUES (?, ?, ?, 'Group Stage', ?, ?, ?, ?, ?, 'SCHEDULED')""",
                (match_number, home_slot_str, away_slot_str, group,
                 match_date, kickoff, venue, city),
            )
            rows += 1
            match_number += 1

    return rows


def seed_knockout_matches(cursor: sqlite3.Cursor) -> int:
    """Insert 32 knockout matches (match_numbers 73-104). Returns row count."""
    rows = 0

    all_rounds: list[tuple[str, list[tuple[int, str, str]]]] = [
        ("Round of 32", R32_MATCHES),
        ("Round of 16", R16_MATCHES),
        ("Quarter-final", QF_MATCHES),
        ("Semi-final", SF_MATCHES),
        ("Third Place", TP_MATCHES),
        ("Final", FINAL_MATCHES),
    ]

    for stage_name, match_list in all_rounds:
        venues = KNOCKOUT_VENUES.get(stage_name, [])
        for i, (mn, home_slot, away_slot) in enumerate(match_list):
            match_date = KNOCKOUT_DATES.get(stage_name, {}).get(mn, "2026-07-19")
            kickoff = KNOCKOUT_TIMES.get(stage_name, "17:00")
            venue, city = venues[i] if i < len(venues) else ("TBD", "TBD")

            # group_name is NULL for knockout matches
            cursor.execute(
                """INSERT INTO wc26_schedule
                   (match_number, home_slot, away_slot, stage, group_name,
                    match_date, kickoff_time, venue, city, match_status)
                   VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, 'SCHEDULED')""",
                (mn, home_slot, away_slot, stage_name,
                 match_date, kickoff, venue, city),
            )
            rows += 1

    return rows


def seed_standings(cursor: sqlite3.Cursor) -> int:
    """Insert 48 standing rows (12 groups x 4 slots), all zeroed. Returns row count."""
    rows = 0
    for group in GROUPS:
        for slot in range(1, 5):
            team_slot = f"{group}{slot}"
            cursor.execute(
                """INSERT INTO wc26_group_standings
                   (group_name, team_slot, played, won, drawn, lost,
                    goals_for, goals_against, goal_diff, points)
                   VALUES (?, ?, 0, 0, 0, 0, 0, 0, 0, 0)""",
                (group, team_slot),
            )
            rows += 1
    return rows


def seed_third_place_ranking(cursor: sqlite3.Cursor) -> int:
    """Insert 12 third-place ranking rows (one per group). Returns row count."""
    rows = 0
    for group in GROUPS:
        team_slot = f"{group}3"
        cursor.execute(
            """INSERT INTO wc26_third_place_ranking
               (group_name, team_slot, played, points, goal_diff, goals_for, advances)
               VALUES (?, ?, 0, 0, 0, 0, 0)""",
            (group, team_slot),
        )
        rows += 1
    return rows


def seed_knockout_paths(cursor: sqlite3.Cursor) -> int:
    """Insert advancement links for all knockout matches. Returns row count."""
    rows = 0
    paths = build_knockout_paths()
    for round_name, mn, winner_to, loser_to in paths:
        cursor.execute(
            """INSERT INTO wc26_knockout_paths
               (round, match_number, winner_advances_to_match, loser_advances_to_match)
               VALUES (?, ?, ?, ?)""",
            (round_name, mn, winner_to, loser_to),
        )
        rows += 1
    return rows


def get_table_count(cursor: sqlite3.Cursor, table: str) -> int:
    cursor.execute(f"SELECT COUNT(*) FROM {table}")
    return cursor.fetchone()[0]


# ── Main ─────────────────────────────────────────────────────────────

def run() -> None:
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    # Idempotent: delete existing data (reverse dependency order)
    for table in (
        "wc26_third_place_ranking",
        "wc26_knockout_paths",
        "wc26_group_standings",
        "wc26_schedule",
        "wc26_groups",
    ):
        cursor.execute(f"DELETE FROM {table}")

    # Seed
    seed_groups(cursor)
    group_matches_count = seed_group_matches(cursor)
    knockout_matches_count = seed_knockout_matches(cursor)
    seed_standings(cursor)
    seed_third_place_ranking(cursor)
    seed_knockout_paths(cursor)

    conn.commit()

    # Verify
    total_matches = get_table_count(cursor, "wc26_schedule")
    total_groups = get_table_count(cursor, "wc26_groups")
    total_paths = get_table_count(cursor, "wc26_knockout_paths")
    total_standings = get_table_count(cursor, "wc26_group_standings")
    total_third = get_table_count(cursor, "wc26_third_place_ranking")

    conn.close()

    print(f"Created {total_groups} group slots across 12 groups")
    print(f"Created {group_matches_count} group stage matches (match 1-72)")
    print(f"Created {knockout_matches_count} knockout matches (match 73-104)")
    print(f"  Round of 32: {len(R32_MATCHES)} matches")
    print(f"  Round of 16: {len(R16_MATCHES)} matches")
    print(f"  Quarter-finals: {len(QF_MATCHES)} matches")
    print(f"  Semi-finals: {len(SF_MATCHES)} matches")
    print(f"  Third Place: {len(TP_MATCHES)} match")
    print(f"  Final: {len(FINAL_MATCHES)} match")
    print(f"Created {total_standings} group standing rows")
    print(f"Created {total_third} third-place ranking rows")
    print(f"Created {total_paths} knockout path advancement links")
    print(f"Total: {total_matches} matches ({group_matches_count} group + {knockout_matches_count} knockout)")


def main() -> None:
    print("Seeding WC26 tournament schedule...")
    print(f"Database: {DB_PATH}")
    run()


if __name__ == "__main__":
    main()
