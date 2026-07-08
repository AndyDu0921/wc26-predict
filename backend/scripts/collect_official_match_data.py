#!/usr/bin/env python3
"""Collect official post-match data into the Match Data OS raw ledger."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.evaluation_registry import DEFAULT_DB_PATH
from app.services.match_data.fifa_official_provider import FIFAOfficialProvider
from app.services.match_data.storage import save_raw_match_data


async def _run(args: argparse.Namespace) -> dict:
    provider = FIFAOfficialProvider(timeout=args.timeout)
    if args.fixture_json:
        raw = provider.from_fixture(
            match_id=args.match_id,
            fixture_path=args.fixture_json,
            source_url=args.source_url,
            provider_match_id=args.provider_match_id,
        )
    else:
        raw = await provider.fetch(
            match_id=args.match_id,
            source_url=args.source_url,
            provider_match_id=args.provider_match_id,
        )
    summary = {
        "match_id": raw.match_id,
        "provider": raw.provider,
        "provider_match_id": raw.provider_match_id,
        "source_url": raw.source_url,
        "status": raw.status,
        "payload_hash": raw.payload_hash,
        "data_scope": raw.data_scope,
        "structured_payloads": len(raw.payload.get("structured_payloads", []))
        if isinstance(raw.payload, dict)
        else 0,
        "dry_run": args.dry_run,
    }
    if not args.dry_run:
        summary["storage"] = save_raw_match_data(args.db_path, raw)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match-id", required=True)
    parser.add_argument("--source-url", required=True, help="FIFA Match Centre or report URL")
    parser.add_argument("--provider-match-id", default=None)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--fixture-json", default=None, help="Load raw payload from fixture instead of network")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    summary = asyncio.run(_run(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

