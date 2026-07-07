#!/usr/bin/env python3
"""Audit public-facing outputs for unsafe betting-advice language."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scripts.public_output_audit_core import audit_paths, iter_public_files, main


__all__ = ["audit_paths", "iter_public_files", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
