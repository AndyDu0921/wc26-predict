#!/usr/bin/env python3
"""Inject a manual structured event into the Event Ledger.

This is the MVP path for getting real pre-match intelligence into the system
without depending on paid news APIs or unreliable RSS feeds.

Usage:
    # Player injury
    python scripts/add_manual_event.py \\
        --team "France" --player "Kylian Mbappé" \\
        --event-type INJURY --severity high --confidence 0.90 \\
        --source "L'Equipe" --source-url "https://..." \\
        --note "Ankle sprain in training, doubtful for opener"

    # Lineup confirmation
    python scripts/add_manual_event.py \\
        --team "Argentina" \\
        --event-type LINEUP_CONFIRMED --confidence 0.95 \\
        --source "AFA Official" \\
        --note "Starting XI confirmed: ..."

    # Rotation hint
    python scripts/add_manual_event.py \\
        --team "Brazil" \\
        --event-type ROTATION_HINT --severity medium --confidence 0.60 \\
        --source "Globo Esporte" \\
        --note "Coach hinted at rotating 3-4 players after short rest"

    # List recent events
    python scripts/add_manual_event.py --list

    # List events for a specific team
    python scripts/add_manual_event.py --list --team "France"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.manual_event import (
    ManualEvent, ALLOWED_EVENT_TYPES, ALLOWED_SEVERITIES,
)
from app.models.team import Team
from app.models.player import Player


def validate_args(args) -> list[str]:
    """Validate CLI arguments. Returns list of errors (empty = valid)."""
    errors = []

    if not args.list:
        if not args.team:
            errors.append("--team is required")
        if not args.event_type:
            errors.append("--event-type is required")
        elif args.event_type not in ALLOWED_EVENT_TYPES:
            errors.append(
                f"Invalid event type: {args.event_type}. "
                f"Allowed: {', '.join(sorted(ALLOWED_EVENT_TYPES))}"
            )
        if args.severity not in ALLOWED_SEVERITIES:
            errors.append(
                f"Invalid severity: {args.severity}. "
                f"Allowed: {', '.join(sorted(ALLOWED_SEVERITIES))}"
            )
        if args.source is None:
            errors.append("--source is required (no source = no trust)")
        if args.source_url is None and not args.force:
            errors.append("--source-url is required (use --force to skip)")
        if args.confidence is not None and not (0.0 <= args.confidence <= 1.0):
            errors.append("--confidence must be between 0.0 and 1.0")

    return errors


async def add_event(args) -> None:
    """Create and persist a manual event with validation."""
    errors = []

    async with AsyncSessionLocal() as db:
        # ── Validation 1: Team must exist ──
        team_result = await db.execute(
            select(Team.id, Team.name).where(Team.name == args.team)
        )
        team_row = team_result.first()
        if not team_row:
            # Try alias lookup
            alias_result = await db.execute(
                select(Team.id, Team.name)
                .select_from(Team)
                .join(Team.aliases)
                .where(Team.alias_normalized == args.team.lower().strip())
            )
            team_row = alias_result.first()
        if not team_row:
            errors.append(
                f"球队 '{args.team}' 在 teams 表中不存在。"
                f"请检查队名拼写或先用 seed_players.py 添加球队。"
            )
            team_id = None
        else:
            team_id = str(team_row[0])

        # ── Validation 2: Player must exist and belong to this team ──
        if args.player and team_id:
            player_result = await db.execute(
                select(Player.name, Player.team_id).where(Player.name == args.player)
            )
            player_rows = player_result.all()
            if not player_rows:
                errors.append(
                    f"球员 '{args.player}' 在 players 表中不存在。"
                    f"请先运行 seed_players.py 添加球员数据。"
                )
            else:
                # Check if any matching player belongs to this team
                player_on_team = any(
                    str(row[1]) == team_id for row in player_rows
                )
                if not player_on_team:
                    player_teams = [
                        str(row[1]) for row in player_rows
                    ]
                    errors.append(
                        f"球员 '{args.player}' 不属于 '{args.team}'。"
                        f"该球员的 team_id: {player_teams}"
                    )

        # ── Validation 3: Enforce expires_at ──
        if args.expires_at:
            expires_at = args.expires_at
        else:
            # Auto-expire after 7 days (default)
            from datetime import timedelta
            expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

        # ── Block or warn ──
        if errors and not args.force:
            print("❌ 验证失败，拒绝入库：")
            for e in errors:
                print(f"   - {e}")
            print()
            print("   使用 --force 可跳过验证强制入库。")
            sys.exit(1)
        elif errors:
            print("⚠️  验证警告（--force 跳过）：")
            for e in errors:
                print(f"   - {e}")

        # ── Source credibility assessment ──
        cred = _assess_credibility(args.source_url or "")
        if cred == "LOW":
            args.confidence = min(args.confidence, 0.50)
            print(f"⚠️  低可信度来源，confidence 自动下调至 {args.confidence:.0%}")
        elif cred == "UNKNOWN":
            args.confidence = min(args.confidence, 0.65)
            print(f"⚠️  未知来源，confidence 自动下调至 {args.confidence:.0%}")

        event = ManualEvent(
            team_name=args.team,
            player_name=args.player,
            event_type=args.event_type,
            severity=args.severity,
            confidence=args.confidence,
            source_name=args.source,
            source_url=args.source_url,
            note=args.note or "",
            created_by=args.created_by,
            expires_at=expires_at,
            match_id=args.match_id,
        )
        db.add(event)
        await db.commit()
        await db.refresh(event)

        print("✅ 事件已注入")
        print(f"   ID:       {event.id}")
        print(f"   类型:     {event.event_type}")
        print(f"   球队:     {event.team_name}")
        if event.player_name:
            print(f"   球员:     {event.player_name}")
        print(f"   严重度:   {event.severity}")
        print(f"   可信度:   {event.confidence:.0%}")
        print(f"   来源:     {event.source_name}")
        if event.source_url:
            print(f"   来源URL:  {event.source_url}")
        if event.note:
            print(f"   备注:     {event.note}")
        print(f"   过期时间: {expires_at}")
        if event.match_id:
            print(f"   关联比赛: {event.match_id}")
        print(f"   创建时间: {event.created_at.isoformat()}")


async def list_events(team: str | None = None, limit: int = 20) -> None:
    """List recent manual events."""
    async with AsyncSessionLocal() as db:
        query = select(ManualEvent).order_by(ManualEvent.created_at.desc()).limit(limit)
        if team:
            query = query.where(ManualEvent.team_name.ilike(f"%{team}%"))

        result = await db.execute(query)
        events = result.scalars().all()

        if not events:
            print("暂无手动事件记录。")
            print("使用示例：")
            print('  python scripts/add_manual_event.py --team "France" --player "Mbappé" --event-type INJURY --severity high --source "L\'Equipe"')
            return

        print(f"{'时间':<22} {'类型':<18} {'球队':<20} {'球员':<15} {'严重度':<8} {'可信度':<8} 备注")
        print("-" * 120)
        for e in events:
            created = e.created_at.strftime("%Y-%m-%d %H:%M") if e.created_at else "N/A"
            player = e.player_name or "—"
            note = (e.note or "")[:40]
            print(f"{created:<22} {e.event_type:<18} {e.team_name:<20} {player:<15} {e.severity:<8} {e.confidence:.0%}     {note}")


# ── Source credibility whitelist ──────────────────────────────

CREDIBLE_SOURCES = {
    "HIGH": [
        "skysports.com", "bbc.co.uk", "espn.com", "lequipe.fr",
        "arsenal.com", "psg.fr", "uefa.com", "bild.de", "guardian.com",
        "onefootball.com", "sportingnews.com", "yahoo.com/sports",
        "kicker.de", "gianlucadimarzio.com",
    ],
    "MEDIUM": [
        "goal.com", "telegraph.co.uk", "marca.com", "mirror.co.uk",
        "rotowire.com", "foxsports.com", "dailymail.com",
    ],
    "LOW": [
        "twitter.com", "reddit.com", "tiktok.com", "facebook.com",
        "instagram.com", "youtube.com",
    ],
}


def _assess_credibility(source_url: str) -> str:
    """Determine source credibility tier from URL.

    Returns: "HIGH" | "MEDIUM" | "LOW" | "UNKNOWN"
    """
    if not source_url:
        return "LOW"
    url_lower = source_url.lower()
    for domain in CREDIBLE_SOURCES["HIGH"]:
        if domain in url_lower:
            return "HIGH"
    for domain in CREDIBLE_SOURCES["MEDIUM"]:
        if domain in url_lower:
            return "MEDIUM"
    for domain in CREDIBLE_SOURCES["LOW"]:
        if domain in url_lower:
            return "LOW"
    return "UNKNOWN"


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="注入手动结构化事件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Mode
    parser.add_argument("--list", action="store_true", help="列出最近事件")

    # Event fields
    parser.add_argument("--team", type=str, help="受影响球队名称")
    parser.add_argument("--player", type=str, help="受影响球员（可选）")
    parser.add_argument("--event-type", type=str, dest="event_type",
                        choices=sorted(ALLOWED_EVENT_TYPES),
                        help="事件类型")
    parser.add_argument("--severity", type=str, default="medium",
                        choices=sorted(ALLOWED_SEVERITIES),
                        help="严重程度 (default: medium)")
    parser.add_argument("--confidence", type=float, default=0.75,
                        help="可信度 0.0–1.0 (default: 0.75)")
    parser.add_argument("--source", type=str, help="来源名称（必填）")
    parser.add_argument("--source-url", type=str, dest="source_url",
                        help="来源 URL")
    parser.add_argument("--note", type=str, help="备注说明")
    parser.add_argument("--created-by", type=str, dest="created_by", default="admin",
                        help="创建者标识 (default: admin)")
    parser.add_argument("--expires-at", type=str, dest="expires_at",
                        help="过期时间 ISO 8601 (默认 7 天后自动过期)")
    parser.add_argument("--match-id", type=str, dest="match_id",
                        help="关联比赛 ID（推荐填写，便于追溯）")
    parser.add_argument("--force", action="store_true",
                        help="跳过球队/球员验证，强制入库")

    args = parser.parse_args()

    if args.list:
        await list_events(args.team)
        return

    errors = validate_args(args)
    if errors:
        print("❌ 参数错误：")
        for e in errors:
            print(f"   - {e}")
        print()
        print("使用 --help 查看完整帮助。")
        sys.exit(1)

    await add_event(args)


if __name__ == "__main__":
    asyncio.run(main())
