import asyncio
import logging
import secrets
from contextlib import asynccontextmanager, suppress
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

logger = logging.getLogger("google-health-importer")
sync_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(scheduled_sync_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Google Health Importer", docs_url=None, redoc_url=None, lifespan=lifespan)
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


def at_or_after(value: str | None, cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    if not value:
        return False
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed >= cutoff


async def send_batch(records: list[dict], sleep: list[dict], sync_time: datetime) -> None:
    settings = get_settings()
    payload = {
        "provider": "google",
        "sdkVersion": "google-health-importer/0.2.0",
        "syncTimestamp": sync_time.isoformat(),
        "data": {"records": records, "sleep": sleep, "workouts": []},
    }
    await send_to_open_wearables(
        settings.open_wearables_url,
        settings.open_wearables_user_id,
        settings.open_wearables_api_key.get_secret_value(),
        payload,
    )


async def run_sync() -> dict[str, int | str]:
    settings = get_settings()
    saved = store().load()
    if not saved.get("refresh_token"):
        raise HTTPException(409, "Connect Google Health first")
    end = datetime.now(timezone.utc)
    cutoff = (
        datetime.fromisoformat(saved["last_sync"]) - timedelta(minutes=10)
        if saved.get("last_sync")
        else None
    )
    client = GoogleHealthClient(await access_token(saved["refresh_token"]))
    record_count = sleep_count = 0
    for data_type in METRICS:
        records: list[dict] = []
        async for point in client.list_points(data_type):
            mapped = metric_record(data_type, point)
            if mapped and at_or_after(mapped.get("endDate") or mapped.get("startDate"), cutoff):
                records.append(mapped)
                if len(records) >= settings.sync_batch_size:
                    await send_batch(records, [], end)
                    record_count += len(records)
                    records = []
        if records:
            await send_batch(records, [], end)
            record_count += len(records)
    sleep: list[dict] = []
    async for point in client.list_points("sleep"):
        for stage in sleep_records(point):
            if at_or_after(stage.get("endDate") or stage.get("startDate"), cutoff):
                sleep.append(stage)
                if len(sleep) >= settings.sync_batch_size:
                    await send_batch([], sleep, end)
                    sleep_count += len(sleep)
                    sleep = []
    if sleep:
        await send_batch([], sleep, end)
        sleep_count += len(sleep)
    saved["last_sync"] = end.isoformat()
    store().save(saved)
    return {"status": "complete", "records": record_count, "sleep_stages": sleep_count}


async def guarded_sync() -> dict[str, int | str]:
    async with sync_lock:
        return await run_sync()


async def scheduled_sync_loop() -> None:
    settings = get_settings()
    delay = max(settings.sync_interval_minutes, 1) * 60
    backoff = delay
    while True:
        await asyncio.sleep(backoff)
        if sync_lock.locked() or not store().load().get("refresh_token"):
            backoff = delay
            continue
        try:
            await guarded_sync()
            backoff = delay
        except Exception:
            logger.exception("Scheduled Google Health sync failed")
            backoff = min(max(backoff * 2, delay), 6 * 60 * 60)


@app.post("/sync", dependencies=[Depends(require_admin)])
async def sync() -> dict[str, int | str]:
    return await guarded_sync()
