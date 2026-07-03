"""Unit tests for venue_context.py — P0-7 home/neutral venue detection."""
from __future__ import annotations

import pytest
from app.services.venue_context import (
    detect_venue_context,
    venue_altitude,
    venue_country,
    venue_capacity,
    VenueContext,
    WC26_TEAM_COUNTRY,
)


class TestDetectVenueContext:
    """Core detection logic tests."""

    def test_mexico_at_azteca_golden_case(self):
        """MEX-ECU at Azteca: the canonical case that drove P0-7."""
        ctx = detect_venue_context("Estadio Azteca", "Mexico", "Ecuador")
        assert not ctx.is_effectively_neutral
        assert ctx.effective_home_advantage >= 0.50
        assert ctx.advantage_team == "Mexico"
        assert ctx.is_high_altitude
        assert ctx.home_in_host_country
        assert ctx.is_home_like_venue
        assert ctx.is_high_crowd_pressure
        assert "high_altitude" in ctx.risk_tags
        assert "host_country_home" in ctx.risk_tags
        assert "home_like_venue" in ctx.risk_tags
        assert "effective_home_advantage" in ctx.risk_tags

    def test_usa_at_att_stadium(self):
        """USA-BIH at AT&T: US home advantage but no altitude."""
        ctx = detect_venue_context("AT&T Stadium", "United States", "Bosnia and Herzegovina")
        assert not ctx.is_effectively_neutral
        assert ctx.effective_home_advantage >= 0.50
        assert ctx.advantage_team == "United States"
        assert not ctx.is_high_altitude
        assert ctx.home_in_host_country
        assert "host_country_home" in ctx.risk_tags

    def test_neutral_match_england_congo(self):
        """ENG-COD at SoFi: truly neutral."""
        ctx = detect_venue_context("SoFi Stadium", "England", "DR Congo")
        assert ctx.is_effectively_neutral
        assert ctx.effective_home_advantage == 0.0
        assert ctx.advantage_team == ""
        assert not ctx.is_high_altitude
        assert not ctx.home_in_host_country
        assert not ctx.is_home_like_venue
        assert ctx.risk_tags == []

    def test_neutral_match_belgium_senegal(self):
        """BEL-SEN at MetLife: truly neutral."""
        ctx = detect_venue_context("MetLife Stadium", "Belgium", "Senegal")
        assert ctx.is_effectively_neutral
        assert ctx.risk_tags == []

    def test_neutral_match_spain_austria(self):
        """ESP-AUT at SoFi: truly neutral."""
        ctx = detect_venue_context("SoFi Stadium", "Spain", "Austria")
        assert ctx.is_effectively_neutral

    def test_neutral_match_portugal_croatia(self):
        """POR-CRO at AT&T: truly neutral."""
        ctx = detect_venue_context("AT&T Stadium", "Portugal", "Croatia")
        assert ctx.is_effectively_neutral

    def test_venue_name_variant_with_parentheses(self):
        """Handle 'Estadio Azteca (Estadio Banorte)' variants."""
        ctx = detect_venue_context("Estadio Azteca (Estadio Banorte)", "Mexico", "Ecuador")
        assert ctx.is_high_altitude
        assert ctx.home_in_host_country

    def test_venue_none(self):
        """None venue → empty context."""
        ctx = detect_venue_context(None, "England", "France")
        assert ctx.is_effectively_neutral
        assert ctx.risk_tags == []

    def test_venue_unknown(self):
        """Unknown venue → empty context (no false positives)."""
        ctx = detect_venue_context("Unknown Stadium", "Brazil", "Argentina")
        assert ctx.is_effectively_neutral
        assert ctx.risk_tags == []

    def test_case_insensitive_team_country(self):
        """Team name matching is case-insensitive."""
        ctx = detect_venue_context("Estadio Azteca", "mexico", "ecuador")
        assert ctx.home_in_host_country
        assert ctx.advantage_team == "mexico"

    def test_bosnia_alias(self):
        """'Bosnia' resolves to 'Bosnia and Herzegovina'."""
        ctx = detect_venue_context("AT&T Stadium", "United States", "Bosnia")
        assert ctx.home_in_host_country

    def test_ivory_coast_aliases(self):
        """Côte d'Ivoire / Ivory Coast / Cote d'Ivoire aliases."""
        for name in ["Côte d'Ivoire", "Cote d'Ivoire", "Ivory Coast"]:
            country = WC26_TEAM_COUNTRY.get(name, "")
            assert country == "Côte d'Ivoire", f"Failed for {name}"


