import importlib

import pytest
from cryptography.fernet import Fernet, InvalidToken


@pytest.fixture(autouse=True)
def _fernet_key(monkeypatch):
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())
    import src.auth.crypto as crypto_module
    importlib.reload(crypto_module)
    yield crypto_module


@pytest.mark.parametrize(
    "plaintext",
    [
        "",
        "hello",
        "sk-proj-abc123_XYZ",
        "unicode: café — 你好 — 🔑",
        "long-" + ("x" * 5000),
    ],
)
def test_roundtrip(_fernet_key, plaintext):
    crypto = _fernet_key
    assert crypto.decrypt(crypto.encrypt(plaintext)) == plaintext


def test_ciphertext_differs_from_plaintext(_fernet_key):
    crypto = _fernet_key
    plaintext = "super-secret-api-key"
    ciphertext = crypto.encrypt(plaintext)
    assert plaintext.encode("utf-8") not in ciphertext


def test_two_encryptions_of_same_value_differ(_fernet_key):
    crypto = _fernet_key
    assert crypto.encrypt("x") != crypto.encrypt("x")


def test_decrypt_tampered_raises(_fernet_key):
    crypto = _fernet_key
    ciphertext = bytearray(crypto.encrypt("payload"))
    # Flip a byte in the middle to avoid mutating only the version prefix.
    idx = len(ciphertext) // 2
    ciphertext[idx] ^= 0x01
    with pytest.raises(InvalidToken):
        crypto.decrypt(bytes(ciphertext))


def test_missing_key_fails_at_import(monkeypatch):
    monkeypatch.delenv("SECRET_ENCRYPTION_KEY", raising=False)
    import src.auth.crypto as crypto_module
    with pytest.raises(RuntimeError, match="SECRET_ENCRYPTION_KEY"):
        importlib.reload(crypto_module)
