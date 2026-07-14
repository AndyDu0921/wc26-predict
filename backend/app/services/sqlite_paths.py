"""SQLite DB path resolution shared by sync and async prediction paths."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import make_url

from app.config import BACKEND_DIR, get_settings


DEFAULT_LOCAL_STAGE2_DB = BACKEND_DIR / "data" / "local_stage2.db"


def sqlite_path_from_url(url: str) -> Path | None:
    """Return the filesystem path for a SQLite SQLAlchemy URL, if any."""
    parsed = make_url(url)
    if not parsed.drivername.startswith("sqlite"):
        return None
    database = parsed.database
    if not database or database == ":memory:":
        return None
    return Path(database).expanduser().resolve()


def configured_async_sqlite_path(postgres_url: str | None = None) -> Path | None:
    """Return the configured async DB path when POSTGRES_URL is SQLite."""
    url = postgres_url or get_settings().postgres_url
    return sqlite_path_from_url(url)


def current_sync_sqlite_path(postgres_url: str | None = None) -> Path:
    """Return the SQLite file used by sync snapshot/accuracy helpers.

    CLI scripts historically use ``local_stage2.db`` directly. When the
    application is explicitly configured with a SQLite ``POSTGRES_URL`` (for
    smoke tests or local API runs), sync helpers must use that same file.
    """
    return configured_async_sqlite_path(postgres_url) or DEFAULT_LOCAL_STAGE2_DB.resolve()


def assert_canonical_sqlite_alignment(
    *,
    postgres_url: str | None = None,
    sync_db_path: str | Path | None = None,
) -> Path:
    """Ensure API/worker async DB and sync snapshot DB are the same SQLite file."""
    async_path = configured_async_sqlite_path(postgres_url)
    if async_path is None:
        raise RuntimeError(
            "Canonical API/worker prediction requires POSTGRES_URL to be a "
            "SQLite URL so sync snapshots and async prediction_runs share one DB. "
            "Set POSTGRES_URL=sqlite+aiosqlite:///absolute/path/to/local_stage2.db."
        )

    sync_path = Path(sync_db_path).expanduser().resolve() if sync_db_path else current_sync_sqlite_path(postgres_url)
    if async_path != sync_path:
        raise RuntimeError(
            "Canonical prediction DB path mismatch: "
            f"async_db={async_path} sync_db={sync_path}. "
            "Refusing to run because this would split prediction_runs from snapshots."
        )
    return async_path
