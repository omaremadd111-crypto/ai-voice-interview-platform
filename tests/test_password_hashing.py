"""Password hashing: PBKDF2-HMAC-SHA256, no database required."""
import pytest

from services.password_hashing import hash_password, verify_password


def test_hash_is_never_the_plaintext() -> None:
    hashed = hash_password("correct horse battery staple")
    assert "correct horse battery staple" not in hashed


def test_verify_accepts_the_correct_password() -> None:
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True


def test_verify_rejects_the_wrong_password() -> None:
    hashed = hash_password("correct horse battery staple")
    assert verify_password("wrong password", hashed) is False


def test_two_hashes_of_the_same_password_differ() -> None:
    """Different random salts each time -- rules out a naive unsalted hash."""
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")
    assert first != second
    assert verify_password("correct horse battery staple", first) is True
    assert verify_password("correct horse battery staple", second) is True


def test_verify_rejects_malformed_stored_hash() -> None:
    assert verify_password("anything", "not-a-real-hash") is False


def test_hash_password_rejects_blank_password() -> None:
    with pytest.raises(ValueError):
        hash_password("")
