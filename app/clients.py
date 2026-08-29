import asyncio
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, AsyncIterator

import httpx


TOTAL_CALORIES_HISTORY_START = datetime(2009, 1, 1, tzinfo=timezone.utc)
# The live API rejects multi-day total-calorie rollups for some accounts even
# though the reference documents a 14-day maximum. Daily newest-first windows
# are reliable and still keep a bounded user-selected history.
TOTAL_CALORIES_WINDOW = timedelta(days=1)
GOOGLE_MAX_ATTEMPTS = 5


class TotalCaloriesHistoryLimit(Exception):
    """Google has reached the oldest daily total-calorie rollup it will serve."""

SAMPLE_TYPES = {
    "blood-glucose",
    "body-fat",
    "core-body-temperature",
    "heart-rate",
    "heart-rate-variability",
    "height",
    "oxygen-saturation",
    "respiratory-rate-sleep-summary",
    "run-vo2-max",
    "vo2-max",
    "weight",
}
DAILY_TYPES = {
    "daily-heart-rate-variability",
    "daily-heart-rate-zones",
    "daily-oxygen-saturation",
    "daily-respiratory-rate",
    "daily-resting-heart-rate",
    "daily-sleep-temperature-derivations",
    "daily-vo2-max",
}


def _rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def google_time_filter(data_type: str, start: datetime, end: datetime) -> str:
    filter_name = data_type.replace("-", "_")
    if data_type in DAILY_TYPES:
        end_date = (end + timedelta(days=1)).date().isoformat()
        return (
            f'{filter_name}.date >= "{start.date().isoformat()}" AND '
            f'{filter_name}.date < "{end_date}"'
        )
    if data_type == "sleep":
        field = "sleep.interval.civil_end_time"
        return (
            f'{field} >= "{start.date().isoformat()}" AND '
            f'{field} < "{(end + timedelta(days=1)).date().isoformat()}"'
        )
    if data_type == "exercise":
        field = "exercise.interval.civil_start_time"
        return (
            f'{field} >= "{start.date().isoformat()}" AND '
            f'{field} < "{(end + timedelta(days=1)).date().isoformat()}"'
        )
    record_field = (
        "sample_time.physical_time" if data_type in SAMPLE_TYPES else "interval.start_time"
    )
    field = f"{filter_name}.{record_field}"
    return (
        f'{field} >= "{_rfc3339(start)}" AND '
        f'{field} < "{_rfc3339(end)}"'
    )


