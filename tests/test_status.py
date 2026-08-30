from datetime import datetime, timezone

from cryptography.fernet import Fernet

from app.config import get_settings
from app.main import status_summary, status_view, update_coverage, update_dashboard_data


def configure(monkeypatch) -> None:
    values = {
        "GOOGLE_CLIENT_ID": "client",
        "GOOGLE_CLIENT_SECRET": "secret",
        "GOOGLE_REDIRECT_URI": "https://importer.example/oauth/callback",
        "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "OPEN_WEARABLES_URL": "https://wearables.example",
        "OPEN_WEARABLES_USER_ID": "user",
        "OPEN_WEARABLES_API_KEY": "key",
        "APP_SESSION_SECRET": "admin-secret",
        "PUBLIC_CONTACT_EMAIL": "privacy@example.com",
        "GOOGLE_HISTORY_START_DATE": "2026-06-17",
        "SYNC_INTERVAL_MINUTES": "5",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


def test_status_summary_is_operational_and_contains_no_token(monkeypatch) -> None:
    configure(monkeypatch)
    state = {
        "refresh_token": "must-not-leak",
        "expanded_backfill_complete": True,
        "last_sync": "2026-08-29T21:58:52Z",
        "sync": {"status": "complete", "records_accepted": 4},
        "coverage": {"heart-rate": {"records_sent": 12}},
    }

    summary = status_summary(state)

    assert summary["connected"] is True
    assert summary["expanded_backfill_complete"] is True
    assert summary["history_start_date"] == "2026-06-17"
    assert summary["sync_interval_minutes"] == 5
    assert "refresh_token" not in summary
    assert "must-not-leak" not in repr(summary)
    get_settings.cache_clear()


def test_coverage_tracks_counts_and_observed_range_without_values() -> None:
    state: dict[str, object] = {}
    sent_at = datetime(2026, 8, 29, 22, 0, tzinfo=timezone.utc)

    update_coverage(
        state,
        "heart-rate",
        [
            {"startDate": "2026-08-29T20:00:00Z", "value": 70},
            {"endDate": "2026-08-29T21:00:00Z", "value": 75},
        ],
        sent_at,
    )
    update_coverage(
        state,
        "heart-rate",
        [{"startDate": "2026-08-28T20:00:00Z", "value": 65}],
        sent_at,
    )

    coverage = state["coverage"]["heart-rate"]  # type: ignore[index]
    assert coverage == {
        "records_sent": 3,
        "last_sent_at": "2026-08-29T22:00:00+00:00",
        "first_seen_at": "2026-08-28T20:00:00Z",
        "last_seen_at": "2026-08-29T21:00:00Z",
    }
    assert "value" not in repr(coverage)


def test_status_view_renders_coverage_without_measurements(monkeypatch) -> None:
    configure(monkeypatch)

    class FakeStore:
        def load(self):
            return {
                "refresh_token": "must-not-leak",
                "expanded_backfill_complete": True,
                "last_sync": "2026-08-29T21:58:52Z",
                "sync": {"status": "complete", "data_type": None},
                "coverage": {
                    "sleep-stages": {
                        "records_sent": 1373,
                        "first_seen_at": "2026-06-17T00:00:00Z",
                        "last_seen_at": "2026-08-29T08:00:00Z",
                    }
                },
            }

    monkeypatch.setattr("app.main.store", lambda: FakeStore())
    page = status_view()

    assert "Historical backfill" in page
    assert "Complete" in page
    assert "sleep-stages" in page
    assert "1,373" in page
    assert "must-not-leak" not in page
    assert "Automatically refreshes every minute" in page
    get_settings.cache_clear()


def test_dashboard_data_tracks_latest_and_only_last_24_hours() -> None:
    state: dict[str, object] = {}
    observed = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    update_dashboard_data(state, "heart-rate", [
        {"startDate": "2026-08-29T11:00:00Z", "value": 60, "unit": "bpm"},
        {"startDate": "2026-08-30T10:00:00Z", "value": 72, "unit": "bpm"},
        {"startDate": "2026-08-30T11:00:00Z", "value": 75, "unit": "bpm"},
    ], observed)

    assert state["latest"]["heart-rate"] == {  # type: ignore[index]
        "timestamp": "2026-08-30T11:00:00Z", "value": 75, "unit": "bpm"
    }
    assert [point["value"] for point in state["series_24h"]["heart-rate"]] == [72.0, 75.0]  # type: ignore[index]
    assert state["coverage"]["heart-rate"]["records_observed"] == 3  # type: ignore[index]
