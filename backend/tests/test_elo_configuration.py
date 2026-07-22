from app.services.elo_ratings import (
    KAPPA_DEFAULT,
    KAPPA_EPL,
    KAPPA_UCL,
    KAPPA_WORLD_CUP,
    get_kappa_for_competition,
)


def test_elo_draw_kappa_is_code_versioned_by_competition():
    assert get_kappa_for_competition("FIFA World Cup 2026") == KAPPA_WORLD_CUP
    assert get_kappa_for_competition("Premier League") == KAPPA_EPL
    assert get_kappa_for_competition("UEFA Champions League") == KAPPA_UCL
    assert get_kappa_for_competition("Unknown League") == KAPPA_DEFAULT
