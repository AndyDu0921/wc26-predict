from __future__ import annotations

import inspect

import pytest
from fastapi.params import Depends
from fastapi.security import HTTPAuthorizationCredentials

from app.config import is_secure_admin_token
from app.dependencies import require_admin_token
from app.exceptions import AuthorizationError
from app.routers.analysis import generate_analysis
from app.routers.predictions import trigger_prediction_public


@pytest.mark.parametrize(
    "token",
    ["", "change-me", "CHANGE_ME_TO_RANDOM_32_CHARS", "short-secret", "admin"],
)
def test_known_or_short_admin_tokens_are_rejected(token):
    assert is_secure_admin_token(token) is False


def test_random_length_admin_token_is_accepted():
    assert is_secure_admin_token("a" * 32) is True


def test_admin_dependency_fails_closed_when_runtime_token_is_weak(monkeypatch):
    from app import dependencies

    monkeypatch.setattr(dependencies.settings, "admin_token", "change-me")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="change-me")

    with pytest.raises(AuthorizationError):
        require_admin_token(credentials)


@pytest.mark.parametrize("endpoint", [trigger_prediction_public, generate_analysis])
def test_expensive_endpoints_declare_admin_dependency(endpoint):
    parameter = inspect.signature(endpoint).parameters["_"]
    assert isinstance(parameter.default, Depends)
    assert parameter.default.dependency is require_admin_token
