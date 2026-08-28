from typing import Any, AsyncIterator

import httpx


def google_list_params(data_type: str, page_token: str | None = None) -> dict[str, str]:
    """Build parameters supported by the Google Health v4 list endpoint."""
    params = {"pageSize": "25" if data_type in {"exercise", "sleep"} else "10000"}
    if page_token:
        params["pageToken"] = page_token
    return params


class GoogleHealthClient:
    base_url = "https://health.googleapis.com/v4/users/me"

    def __init__(self, access_token: str) -> None:
        self.headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    async def list_points(self, data_type: str) -> AsyncIterator[dict[str, Any]]:
        page_token = None
        async with httpx.AsyncClient(timeout=60) as client:
            while True:
                response = await client.get(
                    f"{self.base_url}/dataTypes/{data_type}/dataPoints",
                    headers=self.headers, params=google_list_params(data_type, page_token),
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
