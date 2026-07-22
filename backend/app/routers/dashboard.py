"""Dashboard API — local-only JSON endpoints for the operations dashboard.

Provides read-only views of predictions, data freshness, market snapshots,
news signals, learning logs, and output audit results.

All market/odds data is gated behind internal_research mode check.
"""
from __future__ import annotations


from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.database import AsyncSessionLocal

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


# ── Overview ──────────────────────────────────────────────

@router.get("/overview")
async def overview(mode: str = Query("internal_research")):
    """Dashboard overview: key metrics at a glance."""
    async with AsyncSessionLocal() as db:
        # Today's WC26 matches
        r = await db.execute(text("""
            SELECT COUNT(*) FROM matches
            WHERE competition = 'FIFA World Cup 2026'
            AND match_date > datetime('now')
            AND match_date < datetime('now', '+7 days')
        """))
        upcoming = r.scalar() or 0

        # WC26 total matches
        r = await db.execute(text(
            "SELECT COUNT(*) FROM matches WHERE competition = 'FIFA World Cup 2026'"
        ))
        wc_total = r.scalar() or 0

        # Prediction snapshots
        r = await db.execute(text("SELECT COUNT(*) FROM prediction_snapshots"))
        ps_total = r.scalar() or 0
        r = await db.execute(text(
            "SELECT COUNT(DISTINCT match_id) FROM prediction_snapshots"
        ))
        ps_unique = r.scalar() or 0
        r = await db.execute(text("SELECT MAX(generated_at) FROM prediction_snapshots"))
        ps_latest = r.scalar() or "never"

        # News signals
        r = await db.execute(text("SELECT COUNT(*) FROM news_signals"))
        ns_count = r.scalar() or 0
        r = await db.execute(text("SELECT COUNT(*) FROM news_articles WHERE is_processed = 0"))
        ns_unprocessed = r.scalar() or 0

        # Manual events
        r = await db.execute(text("SELECT COUNT(*) FROM manual_events"))
        me_count = r.scalar() or 0

        # Post-match learning
        r = await db.execute(text("SELECT COUNT(*) FROM postmatch_eval"))
        eval_count = r.scalar() or 0
        r = await db.execute(text("SELECT AVG(brier_score) FROM postmatch_eval"))
        avg_brier = r.scalar() or 0

        # Market data (internal only)
        market = {}
        if mode == "internal_research":
            r = await db.execute(text("SELECT COUNT(*) FROM market_odds"))
            market["odds_count"] = r.scalar() or 0
            r = await db.execute(text("SELECT COUNT(*) FROM market_consensus_snapshots"))
            market["consensus_count"] = r.scalar() or 0

        # Data freshness
        r = await db.execute(text("""
            SELECT MAX(m.match_date) FROM match_results mr
            JOIN matches m ON mr.match_id = REPLACE(m.id, '-', '')
        """))
        latest_result = r.scalar() or "unknown"

        # Alerts
        alerts = []
        if ns_count == 0:
            alerts.append({"level": "critical", "msg": "news_signals = 0 — 情报管线为空"})
        if me_count < 20:
            alerts.append({"level": "warning", "msg": f"manual_events 仅 {me_count} 条 — 人手情报稀疏"})
        if eval_count < 50:
            alerts.append({"level": "warning", "msg": f"赛后评估仅 {eval_count} 场 — 学习回路数据不足"})

        return {
            "match": {"wc_total": wc_total, "upcoming_7d": upcoming},
            "predictions": {"total": ps_total, "unique_matches": ps_unique, "latest": ps_latest},
            "signals": {"news_signals": ns_count, "unprocessed_articles": ns_unprocessed},
            "manual_events": me_count,
            "learning": {"evaluations": eval_count, "avg_brier": round(avg_brier, 4)},
            "data": {"latest_result": latest_result},
            "market": market,
            "alerts": alerts,
        }


# ── Matches ───────────────────────────────────────────────

