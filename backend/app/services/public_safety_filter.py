"""Public safety filter — unsafe advice detection and content auditing.

Market odds/bookmaker references are allowed as research evidence in V4.9.
This filter blocks promotional or advice-like gambling language, not raw
market data needed for model quality.

Design: Pure functions, no side effects, importable from anywhere.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Forbidden advice/promotional terms ──
# Raw odds and bookmaker names are allowed as data.  The blocked terms below
# target calls to action, guaranteed outcomes, and monetized tip language.

CREATOR_SAFE_FORBIDDEN = [
    "建议投注",
    "推荐投注",
    "竞彩推荐",
    "下注",
    "带单",
    "推单",
    "稳赚",
    "必中",
    "包赢",
    "盈利保证",
    "betting tips",
    "bet this",
    "odds pick",
    "guaranteed prediction",
    "sure win",
    "lock pick",
    "wager now",
]

# ── Additional forbidden for public_safe (action plan Section 8.2) ──
PUBLIC_SAFE_EXTRA_FORBIDDEN = [
    "胜率", "概率", "比分预测", "预计比分", "xG", "expected goals",
    "主胜", "平局概率", "客胜",
]

# All forbidden terms combined
ALL_FORBIDDEN = list(set(CREATOR_SAFE_FORBIDDEN + PUBLIC_SAFE_EXTRA_FORBIDDEN))


def scan_text(
    text: str,
    terms: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Scan text for forbidden terms.

    Args:
        text: Text to scan.
        terms: List of forbidden terms. Defaults to ALL_FORBIDDEN.

    Returns:
        List of findings with term, position, and context.
    """
    if not text:
        return []
    if terms is None:
        terms = ALL_FORBIDDEN

    findings = []
    for term in terms:
        # Case-insensitive for English, exact for Chinese
        flags = re.IGNORECASE if term.isascii() and not _contains_cjk(term) else 0
        for m in re.finditer(re.escape(term), text, flags=flags):
            ctx_start = max(0, m.start() - 30)
            ctx_end = min(len(text), m.end() + 30)
            findings.append({
                "term": term,
                "position": m.start(),
                "line": text[:m.start()].count("\n") + 1,
                "context": text[ctx_start:ctx_end].replace("\n", " ").strip(),
            })

    return findings


def filter_dict(
    data: dict[str, Any],
    mode: str = "creator_safe",
) -> dict[str, Any]:
    """Recursively filter forbidden terms from dict keys and string values.

    Args:
        data: Dict to filter.
        mode: "creator_safe" or "public_safe".

    Returns:
        Filtered dict with forbidden content replaced.
    """
    if mode == "internal_research":
        return dict(data)

    forbidden = list(CREATOR_SAFE_FORBIDDEN)
    if mode == "public_safe":
        forbidden.extend(PUBLIC_SAFE_EXTRA_FORBIDDEN)

    result = {}
    for key, value in data.items():
        safe_key = key
        for term in forbidden:
            if term.lower() in safe_key.lower():
                safe_key = safe_key.replace(term, "[filtered]")

        if isinstance(value, str):
            safe_value = value
            for term in forbidden:
                if term.lower() in safe_value.lower():
                    safe_value = safe_value.replace(term, "[filtered]")
            result[safe_key] = safe_value
        elif isinstance(value, dict):
            result[safe_key] = filter_dict(value, mode)
        elif isinstance(value, list):
            result[safe_key] = [
                filter_dict(v, mode) if isinstance(v, dict) else v
                for v in value
            ]
        else:
            result[safe_key] = value

    return result


async def audit_artifact(
    text: str,
    artifact_type: str,
    artifact_path: str = "",
    mode: str = "creator_safe",
    db_session=None,
) -> dict[str, Any]:
    """Audit a single artifact and log to output_audit_log.

    Args:
        text: Content to audit.
        artifact_type: "report", "article", "dashboard", "api_response", etc.
        artifact_path: File path or URL.
        mode: Output mode to check against.
        db_session: Async DB session (optional, for logging).

    Returns:
        Audit result dict.
    """
    if mode == "internal_research":
        return {
            "artifact_type": artifact_type,
            "artifact_path": artifact_path,
            "mode": mode,
            "passed": True,
            "blocked_terms": [],
        }

    forbidden = list(CREATOR_SAFE_FORBIDDEN)
    if mode == "public_safe":
        forbidden.extend(PUBLIC_SAFE_EXTRA_FORBIDDEN)

    findings = scan_text(text, forbidden)
    blocked = [f["term"] for f in findings]
    passed = len(blocked) == 0

    # Log to DB if session provided
    if db_session is not None and blocked:
        try:
            import uuid
            from sqlalchemy import text
            await db_session.execute(
                text(
                    "INSERT INTO output_audit_log "
                    "(id, artifact_type, artifact_path, mode, passed, blocked_terms) "
                    "VALUES (:id, :type, :path, :mode, :passed, :terms)"
                ),
                {
                    "id": str(uuid.uuid4()).replace("-", ""),
                    "type": artifact_type,
                    "path": artifact_path,
                    "mode": mode,
                    "passed": passed,
                    "terms": ",".join(blocked),
                },
            )
            await db_session.commit()
        except Exception as exc:
            logger.debug("Output audit log write skipped (best-effort): %s", exc)

    return {
        "artifact_type": artifact_type,
        "artifact_path": artifact_path,
        "mode": mode,
        "passed": passed,
        "blocked_terms": blocked,
    }


def _contains_cjk(text: str) -> bool:
    """Check if text contains CJK characters."""
    return any("一" <= c <= "鿿" or "㐀" <= c <= "䶿" for c in text)
