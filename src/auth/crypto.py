import os

from cryptography.fernet import Fernet

_KEY_ENV_VAR = "SECRET_ENCRYPTION_KEY"


def _load_fernet() -> Fernet:
    key = os.environ.get(_KEY_ENV_VAR)
    if not key:
        raise RuntimeError(
            "SECRET_ENCRYPTION_KEY environment variable is required"
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


# Cached at import time so a missing key fails loud immediately rather than
# silently deferring the failure to the first encrypt() call.
_FERNET: Fernet = _load_fernet()


def encrypt(plaintext: str) -> bytes:
    return _FERNET.encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes) -> str:
    return _FERNET.decrypt(ciphertext).decode("utf-8")