@router.get("/matches")
async def matches(mode: str = Query("internal_research")):
    """WC26 group stage matches with prediction status."""
    async with AsyncSessionLocal() as db:
        r = await db.execute(text("""
            SELECT m.match_date, ht.name, at.name, m.stage,
                   CASE WHEN ps.id IS NOT NULL THEN 1 ELSE 0 END as has_prediction
            FROM matches m
            JOIN teams ht ON m.home_team_id = ht.id
            JOIN teams at ON m.away_team_id = at.id
            LEFT JOIN prediction_snapshots ps ON ps.match_id = REPLACE(m.id, '-', '')
            WHERE m.competition = 'FIFA World Cup 2026'
            AND m.stage LIKE 'Group%'
            ORDER BY m.match_date
        """))
        rows = r.fetchall()
        matches_list = []
        for row in rows:
            m = {
                "date": str(row[0])[:10],
                "home": row[1],
                "away": row[2],
                "stage": row[3],
                "has_prediction": bool(row[4]),
            }

            # Add prediction details if internal
            if mode == "internal_research" and m["has_prediction"]:
                r2 = await db.execute(text("""
                    SELECT json_extract(baseline_probs, '$.home_win_prob'),
                           json_extract(baseline_probs, '$.draw_prob'),
                           json_extract(baseline_probs, '$.away_win_prob'),
                           json_extract(expected_goals, '$.home_xg'),
                           json_extract(expected_goals, '$.away_xg'),
                           confidence
                    FROM prediction_snapshots ps
                    WHERE ps.match_id = (
                        SELECT REPLACE(m2.id, '-', '') FROM matches m2
                        JOIN teams ht2 ON m2.home_team_id = ht2.id
                        JOIN teams at2 ON m2.away_team_id = at2.id
                        WHERE ht2.name = :home AND at2.name = :away
                        AND m2.competition = 'FIFA World Cup 2026'
                        LIMIT 1
                    )
                    ORDER BY ps.generated_at DESC LIMIT 1
                """), {"home": row[1], "away": row[2]})
                pred_rows = r2.fetchall()
                if pred_rows:
                    p = pred_rows[0]
                    m["prediction"] = {
                        "home": round(float(p[0] or 0), 3),
                        "draw": round(float(p[1] or 0), 3),
                        "away": round(float(p[2] or 0), 3),
                        "confidence": p[5] or "unknown",
                    }

            matches_list.append(m)

        return {"matches": matches_list, "total": len(matches_list)}


# ── Data Freshness ────────────────────────────────────────

@router.get("/freshness")
async def freshness(mode: str = Query("internal_research")):
    """Data freshness dashboard."""
    async with AsyncSessionLocal() as db:
        # Last 7 days of predictions
        r = await db.execute(text("""
            SELECT date(generated_at), COUNT(*)
            FROM prediction_snapshots
            WHERE generated_at > datetime('now', '-7 days')
            GROUP BY date(generated_at)
            ORDER BY date(generated_at) DESC
        """))
        daily = [{"date": row[0], "count": row[1]} for row in r.fetchall()]

        # Ingestion runs
        r = await db.execute(text("""
            SELECT pipeline, status, started_at FROM ingest_runs
            ORDER BY started_at DESC LIMIT 5
        """))
        ingest = [{"source": row[0], "status": row[1], "at": str(row[2])} for row in r.fetchall()]

        return {"daily_predictions": daily, "recent_ingest": ingest}


# ── Signals ───────────────────────────────────────────────

@router.get("/signals")
async def signals(mode: str = Query("internal_research")):
    """News signals and manual events."""
    async with AsyncSessionLocal() as db:
        r = await db.execute(text("""
            SELECT ns.signal_type, ns.impact_direction, ns.confidence,
                   ns.summary_zh, ns.created_at, t.name
            FROM news_signals ns
            LEFT JOIN teams t ON ns.team_id = t.id
            ORDER BY ns.created_at DESC LIMIT 20
        """))
        signals_list = []
        for row in r.fetchall():
            signals_list.append({
                "type": row[0],
                "direction": row[1],
                "confidence": row[2],
                "summary": row[3],
                "created": str(row[4]) if row[4] else "",
                "team": row[5] or "unknown",
            })

        r = await db.execute(text("""
            SELECT event_type, severity, confidence, note, team_name, created_at
            FROM manual_events
            ORDER BY created_at DESC LIMIT 20
        """))
        events = []
        for row in r.fetchall():
            events.append({
                "type": row[0],
                "severity": row[1],
                "confidence": row[2],
                "note": row[3],
                "team": row[4] or "unknown",
                "created": str(row[5]) if row[5] else "",
            })

        return {"signals": signals_list, "signals_count": len(signals_list),
                "events": events, "events_count": len(events)}


