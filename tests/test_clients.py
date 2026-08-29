from datetime import datetime, timezone

from app.clients import google_list_params, total_calorie_windows


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