class TestVenueMetadata:
    """Venue metadata lookup tests."""

    def test_altitude_azteca(self):
        assert venue_altitude("Estadio Azteca") == 2240

    def test_altitude_akron(self):
        assert venue_altitude("Estadio Akron") == 1560

    def test_altitude_sofi(self):
        assert venue_altitude("SoFi Stadium") == 38

    def test_altitude_none(self):
        assert venue_altitude(None) == 0

    def test_altitude_unknown(self):
        assert venue_altitude("Unknown") == 0

    def test_country_azteca(self):
        assert venue_country("Estadio Azteca") == "Mexico"

    def test_country_metlife(self):
        assert venue_country("MetLife Stadium") == "United States"

    def test_country_bc_place(self):
        assert venue_country("BC Place") == "Canada"

    def test_capacity_azteca(self):
        assert venue_capacity("Estadio Azteca") == 87523

    def test_capacity_metlife(self):
        assert venue_capacity("MetLife Stadium") == 82500


class TestHighAltitude:
    """Altitude-specific detection tests."""

    def test_azteca_is_high_altitude(self):
        ctx = detect_venue_context("Estadio Azteca", "Mexico", "Ecuador")
        assert ctx.is_high_altitude
        assert ctx.altitude_m == 2240

    def test_akron_is_high_altitude(self):
        ctx = detect_venue_context("Estadio Akron", "Mexico", "Costa Rica")
        assert ctx.is_high_altitude
        assert ctx.altitude_m == 1560

    def test_bbva_not_high_altitude(self):
        ctx = detect_venue_context("Estadio BBVA", "Mexico", "Haiti")
        assert not ctx.is_high_altitude  # 537m < 1500m threshold

    def test_us_stadiums_not_high_altitude(self):
        for venue in ["MetLife Stadium", "SoFi Stadium", "AT&T Stadium",
                       "Mercedes-Benz Stadium", "NRG Stadium", "Levi's Stadium",
                       "Gillette Stadium", "Lumen Field", "Hard Rock Stadium"]:
            ctx = detect_venue_context(venue, "United States", "Canada")
            assert not ctx.is_high_altitude, f"{venue} should not be high altitude"


class TestWC26TeamCoverage:
    """Verify all 48 WC26 teams have country mappings."""

    WC26_TEAMS = [
        # Group A
        "Canada", "Cape Verde", "Germany", "Paraguay",
        # Group B
        "Brazil", "Haiti", "South Africa", "Turkey",
        # Group C
        "France", "Iraq", "New Zealand", "Sweden",
        # Group D
        "Argentina", "Austria", "Jordan", "Saudi Arabia",
        # Group E
        "Algeria", "Belgium", "Norway", "Senegal",
        # Group F
        "Colombia", "Czech Republic", "Costa Rica", "Uzbekistan",
        # Group G
        "DR Congo", "England", "Ghana", "Iran",
        # Group H
        "Spain", "Uruguay", "Côte d'Ivoire",
        # Group I
        "Italy", "Japan", "Netherlands", "Nigeria",
        # Group J
        "Croatia", "Panama", "Portugal", "Ukraine",
        # Group K
        "Mexico", "Ecuador", "South Korea", "Switzerland",
        # Group L
        "Denmark", "Egypt", "Morocco", "Russia",
        # Host nations
        "United States",
    ]

    @pytest.mark.parametrize("team", WC26_TEAMS)
    def test_team_has_country(self, team):
        """Every WC26 team must have a country mapping."""
        from app.services.venue_context import _team_country
        country = _team_country(team, WC26_TEAM_COUNTRY)
        assert country, f"Team '{team}' has no country mapping"
        # For national teams, country == team is the normal case
        # Only flag when a team maps to a clearly wrong country
        known_exceptions: dict[str, str] = {
            "England": "England",  # Part of UK but plays as England
            "United States": "United States",  # USA
            "South Korea": "South Korea",  # KOR
            "DR Congo": "DR Congo",  # COD
            "Côte d'Ivoire": "Côte d'Ivoire",  # CIV
            "Czech Republic": "Czech Republic",  # CZE
            "Saudi Arabia": "Saudi Arabia",  # KSA
            "New Zealand": "New Zealand",  # NZL
            "South Africa": "South Africa",  # RSA
            "Costa Rica": "Costa Rica",  # CRC
            "Cape Verde": "Cape Verde",  # CPV
        }
        if team in known_exceptions:
            assert country == known_exceptions[team], \
                f"Team '{team}' should map to '{known_exceptions[team]}', got '{country}'"
