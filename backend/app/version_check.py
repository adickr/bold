"""Fail fast on unsupported Python versions."""

from __future__ import annotations

import sys


MIN_VERSION = (3, 10)


def ensure_supported_python() -> None:
    if sys.version_info >= MIN_VERSION:
        return
    major, minor = sys.version_info[:2]
    raise SystemExit(
        f"Python {major}.{minor} is not supported. "
        f"This project requires Python {MIN_VERSION[0]}.{MIN_VERSION[1]}+ "
        f"(3.12 recommended).\n\n"
        "On macOS with Homebrew:\n"
        "  brew install python@3.12\n"
        "  cd backend\n"
        "  rm -rf .venv\n"
        "  python3.12 -m venv .venv\n"
        "  source .venv/bin/activate\n"
        "  pip install -r requirements.txt\n"
    )


ensure_supported_python()
