"""Pure helpers for bookmaker-level 1X2 market consensus."""

from __future__ import annotations

import statistics
from typing import Any

from app.services.market.probability import normalize_1x2_odds


def normalize_team_name(value: str) -> str:
    """Normalize team/outcome names for provider matching."""
    normalized = (value or "").lower().strip()
    for token in ("fc ", "cf ", "afc "):
        if normalized.startswith(token):
            normalized = normalized[len(token):]
    return normalized


def extract_bookmaker_1x2(
    event: dict[str, Any],
    bookmaker: dict[str, Any],
    home_team: str,
    away_team: str,
) -> dict[str, Any] | None:
    """Extract home/draw/away decimal odds from one bookmaker payload."""
    h2h = None
    for market in bookmaker.get("markets", []) or []:
        if market.get("key") == "h2h":
            h2h = market
            break
    if not h2h:
        return None

    prices: dict[str, float] = {}
    for outcome in h2h.get("outcomes", []) or []:
        name = str(outcome.get("name", "")).strip()
        price = outcome.get("price")
        if name and price:
            try:
                prices[name] = float(price)
            except (TypeError, ValueError):
                continue

    if len(prices) < 3:
        return None

    home_norm = normalize_team_name(home_team or event.get("home_team", ""))
    away_norm = normalize_team_name(away_team or event.get("away_team", ""))
    home_price = draw_price = away_price = None
    non_draw_prices: list[float] = []

    for name, price in prices.items():
        norm = normalize_team_name(name)
        if norm == "draw":
            draw_price = price
        elif home_norm and (home_norm in norm or norm in home_norm):
            home_price = price
        elif away_norm and (away_norm in norm or norm in away_norm):
            away_price = price
        else:
            non_draw_prices.append(price)

    if not all([home_price, draw_price, away_price]):
        # Provider outcome names are sometimes abbreviated.  Fall back to API
        # order only after the name-based pass failed.
        non_draw = [
            price
            for name, price in prices.items()
            if normalize_team_name(name) != "draw"
        ]
        if home_price is None and len(non_draw) >= 1:
            home_price = non_draw[0]
        if away_price is None and len(non_draw) >= 2:
            away_price = non_draw[1]

    if not all([home_price, draw_price, away_price]):
        return None
    if not (home_price > 1.0 and draw_price > 1.0 and away_price > 1.0):
        return None

    return {
        "bookmaker": bookmaker.get("title") or bookmaker.get("key") or "unknown",
        "home_odds": float(home_price),
        "draw_odds": float(draw_price),
        "away_odds": float(away_price),
    }


def consensus_from_bookmakers(
    event: dict[str, Any],
    home_team: str,
    away_team: str,
    *,
    provider: str,
    min_bookmakers: int = 1,
) -> dict[str, Any] | None:
    """Build median-odds consensus from all valid bookmaker quotes."""
    rows = [
        extracted
        for bookmaker in event.get("bookmakers", []) or []
        if (
            extracted := extract_bookmaker_1x2(
                event,
                bookmaker,
                home_team,
                away_team,
            )
        )
    ]
    if len(rows) < min_bookmakers:
        return None

    median_home = statistics.median(row["home_odds"] for row in rows)
    median_draw = statistics.median(row["draw_odds"] for row in rows)
    median_away = statistics.median(row["away_odds"] for row in rows)
    norm = normalize_1x2_odds(median_home, median_draw, median_away)
    bookmaker_names = [str(row["bookmaker"]) for row in rows]

    return {
        "home_prob": norm["home"],
        "draw_prob": norm["draw"],
        "away_prob": norm["away"],
        "provider": provider,
        "overround": norm["overround"],
        "vig": norm["overround"],
        "home_odds": median_home,
        "draw_odds": median_draw,
        "away_odds": median_away,
        "bookmaker": (
            bookmaker_names[0]
            if len(bookmaker_names) == 1
            else f"{len(bookmaker_names)}-bookmaker-consensus"
        ),
        "sample_bookmakers": len(bookmaker_names),
        "bookmaker_list": bookmaker_names,
        "consensus_method": "median_odds_proportional_devig",
    }
