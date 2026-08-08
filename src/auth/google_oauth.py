"""Google Sign-In id_token verification."""
from __future__ import annotations

import os
from dataclasses import dataclass

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
if not _CLIENT_ID:
    raise RuntimeError("GOOGLE_OAUTH_CLIENT_ID environment variable is required")

_ALLOWED_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


@dataclass(frozen=True)
class GoogleUser:
    email: str
    name: str
    picture_url: str
    email_verified: bool


def verify_id_token(token: str) -> GoogleUser | None:
    """Verify a Google id_token JWT.

    Returns a GoogleUser on success.
    Returns None on ANY failure (invalid signature, wrong audience, expired,
    wrong issuer, email not verified). Never raises.
    """
    try:
        idinfo = google_id_token.verify_oauth2_token(
            token, google_requests.Request(), audience=_CLIENT_ID
        )
    except Exception:
        return None

    if idinfo.get("iss") not in _ALLOWED_ISSUERS:
        return None

    # Reject unverified emails: Google issues id_tokens for unverified addresses
    # too, and accepting them would let an attacker claim someone else's email.
    if idinfo.get("email_verified") is not True:
        return None

    email = idinfo.get("email")
    if not email:
        return None

    return GoogleUser(
        email=email.lower(),
        name=idinfo.get("name", ""),
        picture_url=idinfo.get("picture", ""),
        email_verified=True,
    )
