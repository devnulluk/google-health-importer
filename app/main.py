import asyncio
import html
import logging
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, status as http_status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.clients import GoogleHealthClient, send_to_open_wearables
from app.config import get_settings
from app.mapping import EXPANDED_METRICS, METRICS, metric_record, sleep_records, workout_record
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
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
])

HOME_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Google Health Importer</title><style>
body{font:16px system-ui,sans-serif;max-width:760px;margin:5rem auto;padding:0 1.5rem;color:#172033;background:#f7f9fc}
main{background:white;padding:2.5rem;border-radius:20px;box-shadow:0 12px 40px #14213d18}h1{margin-top:0;color:#155eef}
a{color:#155eef}code{background:#eef3ff;padding:.15rem .35rem;border-radius:.3rem}.links{display:flex;gap:1rem;flex-wrap:wrap}
</style></head><body><main><h1>Google Health Importer</h1>
<p>A self-hosted, read-only bridge that copies authorised health metrics, activity, workouts and sleep data from Google Health into an Open Wearables instance controlled by the user.</p>
<p>The importer does not sell data, use it for advertising, or train AI models. Administrative and synchronisation controls require HTTP Basic authentication.</p>
<p class="links"><a href="/privacy">Privacy policy</a><a href="https://github.com/devnulluk/google-health-importer">Source code</a></p>
</main></body></html>"""

PRIVACY_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Privacy policy · Google Health Importer</title><style>
body{font:16px/1.6 system-ui,sans-serif;max-width:820px;margin:3rem auto;padding:0 1.5rem;color:#172033}h1,h2{color:#155eef}a{color:#155eef}
</style></head><body><h1>Privacy policy</h1><p><strong>Effective 29 August 2026.</strong></p>
<p>This self-hosted application accesses Google user data only after the user grants OAuth consent. It requests read-only access to Google Health health metrics and measurements, activity and fitness, and sleep data.</p>
<h2>How data is used</h2><p>Authorised data is used solely to provide the user-facing feature of copying the user's health history into the Open Wearables destination selected and controlled by the operator. It is not used for advertising, profiling, sale, surveillance, or training general-purpose AI models.</p>
<h2>Storage and sharing</h2><p>The importer stores an encrypted Google refresh token, a last-sync checkpoint, and aggregate progress counts in its private persistent volume. Health records pass through memory in batches and are sent only to the configured Open Wearables instance; the importer does not retain a second health-record database. No Google user data is shared with unrelated third parties.</p>
<h2>Retention, deletion, and revocation</h2><p>The encrypted connection state is retained until the operator uses the authenticated <code>POST /disconnect</code> control or removes the persistent volume. Disconnecting revokes the Google token and deletes importer state. Records already copied into Open Wearables are controlled by that separate self-hosted service and must be deleted there if desired.</p>
<h2>Security</h2><p>The service is intended to run behind HTTPS. OAuth credentials, API keys and administrator credentials are supplied as deployment secrets, never embedded in source. Administrative routes require authentication, tokens are encrypted at rest, and logs contain aggregate counts rather than health values.</p>
<h2>Google API Services User Data Policy</h2><p>The application's use and transfer of information received from Google APIs adheres to the <a href="https://developers.google.com/terms/api-services-user-data-policy">Google API Services User Data Policy</a>, including its Limited Use requirements, and the <a href="https://developers.google.com/health/policies/health-api-developer-user-data-policy">Google Health API Developer and User Data Policy</a>.</p>
<h2>Contact</h2><p>Questions and deletion requests for this deployed instance can be sent to <a href="mailto:{{CONTACT}}">{{CONTACT}}</a>. Security issues in the software can be reported using the repository's security policy.</p>
<p><a href="/">Return to homepage</a></p></body></html>"""


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


@app.get("/", response_class=HTMLResponse)
def homepage() -> str:
    return HOME_HTML


@app.get("/privacy", response_class=HTMLResponse)
def privacy() -> str:
    contact = html.escape(get_settings().public_contact_email, quote=True)
    return PRIVACY_HTML.replace("{{CONTACT}}", contact)


@app.get("/status", dependencies=[Depends(require_admin)])
def status() -> dict[str, object]:
    state = store().load()
    return {
        "connected": bool(state.get("refresh_token")),
        "last_sync": state.get("last_sync"),
        "sync": state.get("sync", {"status": "idle"}),
    }


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
    saved["expanded_backfill_complete"] = False
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


@app.post("/disconnect", dependencies=[Depends(require_admin)])
async def disconnect() -> dict[str, str]:
    state_store = store()
    saved = state_store.load()
    refresh_token = saved.get("refresh_token")
    if refresh_token:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": refresh_token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
    state_store.delete()
    return {"status": "disconnected", "local_state": "deleted"}


def at_or_after(value: str | None, cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    if not value:
        return False
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed >= cutoff


async def send_batch(
    records: list[dict], sleep: list[dict], sync_time: datetime,
    workouts: list[dict] | None = None,
) -> None:
    settings = get_settings()
    payload = {
        "provider": "google",
        "sdkVersion": "google-health-importer/0.2.0",
        "syncTimestamp": sync_time.isoformat(),
        "data": {"records": records, "sleep": sleep, "workouts": workouts or []},
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
    saved["sync"] = {
        "status": "running",
        "started_at": end.isoformat(),
        "data_type": None,
        "records_accepted": 0,
        "sleep_stages_accepted": 0,
        "workouts_accepted": 0,
    }
    store().save(saved)
    cutoff = (
        datetime.fromisoformat(saved["last_sync"]) - timedelta(minutes=10)
        if saved.get("last_sync")
        else None
    )
    client = GoogleHealthClient(await access_token(saved["refresh_token"]))
    expanded_backfill = not saved.get("expanded_backfill_complete", False)
    record_count = sleep_count = workout_count = 0
    for data_type in METRICS:
        saved["sync"]["data_type"] = data_type
        store().save(saved)
        records: list[dict] = []
        metric_cutoff = None if expanded_backfill and data_type in EXPANDED_METRICS else cutoff
        async for point in client.list_points(data_type, metric_cutoff, end):
            mapped = metric_record(data_type, point)
            if mapped and at_or_after(mapped.get("endDate") or mapped.get("startDate"), metric_cutoff):
                records.append(mapped)
                if len(records) >= settings.sync_batch_size:
                    await send_batch(records, [], end)
                    record_count += len(records)
                    saved["sync"]["records_accepted"] = record_count
                    store().save(saved)
                    records = []
        if records:
            await send_batch(records, [], end)
            record_count += len(records)
            saved["sync"]["records_accepted"] = record_count
            store().save(saved)
    saved["sync"]["data_type"] = "sleep"
    store().save(saved)
    sleep: list[dict] = []
    async for point in client.list_points("sleep"):
        for stage in sleep_records(point):
            if at_or_after(stage.get("endDate") or stage.get("startDate"), cutoff):
                sleep.append(stage)
                if len(sleep) >= settings.sync_batch_size:
                    await send_batch([], sleep, end)
                    sleep_count += len(sleep)
                    saved["sync"]["sleep_stages_accepted"] = sleep_count
                    store().save(saved)
                    sleep = []
    if sleep:
        await send_batch([], sleep, end)
        sleep_count += len(sleep)
        saved["sync"]["sleep_stages_accepted"] = sleep_count
    saved["sync"]["data_type"] = "exercise"
    store().save(saved)
    workouts: list[dict] = []
    async for point in client.list_points("exercise"):
        mapped = workout_record(point)
        workout_cutoff = None if expanded_backfill else cutoff
        if mapped and at_or_after(mapped.get("endDate") or mapped.get("startDate"), workout_cutoff):
            workouts.append(mapped)
            if len(workouts) >= settings.sync_batch_size:
                await send_batch([], [], end, workouts)
                workout_count += len(workouts)
                saved["sync"]["workouts_accepted"] = workout_count
                store().save(saved)
                workouts = []
    if workouts:
        await send_batch([], [], end, workouts)
        workout_count += len(workouts)
        saved["sync"]["workouts_accepted"] = workout_count
    saved["last_sync"] = end.isoformat()
    saved["expanded_backfill_complete"] = True
    saved["sync"].update({
        "status": "complete",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "data_type": None,
        "records_accepted": record_count,
        "sleep_stages_accepted": sleep_count,
        "workouts_accepted": workout_count,
    })
    store().save(saved)
    return {
        "status": "complete", "records": record_count,
        "sleep_stages": sleep_count, "workouts": workout_count,
    }


async def guarded_sync() -> dict[str, int | str]:
    async with sync_lock:
        try:
            return await run_sync()
        except Exception as exc:
            saved = store().load()
            progress = saved.get("sync", {})
            progress.update({
                "status": "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": str(exc)[:500],
            })
            saved["sync"] = progress
            store().save(saved)
            raise


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
