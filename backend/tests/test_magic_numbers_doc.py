from pathlib import Path

from app.core import engine


def test_magic_numbers_doc_tracks_current_core_constants():
    doc = (Path(__file__).resolve().parents[1] / "docs" / "MAGIC_NUMBERS.md").read_text(encoding="utf-8")

    expected = {
        "WC_XG_CALIBRATION_FACTOR": engine.WC_XG_CALIBRATION_FACTOR,
        "NEGBIN_R": engine.NEGBIN_R,
        "NEGBIN_FUSION_WEIGHT": engine.NEGBIN_FUSION_WEIGHT,
        "DRAW_FLOOR": engine.DRAW_FLOOR,
        "KO_DRAW_FLOOR": engine.KO_DRAW_FLOOR,
        "MARKET_CONSENSUS_CV_THRESHOLD": engine.MARKET_CONSENSUS_CV_THRESHOLD,
        "MARKET_CONSENSUS_BOOST": engine.MARKET_CONSENSUS_BOOST,
        "MARKET_CONSENSUS_MAX_CAP": engine.MARKET_CONSENSUS_MAX_CAP,
        "MARKET_CONSENSUS_MIN_BOOKMAKERS": engine.MARKET_CONSENSUS_MIN_BOOKMAKERS,
    }

    for name, value in expected.items():
        assert f"`{name}`" in doc
        assert f"| {value}" in doc or f"| {value} " in doc