def total_calorie_windows(
    start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    windows: list[tuple[datetime, datetime]] = []
    window_end = end
    while window_end > start:
        window_start = max(start, window_end - TOTAL_CALORIES_WINDOW)
        windows.append((window_start, window_end))
        window_end = window_start
    return windows


def total_calorie_days(start: datetime, end: datetime) -> list[date]:
    """Return completed civil days newest-first, excluding the current day."""
    days: list[date] = []
    day = end.date() - timedelta(days=1)
    while day >= start.date():
        days.append(day)
        day -= timedelta(days=1)
    return days


def google_list_params(
    data_type: str,
    page_token: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, str]:
    """Build parameters supported by the Google Health v4 list endpoint."""
    params = {"pageSize": "25" if data_type in {"exercise", "sleep"} else "10000"}
    if start is not None and end is not None:
        params["filter"] = google_time_filter(data_type, start, end)
    if page_token:
        params["pageToken"] = page_token
    return params


def google_rollup_body(day: date, page_token: str | None = None) -> dict[str, Any]:
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    body: dict[str, Any] = {
        "range": {"startTime": _rfc3339(start), "endTime": _rfc3339(end)},
        "windowSize": "86400s",
        "pageSize": 10000,
    }
    if page_token:
        body["pageToken"] = page_token
    return body


class GoogleHealthClient:
    base_url = "https://health.googleapis.com/v4/users/me"

    def __init__(self, access_token: str) -> None:
        self.headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    async def _list_window(
        self,
        client: httpx.AsyncClient,
        data_type: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        page_token = None
        while True:
            response = None
            for attempt in range(GOOGLE_MAX_ATTEMPTS):
                response = await client.get(
                    f"{self.base_url}/dataTypes/{data_type}/dataPoints",
                    headers=self.headers,
                    params=google_list_params(data_type, page_token, start, end),
                )
                if response.status_code != 429 and response.status_code < 500:
                    break
                if attempt + 1 < GOOGLE_MAX_ATTEMPTS:
                    await asyncio.sleep(0.5 * (2**attempt))
            assert response is not None
            if response.status_code == 403 and "MISSING_OAUTH_SCOPE" in response.text:
                return
            if response.is_error:
                raise RuntimeError(
                    f"Google Health rejected {data_type} with HTTP "
                    f"{response.status_code}: {response.text[:500]}"
                )
            payload = response.json()
            for point in payload.get("dataPoints", []):
                yield point
            page_token = payload.get("nextPageToken")
            if not page_token:
                break

    async def _rollup_total_calories(
        self, client: httpx.AsyncClient, day: date
    ) -> AsyncIterator[dict[str, Any]]:
        page_token = None
        while True:
            response = None
            for attempt in range(GOOGLE_MAX_ATTEMPTS):
                response = await client.post(
                    f"{self.base_url}/dataTypes/total-calories/dataPoints:rollUp",
                    headers=self.headers,
                    json=google_rollup_body(day, page_token),
                )
                if response.status_code != 429 and response.status_code < 500:
                    break
                if attempt + 1 < GOOGLE_MAX_ATTEMPTS:
                    await asyncio.sleep(0.5 * (2**attempt))
            assert response is not None
            if response.is_error:
                if (
                    response.status_code == 400
                    and "INVALID_ROLLUP_QUERY_DURATION" in response.text
                ):
                    raise TotalCaloriesHistoryLimit
                raise RuntimeError(
                    "Google Health rejected total-calories rollup with HTTP "
                    f"{response.status_code}: {response.text[:500]}"
                )
            payload = response.json()
            for point in payload.get("rollupDataPoints", []):
                value = point.get("totalCalories", {})
                if "kcalSum" not in value:
                    continue
                start_time = datetime.combine(day, time.min, tzinfo=timezone.utc)
                end_time = start_time + timedelta(days=1)
                yield {
                    "name": (
                        "users/me/dataTypes/total-calories/rollups/"
                        f"{day.isoformat()}"
                    ),
                    "totalCalories": {
                        "kcal": value["kcalSum"],
                        "interval": {
                            "startTime": _rfc3339(start_time),
                            "endTime": _rfc3339(end_time),
                        },
                    },
                }
            page_token = payload.get("nextPageToken")
            if not page_token:
                break

    async def list_points(
        self,
        data_type: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=60) as client:
            if data_type != "total-calories":
                async for point in self._list_window(client, data_type, start, end):
                    yield point
                return

            final_end = end or datetime.now(timezone.utc)
            history_start = start or TOTAL_CALORIES_HISTORY_START
            # Daily rollups must cover complete civil days. Asking for today
            # would make the exclusive end tomorrow (a future-ended range),
            # which the live API reports as INVALID_ROLLUP_QUERY_DURATION.
            for day in total_calorie_days(history_start, final_end):
                try:
                    async for point in self._rollup_total_calories(client, day):
                        yield point
                except TotalCaloriesHistoryLimit:
                    break


async def send_to_open_wearables(url: str, user_id: str, api_key: str, payload: dict[str, Any]) -> None:
    headers = {"X-Open-Wearables-API-Key": api_key}
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(f"{url.rstrip('/')}/api/v1/sdk/users/{user_id}/sync", json=payload, headers=headers)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                body = response.json()
                if isinstance(body, dict):
                    detail = str(body.get("detail", ""))
            except ValueError:
                pass
            suffix = f": {detail[:500]}" if detail else ""
            raise RuntimeError(
                f"Open Wearables rejected a sync batch with HTTP {response.status_code}{suffix}"
            ) from exc
