import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, status as http_status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.clients import GoogleHealthClient, send_to_open_wearables
from app.config import get_settings
from app.mapping import METRICS, metric_record, sleep_records
from app.store import StateStore

app = FastAPI(title="Google Health Importer", docs_url=None, redoc_url=None)
basic = HTTPBasic()
SCOPES = " ".join([
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.profile.readonly",
])


def store() -> StateStore:
    settings = get_settings()
    return StateStore(settings.state_path, settings.token_encryption_key.get_secret_value())


def require_admin(credentials: HTTPBasicCredentials = Depends(basic)) -> None:
    settings = get_settings()
    valid_user = secrets.compare_digest(credentials.username, settings.app_admin_user)
    valid_password = secrets.compare_digest(
        credentials.password, settings.app_session_secret.get_secret_value()
    )
    if not (valid_user and valid_password):
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/status", dependencies=[Depends(require_admin)])
def status() -> dict[str, object]:
    state = store().load()
    return {"connected": bool(state.get("refresh_token")), "last_sync": state.get("last_sync")}


@app.get("/oauth/start", dependencies=[Depends(require_admin)])
def oauth_start() -> RedirectResponse:
    settings = get_settings()
    state = secrets.token_urlsafe(32)
    saved = store().load()
    saved["oauth_state"] = state
    store().save(saved)
    query = urlencode({
        "client_id": settings.google_client_id, "redirect_uri": settings.google_redirect_uri,
        "response_type": "code", "scope": SCOPES, "access_type": "offline",
        "prompt": "consent", "state": state,
    })
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{query}")


@app.get("/oauth/callback")
async def oauth_callback(request: Request, code: str, state: str) -> dict[str, str]:
    settings = get_settings()
    saved = store().load()
    if not secrets.compare_digest(state, saved.pop("oauth_state", "")):
        raise HTTPException(400, "Invalid OAuth state")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post("https://oauth2.googleapis.com/token", data={
            "code": code, "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret.get_secret_value(),
            "redirect_uri": settings.google_redirect_uri, "grant_type": "authorization_code",
        })
        response.raise_for_status()
    token = response.json()
    if "refresh_token" not in token:
        raise HTTPException(400, "Google did not return an offline refresh token")
    saved["refresh_token"] = token["refresh_token"]
    store().save(saved)
    return {"status": "connected"}


async def access_token(refresh_token: str) -> str:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post("https://oauth2.googleapis.com/token", data={
            "refresh_token": refresh_token, "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret.get_secret_value(),
            "grant_type": "refresh_token",
        })
        response.raise_for_status()
    return response.json()["access_token"]


@app.post("/sync", dependencies=[Depends(require_admin)])
async def sync() -> dict[str, int | str]:
    settings = get_settings()
    saved = store().load()
    if not saved.get("refresh_token"):
        raise HTTPException(409, "Connect Google Health first")
    end = datetime.now(timezone.utc)
    start = datetime.fromisoformat(saved["last_sync"]) - timedelta(minutes=10) if saved.get("last_sync") else end - timedelta(days=settings.initial_sync_days)
    client = GoogleHealthClient(await access_token(saved["refresh_token"]))
    records, sleep = [], []
    for data_type in METRICS:
        async for point in client.list_points(data_type, start, end):
            mapped = metric_record(data_type, point)
            if mapped:
                records.append(mapped)
    async for point in client.list_points("sleep", start, end):
        sleep.extend(sleep_records(point))
    payload = {"provider": "google", "sdkVersion": "google-health-importer/0.1.0", "syncTimestamp": end.isoformat(), "data": {"records": records, "sleep": sleep, "workouts": []}}
    await send_to_open_wearables(settings.open_wearables_url, settings.open_wearables_user_id, settings.open_wearables_api_key.get_secret_value(), payload)
    saved["last_sync"] = end.isoformat()
    store().save(saved)
    return {"status": "queued", "records": len(records), "sleep_stages": len(sleep)}
