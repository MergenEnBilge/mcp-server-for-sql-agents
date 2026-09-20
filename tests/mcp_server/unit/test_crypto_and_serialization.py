import math
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from cryptography.fernet import Fernet

from mcp_sql_server.crypto import SecretBox
from mcp_sql_server.services.serialization import to_json_safe

# --- SecretBox -----------------------------------------------------------------------------


def test_a_secret_round_trips_and_is_not_stored_in_the_clear():
    box = SecretBox(Fernet.generate_key().decode())
    token = box.encrypt("hunter2")
    assert "hunter2" not in token
    assert box.decrypt(token) == "hunter2"


def test_encrypting_twice_gives_different_tokens():
    box = SecretBox(Fernet.generate_key().decode())
    assert box.encrypt("same") != box.encrypt("same")


def test_keys_can_be_rotated_without_breaking_old_secrets():
    old_key, new_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    old_token = SecretBox(old_key).encrypt("hunter2")

    rotated = SecretBox(f"{new_key},{old_key}")  # newest first
    assert rotated.decrypt(old_token) == "hunter2"  # still readable
    new_token = rotated.encrypt("hunter2")
    assert SecretBox(new_key).decrypt(new_token) == "hunter2"  # new writes use the new key alone


def test_the_wrong_key_is_a_clear_error():
    token = SecretBox(Fernet.generate_key().decode()).encrypt("x")
    with pytest.raises(ValueError, match="could not decrypt"):
        SecretBox(Fernet.generate_key().decode()).decrypt(token)


@pytest.mark.parametrize("bad_key", ["", "  ,  ", "change-me", "not base64!!"])
def test_a_missing_or_malformed_key_fails_loudly_at_startup(bad_key):
    with pytest.raises(ValueError):
        SecretBox(bad_key)


# --- to_json_safe --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, True),
        (7, 7),
        ("text", "text"),
        (1.5, 1.5),
        (Decimal("129.00"), 129.0),  # exact as a float, so a JSON number
        (Decimal("59.90"), 59.9),
        (
            Decimal("12345678901234567890.123456789"),
            "12345678901234567890.123456789",
        ),  # not exact: keep as text
        (date(2025, 3, 1), "2025-03-01"),
        (datetime(2025, 3, 1, 10, 30, tzinfo=UTC), "2025-03-01T10:30:00+00:00"),
        (time(10, 30), "10:30:00"),
        (timedelta(hours=1), "1:00:00"),
        (UUID("12345678-1234-5678-1234-567812345678"), "12345678-1234-5678-1234-567812345678"),
        (b"\x00\x01\x02", "<binary, 3 bytes>"),
        ((1, "a"), [1, "a"]),
        ({"k": Decimal("1.5")}, {"k": 1.5}),
    ],
)
def test_values_become_json_friendly(value, expected):
    assert to_json_safe(value) == expected


def test_non_finite_floats_become_text_because_json_cannot_hold_them():
    assert to_json_safe(math.inf) == "inf"
    assert to_json_safe(math.nan) == "nan"
    assert to_json_safe(Decimal("NaN")) == "NaN"
