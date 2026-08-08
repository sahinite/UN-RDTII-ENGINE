"""Local-development OAuth bypass with a loud safety warning."""
from __future__ import annotations

import os

_LOCALHOST_BINDS = {"127.0.0.1", "localhost", "::1"}


def is_bypass_enabled() -> bool:
    return os.environ.get("AUTH_DEV_BYPASS") == "1"


def get_dev_user():
    if not is_bypass_enabled():
        raise RuntimeError("get_dev_user called with bypass disabled")

    email = os.environ.get("DEV_USER_EMAIL", "")
    if not email:
        raise RuntimeError("DEV_USER_EMAIL is required when AUTH_DEV_BYPASS=1")

    # Lazy import: google_oauth raises at import time if GOOGLE_OAUTH_CLIENT_ID
    # is unset, and dev bypass must work without the OAuth client configured.
    from src.auth.google_oauth import GoogleUser

    return GoogleUser(
        email=email,
        name=email.split("@")[0],
        picture_url="",
        email_verified=True,
    )


def check_bypass_safety(bind_address: str) -> str | None:
    if not is_bypass_enabled():
        return None
    if bind_address in _LOCALHOST_BINDS:
        return None
    return (
        "⚠ SECURITY WARNING: AUTH_DEV_BYPASS is enabled and the server is "
        f"bound to {bind_address!r}, which is not a localhost-only address.\n"
        "Dev bypass grants unauthenticated access to anyone who can reach this "
        "address. Set AUTH_DEV_BYPASS=0 for production deployments."
    )
