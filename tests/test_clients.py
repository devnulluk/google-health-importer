from datetime import datetime, timezone

from app.clients import google_timestamp


def test_google_timestamp_uses_utc_z_suffix() -> None:
    value = datetime(2026, 8, 28, 12, 34, 56, 123456, tzinfo=timezone.utc)

    assert google_timestamp(value) == "2026-08-28T12:34:56.123456Z"
