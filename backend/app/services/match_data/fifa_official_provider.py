"""FIFA official Match Centre provider adapter.

FIFA pages are front-end applications and the backing endpoints can change.
This adapter therefore stores the page response and API-attempt metadata first,
then lets the normalizer parse whatever structured payload is actually present.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.services.match_data.schema import RawOfficialMatchData
from app.services.match_data.storage import payload_hash


PROVIDER_NAME = "fifa_official"
DEFAULT_TIMEOUT = 20.0


class FIFAOfficialProvider:
    provider_name = PROVIDER_NAME

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout

    async def fetch(
        self,
        *,
        match_id: str,
        source_url: str,
        provider_match_id: str | None = None,
    ) -> RawOfficialMatchData:
        provider_match_id = provider_match_id or parse_fifa_provider_match_id(source_url)
        fetched_at = datetime.now(timezone.utc).isoformat()
        payload: dict[str, Any] = {
            "source": "fifa_official_match_centre",
            "source_url": source_url,
            "provider_match_id": provider_match_id,
            "fetched_at": fetched_at,
            "page": None,
            "api_attempts": [],
            "structured_payloads": [],
            "discovered_links": [],
        }
        status = "partial"
        content_type = None
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            page = await _fetch_text(client, source_url)
            content_type = page.get("content_type")
            payload["page"] = page
            if page.get("ok"):
                status = "fetched"
                text = page.get("text") or ""
                payload["discovered_links"] = _extract_discovered_links(text)
                for endpoint in candidate_fifa_api_urls(source_url, provider_match_id):
                    attempt = await _fetch_json(client, endpoint)
                    payload["api_attempts"].append(attempt)
                    if attempt.get("ok") and isinstance(attempt.get("json"), dict):
                        payload["structured_payloads"].append(attempt["json"])
                        status = "parsed_candidate"
        return RawOfficialMatchData(
            match_id=str(match_id),
            provider=self.provider_name,
            provider_match_id=provider_match_id,
            source_url=source_url,
            fetched_at=fetched_at,
            payload=payload,
            payload_hash=payload_hash(payload),
            content_type=content_type,
            status=status,
            data_scope="postmatch",
            notes="Official FIFA provider capture; structured fields depend on available public endpoints.",
        )

    @staticmethod
    def from_fixture(
        *,
        match_id: str,
        fixture_path: str | Path,
        source_url: str,
        provider_match_id: str | None = None,
    ) -> RawOfficialMatchData:
        path = Path(fixture_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return RawOfficialMatchData(
            match_id=str(match_id),
            provider=PROVIDER_NAME,
            provider_match_id=provider_match_id or parse_fifa_provider_match_id(source_url),
            source_url=source_url,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            payload=payload,
            payload_hash=payload_hash(payload),
            content_type="application/json; fixture",
            status="fixture",
            data_scope="postmatch",
            notes=f"Loaded from fixture {path.name}",
        )


def parse_fifa_provider_match_id(source_url: str) -> str | None:
    path = urlparse(source_url).path.strip("/")
    parts = path.split("/")
    if parts and parts[-1].isdigit():
        return parts[-1]
    match = re.search(r"/match/[^?#]+/(\d+)", source_url)
    return match.group(1) if match else None


def candidate_fifa_api_urls(source_url: str, provider_match_id: str | None) -> list[str]:
    if not provider_match_id:
        return []
    parsed = urlparse(source_url)
    path_parts = parsed.path.strip("/").split("/")
    numeric_parts = [part for part in path_parts if part.isdigit()]
    candidates = [
        f"https://cxm-api.fifa.com/fifaplusweb/api/sections/matchCentre/{provider_match_id}?locale=en",
        f"https://cxm-api.fifa.com/fifaplusweb/api/data/matchCentreData/{provider_match_id}?locale=en",
        f"https://cxm-api.fifa.com/fifaplusweb/api/sections/match-centre/{provider_match_id}?locale=en",
        f"https://cxm-api.fifa.com/fifaplusweb/api/data/match/{provider_match_id}?locale=en",
    ]
    if len(numeric_parts) >= 4:
        competition, season, stage, match = numeric_parts[-4:]
        candidates.extend(
            [
                f"https://api.fifa.com/api/v3/calendar/matches/{match}",
                f"https://api.fifa.com/api/v3/live/football/{competition}/{season}/{stage}/{match}",
            ]
        )
    return list(dict.fromkeys(candidates))


async def _fetch_text(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    try:
        response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        return {
            "url": url,
            "status_code": response.status_code,
            "ok": response.status_code == 200,
            "content_type": response.headers.get("content-type"),
            "text": response.text[:500000],
            "length": len(response.content),
        }
    except Exception as exc:
        return {"url": url, "ok": False, "error": str(exc)}


async def _fetch_json(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    try:
        response = await client.get(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        item: dict[str, Any] = {
            "url": url,
            "status_code": response.status_code,
            "ok": response.status_code == 200,
            "content_type": response.headers.get("content-type"),
            "length": len(response.content),
        }
        if response.status_code == 200:
            try:
                item["json"] = response.json()
            except Exception:
                item["text"] = response.text[:2000]
                item["ok"] = False
                item["error"] = "response_not_json"
        else:
            item["text"] = response.text[:500]
        return item
    except Exception as exc:
        return {"url": url, "ok": False, "error": str(exc)}


def _extract_discovered_links(text: str) -> list[str]:
    links = re.findall(r"https?://[^\"'<>\\\s]+", text or "")
    interesting = [
        link
        for link in links
        if any(token in link.lower() for token in ("fifa", "pdf", "match", "report", "stats"))
    ]
    return sorted(set(interesting))[:200]

