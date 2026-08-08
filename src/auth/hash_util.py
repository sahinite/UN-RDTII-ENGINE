import hashlib


def sha256_hash(email: str) -> str:
    """Return a stable 16-char lowercase hex prefix of sha256(email_lowercased).
    Used as a filesystem-safe, opaque user identifier for outputs/{hash}/ and logs/{hash}/."""
    return hashlib.sha256(email.lower().encode("utf-8")).hexdigest()[:16]
