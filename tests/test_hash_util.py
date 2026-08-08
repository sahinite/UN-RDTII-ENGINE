import hashlib
import string

from src.auth.hash_util import sha256_hash


def test_length_is_16():
    assert len(sha256_hash("user@example.com")) == 16


def test_hex_charset():
    allowed = set(string.hexdigits.lower())
    out = sha256_hash("user@example.com")
    assert set(out).issubset(allowed)


def test_case_insensitive():
    assert sha256_hash("Alice@Gmail.com") == sha256_hash("alice@gmail.com")


def test_different_emails_differ():
    pairs = [
        ("a@example.com", "b@example.com"),
        ("alice@gmail.com", "bob@gmail.com"),
        ("user1@x.io", "user2@x.io"),
        ("foo@bar.com", "foo@baz.com"),
        ("hello@world.org", "hola@world.org"),
    ]
    for left, right in pairs:
        assert sha256_hash(left) != sha256_hash(right)


def test_deterministic():
    email = "repeat@example.com"
    assert sha256_hash(email) == sha256_hash(email)


def test_known_value():
    expected = hashlib.sha256("test@example.com".encode()).hexdigest()[:16]
    assert expected == "973dfe463ec85785"
    assert sha256_hash("test@example.com") == "973dfe463ec85785"
