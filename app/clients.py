from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

import httpx


TOTAL_CALORIES_HISTORY_START = datetime(2009, 1, 1, tzinfo=timezone.utc)
TOTAL_CALORIES_WINDOW = timedelta(days=14)


def _rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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


def google_list_params(
    data_type: str,
    page_token: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, str]:
    """Build parameters supported by the Google Health v4 list endpoint."""
    params = {"pageSize": "25" if data_type in {"exercise", "sleep"} else "10000"}
    if data_type == "total-calories" and start is not None and end is not None:
        params["filter"] = (
            f'total_calories.interval.start_time >= "{_rfc3339(start)}" AND '
            f'total_calories.interval.start_time < "{_rfc3339(end)}"'
        )
    if page_token:
        params["pageToken"] = page_token
    return params


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
            response = await client.get(
                f"{self.base_url}/dataTypes/{data_type}/dataPoints",
                headers=self.headers,
                params=google_list_params(data_type, page_token, start, end),
            )
            if response.status_code == 403 and "MISSING_OAUTH_SCOPE" in response.text:
                return
            response.raise_for_status()
            payload = response.json()
            for point in payload.get("dataPoints", []):
                yield point
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
                async for point in self._list_window(client, data_type):
                    yield point
                return

            final_end = end or datetime.now(timezone.utc)
            for window_start, window_end in total_calorie_windows(
                start or TOTAL_CALORIES_HISTORY_START, final_end
            ):
                async for point in self._list_window(
                    client, data_type, window_start, window_end
                ):
                    yield point


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
