#!/usr/bin/env python3
"""Collect traceable local evidence for one match into the V4.10 ledger."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.evaluation_registry import DEFAULT_DB_PATH  # noqa: E402
from app.services.information_state_engine import (  # noqa: E402
    EvidenceInput,
    collect_match_evidence,
    upsert_evidence_item,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect V4.10 match evidence")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--match-id", default="")
    parser.add_argument("--home", default="")
    parser.add_argument("--away", default="")
    parser.add_argument("--limit-articles", type=int, default=20)
    parser.add_argument("--source-url", default="", help="Optional one-off evidence source URL")
    parser.add_argument("--source-name", default="")
    parser.add_argument("--evidence-type", default="news")
    parser.add_argument("--title", default="")
    parser.add_argument("--text", default="")
    parser.add_argument("--published-at", default="")
    parser.add_argument("--available-at", default="")
    parser.add_argument("--reliability", type=float, default=0.65)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    if args.source_url:
        result = upsert_evidence_item(
            args.db_path,
            EvidenceInput(
                evidence_type=args.evidence_type,
                source_url=args.source_url,
                source_name=args.source_name or "manual",
                title=args.title or args.source_name or args.source_url,
                content=args.text or args.title or args.source_url,
                published_at=args.published_at or None,
                available_at=args.available_at or args.published_at or None,
                reliability_score=args.reliability,
                match_id=args.match_id or None,
                home_team=args.home or None,
                away_team=args.away or None,
                metadata={"input_mode": "manual_cli"},
            ),
        )
        payload = {
            "schema_version": "evidence_collection_cli.v1",
            "mode": "manual_source",
            "inserted": 1 if result["inserted"] else 0,
            "skipped": 0 if result["inserted"] else 1,
            "details": [result],
        }
    else:
        payload = collect_match_evidence(
            args.db_path,
            match_id=args.match_id or None,
            home_team=args.home or None,
            away_team=args.away or None,
            limit_articles=args.limit_articles,
        )

    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
