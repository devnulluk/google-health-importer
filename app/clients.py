from datetime import datetime
from typing import Any, AsyncIterator

import httpx


def google_timestamp(value: datetime) -> str:
    """Format a UTC timestamp in the form required by Google Health."""
    return value.isoformat().replace("+00:00", "Z")


class GoogleHealthClient:
    base_url = "https://health.googleapis.com/v4/users/me"

    def __init__(self, access_token: str) -> None:
        self.headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    async def list_points(self, data_type: str, start: datetime, end: datetime) -> AsyncIterator[dict[str, Any]]:
        page_token = None
        async with httpx.AsyncClient(timeout=60) as client:
            while True:
                params = {
                    "startTime": google_timestamp(start),
                    "endTime": google_timestamp(end),
                    "pageSize": "1000",
                }
                if page_token:
                    params["pageToken"] = page_token
                response = await client.get(
                    f"{self.base_url}/dataTypes/{data_type}/dataPoints",
                    headers=self.headers, params=params,
                )
                response.raise_for_status()
                payload = response.json()
                for point in payload.get("dataPoints", []):
                    yield point
                page_token = payload.get("nextPageToken")
                if not page_token:
                    break


async def send_to_open_wearables(url: str, user_id: str, api_key: str, payload: dict[str, Any]) -> None:
    headers = {"X-Open-Wearables-API-Key": api_key}
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(f"{url.rstrip('/')}/api/v1/sdk/users/{user_id}/sync", json=payload, headers=headers)
        response.raise_for_status()