# ── Learning Log ──────────────────────────────────────────

@router.get("/learning")
async def learning(mode: str = Query("internal_research")):
    """Post-match learning log."""
    async with AsyncSessionLocal() as db:
        r = await db.execute(text("""
            SELECT pe.actual_home_goals, pe.actual_away_goals, pe.brier_score,
                   pe.log_loss, pe.top3_hit, pe.calibration_bucket, pe.created_at
            FROM postmatch_eval pe
            ORDER BY pe.created_at DESC LIMIT 30
        """))
        evals = []
        for row in r.fetchall():
            evals.append({
                "score": f"{row[0]}-{row[1]}",
                "brier": round(float(row[2]), 4) if row[2] else None,
                "logloss": round(float(row[3]), 4) if row[3] else None,
                "top3_hit": bool(row[4]),
                "cal_bucket": row[5],
                "at": str(row[6]) if row[6] else "",
            })

        # Summary stats
        r = await db.execute(text("""
            SELECT COUNT(*), AVG(brier_score), AVG(log_loss),
                   SUM(CASE WHEN top3_hit THEN 1 ELSE 0 END) * 1.0 / MAX(COUNT(*), 1)
            FROM postmatch_eval
        """))
        stats = r.fetchone()
        summary = {
            "total": stats[0] or 0,
            "avg_brier": round(float(stats[1] or 0), 4),
            "avg_logloss": round(float(stats[2] or 0), 4),
            "top3_rate": round(float(stats[3] or 0), 4),
        }

        return {"evaluations": evals, "summary": summary}


# ── Output Audit ──────────────────────────────────────────

@router.get("/audit")
async def audit(mode: str = Query("internal_research")):
    """Output safety audit log."""
    async with AsyncSessionLocal() as db:
        r = await db.execute(text("""
            SELECT artifact_type, artifact_path, mode, passed, blocked_terms, checked_at
            FROM output_audit_log
            ORDER BY checked_at DESC LIMIT 30
        """))
        logs = []
        for row in r.fetchall():
            logs.append({
                "type": row[0],
                "path": row[1],
                "mode": row[2],
                "passed": bool(row[3]),
                "blocked": row[4],
                "at": str(row[5]) if row[5] else "",
            })

        r = await db.execute(text("""
            SELECT COUNT(*), SUM(CASE WHEN passed THEN 1 ELSE 0 END)
            FROM output_audit_log
        """))
        stats = r.fetchone()
        return {
            "logs": logs,
            "total": stats[0] or 0,
            "passed_count": stats[1] or 0,
        }


# ── Market Snapshots (internal only) ──────────────────────

@router.get("/market")
async def market(mode: str = Query("internal_research")):
    """Market consensus snapshots — internal_research only."""
    if mode != "internal_research":
        return JSONResponse(
            status_code=403,
            content={"detail": "Market data not available in this mode"},
        )

    async with AsyncSessionLocal() as db:
        r = await db.execute(text("""
            SELECT match_id, captured_at, consensus_home, consensus_draw, consensus_away,
                   bookmaker_count, provider_count, confidence
            FROM market_consensus_snapshots
            ORDER BY captured_at DESC LIMIT 20
        """))
        snapshots = []
        for row in r.fetchall():
            snapshots.append({
                "match_id": row[0],
                "captured": str(row[1]) if row[1] else "",
                "home": round(float(row[2] or 0), 4),
                "draw": round(float(row[3] or 0), 4),
                "away": round(float(row[4] or 0), 4),
                "bookmakers": row[5],
                "providers": row[6],
                "confidence": round(float(row[7] or 0), 4),
            })

        r = await db.execute(text("""
            SELECT COUNT(*), provider FROM market_odds GROUP BY provider
        """))
        providers = [{"provider": row[1], "count": row[0]} for row in r.fetchall()]

        return {"snapshots": snapshots, "providers": providers}
