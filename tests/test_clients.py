import asyncio
from datetime import datetime, timezone

import httpx

from app.clients import (
    GoogleHealthClient,
    google_list_params,
    google_time_filter,
    total_calorie_windows,
)


def test_google_list_params_only_use_supported_fields() -> None:
    assert google_list_params("heart-rate") == {"pageSize": "10000"}
    assert google_list_params("heart-rate", "next") == {
        "pageSize": "10000",
        "pageToken": "next",
    }


def test_sleep_uses_google_maximum_page_size() -> None:
    assert google_list_params("sleep") == {"pageSize": "25"}


def test_total_calories_includes_required_bounded_interval_filter() -> None:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 15, tzinfo=timezone.utc)

    assert google_list_params("total-calories", "next", start, end) == {
        "pageSize": "10000",
        "filter": (
            'total_calories.interval.start_time >= "2026-08-01T00:00:00Z" AND '
            'total_calories.interval.start_time < "2026-08-15T00:00:00Z"'
        ),
        "pageToken": "next",
    }


def test_google_filters_cover_each_record_time_shape() -> None:
    start = datetime(2026, 6, 17, 12, 30, tzinfo=timezone.utc)
    end = datetime(2026, 8, 29, 13, 0, tzinfo=timezone.utc)

    assert google_time_filter("heart-rate", start, end) == (
        'heart_rate.sample_time.physical_time >= "2026-06-17T12:30:00Z" AND '
        'heart_rate.sample_time.physical_time < "2026-08-29T13:00:00Z"'
    )
    assert google_time_filter("steps", start, end) == (
        'steps.interval.start_time >= "2026-06-17T12:30:00Z" AND '
        'steps.interval.start_time < "2026-08-29T13:00:00Z"'
    )
    assert google_time_filter("daily-heart-rate-variability", start, end) == (
        'daily_heart_rate_variability.date >= "2026-06-17" AND '
        'daily_heart_rate_variability.date < "2026-08-30"'
    )
    assert google_time_filter("sleep", start, end) == (
        'sleep.interval.civil_end_time >= "2026-06-17" AND '
        'sleep.interval.civil_end_time < "2026-08-30"'
    )
    assert google_time_filter("exercise", start, end) == (
        'exercise.interval.civil_start_time >= "2026-06-17" AND '
        'exercise.interval.civil_start_time < "2026-08-30"'
    )


def test_total_calories_history_is_split_into_fourteen_day_windows() -> None:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 16, tzinfo=timezone.utc)

    assert total_calorie_windows(start, end) == [
        (datetime(2026, 8, 2, tzinfo=timezone.utc), end),
        (start, datetime(2026, 8, 2, tzinfo=timezone.utc)),
    ]


def test_fitbit_air_history_needs_only_six_newest_first_windows() -> None:
    start = datetime(2026, 6, 17, tzinfo=timezone.utc)
    end = datetime(2026, 8, 29, tzinfo=timezone.utc)

    windows = total_calorie_windows(start, end)

    assert len(windows) == 6
    assert windows[0][1] == end
    assert windows[-1][0] == start


def test_google_server_error_is_retried(monkeypatch) -> None:
    responses = [
        httpx.Response(500, request=httpx.Request("GET", "https://health.googleapis.com")),
        httpx.Response(
            200,
            json={"dataPoints": [{"name": "point-1"}]},
            request=httpx.Request("GET", "https://health.googleapis.com"),
        ),
    ]

    class FakeClient:
        calls = 0

        async def get(self, *args, **kwargs):
            response = responses[self.calls]
            self.calls += 1
            return response

    async def no_sleep(_):
        return None

    monkeypatch.setattr("app.clients.asyncio.sleep", no_sleep)
    fake = FakeClient()

    async def collect_points():
        client = GoogleHealthClient("access-token")
        return [point async for point in client._list_window(fake, "heart-rate")]

    assert asyncio.run(collect_points()) == [{"name": "point-1"}]
    assert fake.calls == 2
