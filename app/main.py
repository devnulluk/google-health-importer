import asyncio
import html
import json
import logging
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import apprise
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
APP_VERSION = "0.4.1"
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
<h2>Storage and sharing</h2><p>The importer stores an encrypted Google refresh token, a last-sync checkpoint, aggregate progress counts, the latest value for each available category and a bounded 24-hour chart series in its private persistent volume. Full health records pass through memory in batches and are sent only to the configured Open Wearables instance; the importer does not retain a second full health-record database. No Google user data is shared with unrelated third parties. The authenticated dashboard loads Chart.js from jsDelivr to draw charts; the application does not intentionally send chart data to jsDelivr.</p>
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


def status_summary(state: dict[str, object]) -> dict[str, object]:
    settings = get_settings()
    return {
        "service": "google-health-importer",
        "version": APP_VERSION,
        "connected": bool(state.get("refresh_token")),
        "history_start_date": settings.google_history_start_date.isoformat(),
        "sync_interval_minutes": settings.sync_interval_minutes,
        "expanded_backfill_complete": bool(state.get("expanded_backfill_complete")),
        "last_sync": state.get("last_sync"),
        "sync": state.get("sync", {"status": "idle"}),
        "last_success": state.get("last_success"),
        "coverage": state.get("coverage", {}),
        "latest": state.get("latest", {}),
        "series_24h": state.get("series_24h", {}),
        "dashboard_rebuild": state.get("dashboard_rebuild"),
        "notifications": state.get("notifications", {}),
    }


@app.get("/status", dependencies=[Depends(require_admin)])
def status() -> dict[str, object]:
    state = store().load()
    return status_summary(state)


@app.get("/status/view", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def status_view() -> str:
    summary = status_summary(store().load())
    sync = summary["sync"] if isinstance(summary["sync"], dict) else {}
    coverage = summary["coverage"] if isinstance(summary["coverage"], dict) else {}
    latest = summary["latest"] if isinstance(summary["latest"], dict) else {}
    series = summary["series_24h"] if isinstance(summary["series_24h"], dict) else {}
    rows = []
    for data_type, item in sorted(coverage.items()):
        details = item if isinstance(item, dict) else {}
        current = latest.get(data_type, {}) if isinstance(latest.get(data_type), dict) else {}
        latest_value = current.get("value", current.get("stage", current.get("title", "—")))
        latest_unit = current.get("unit", "")
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(data_type))}</td>"
            f"<td>{int(details.get('records_observed', details.get('records_sent', 0))):,}</td>"
            f"<td>{html.escape(str(latest_value))} {html.escape(str(latest_unit))}</td>"
            f"<td>{html.escape(str(current.get('timestamp') or '—'))}</td>"
            f"<td>{html.escape(str(details.get('first_seen_at') or '—'))}</td>"
            f"<td>{html.escape(str(details.get('last_seen_at') or '—'))}</td>"
            "</tr>"
        )
    status_name = html.escape(str(sync.get("status", "idle")))
    current_type = html.escape(str(sync.get("data_type") or "—"))
    chart_data = json.dumps(series, separators=(",", ":")).replace("<", "\\u003c")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="60">
<title>Importer status</title><style>
body{{font:15px/1.5 system-ui,sans-serif;margin:0;background:#f4f7fb;color:#172033}}main{{max-width:1050px;margin:2rem auto;padding:0 1rem}}
h1{{color:#155eef}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:1rem}}
.card,table{{background:white;border-radius:14px;box-shadow:0 6px 24px #14213d12}}.card{{padding:1rem 1.2rem}}
.label{{color:#667085;font-size:.85rem}}.value{{font-size:1.15rem;font-weight:650;overflow-wrap:anywhere}}
table{{width:100%;border-collapse:collapse;margin-top:1.5rem;overflow:hidden}}th,td{{padding:.7rem;text-align:left;border-bottom:1px solid #e7ebf2}}
th{{background:#eef3ff}}code{{background:#eef3ff;padding:.12rem .3rem;border-radius:.25rem}}a{{color:#155eef}}
.charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:1rem;margin-top:1.5rem}}.chart{{background:white;padding:1rem;border-radius:14px;box-shadow:0 6px 24px #14213d12;min-height:260px}}
</style></head><body><main><h1>Google Health importer</h1>
<div class="grid">
<div class="card"><div class="label">Connection</div><div class="value">{'Connected' if summary['connected'] else 'Disconnected'}</div></div>
<div class="card"><div class="label">Current status</div><div class="value">{status_name}</div></div>
<div class="card"><div class="label">Current data type</div><div class="value">{current_type}</div></div>
<div class="card"><div class="label">Historical backfill</div><div class="value">{'Complete' if summary['expanded_backfill_complete'] else 'Pending'}</div></div>
<div class="card"><div class="label">Last checkpoint</div><div class="value">{html.escape(str(summary['last_sync'] or 'Never'))}</div></div>
<div class="card"><div class="label">Schedule</div><div class="value">Every {int(summary['sync_interval_minutes'])} minutes</div></div>
</div><table><thead><tr><th>Data type</th><th>Observed</th><th>Latest</th><th>Latest timestamp</th><th>First observed</th><th>Last observed</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan="6">Run the historical dashboard rebuild to calculate coverage.</td></tr>'}</tbody></table>
<div id="charts" class="charts"></div>
<p><small>Observed counts are reconstructed from Google and may include source revisions. Charts contain only the latest 24 hours and animate when drawn.</small></p>
<p><a href="/status">JSON status</a> · Automatically refreshes every minute.</p>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js"></script><script>
const series={chart_data}; const host=document.getElementById('charts');
for(const [name,points] of Object.entries(series)){{if(!Array.isArray(points)||!points.length)continue;
 const card=document.createElement('section');card.className='chart';const title=document.createElement('h2');title.textContent=name.replaceAll('-',' ');const canvas=document.createElement('canvas');card.append(title,canvas);host.append(card);
 new Chart(canvas,{{type:'line',data:{{labels:points.map(p=>new Date(p.timestamp).toLocaleTimeString([],{{hour:'2-digit',minute:'2-digit'}})),datasets:[{{label:points[0].unit||name,data:points.map(p=>p.value),borderColor:'#155eef',backgroundColor:'#155eef18',fill:true,tension:.35,pointRadius:0,borderWidth:2}}]}},options:{{responsive:true,maintainAspectRatio:false,animation:{{duration:1600,easing:'easeOutQuart'}},plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{maxTicksLimit:8}}}},y:{{beginAtZero:false}}}}}}}});
}}
</script></main></body></html>"""


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


def update_coverage(
    saved: dict[str, object], data_type: str, items: list[dict], sent_at: datetime
) -> None:
    """Record aggregate transfer coverage without retaining health values."""
    if not items:
        return
    coverage = saved.setdefault("coverage", {})
    assert isinstance(coverage, dict)
    entry = coverage.setdefault(data_type, {})
    assert isinstance(entry, dict)
    timestamps = [
        value
        for item in items
        for value in [item.get("endDate") or item.get("startDate")]
        if isinstance(value, str)
    ]
    entry["records_sent"] = int(entry.get("records_sent", 0)) + len(items)
    entry["last_sent_at"] = sent_at.isoformat()
    if timestamps:
        earliest, latest = min(timestamps), max(timestamps)
        entry["first_seen_at"] = min(
            str(entry.get("first_seen_at") or earliest), earliest
        )
        entry["last_seen_at"] = max(
            str(entry.get("last_seen_at") or latest), latest
        )


def update_dashboard_data(
    saved: dict[str, object], data_type: str, items: list[dict], observed_at: datetime
) -> None:
    """Keep bounded dashboard summaries and 24-hour numeric series."""
    if not items:
        return
    coverage = saved.setdefault("coverage", {})
    assert isinstance(coverage, dict)
    entry = coverage.setdefault(data_type, {})
    assert isinstance(entry, dict)
    entry["records_observed"] = int(entry.get("records_observed", 0)) + len(items)
    entry["last_observed_at"] = observed_at.isoformat()

    dated = []
    for item in items:
        timestamp = item.get("endDate") or item.get("startDate")
        if isinstance(timestamp, str):
            dated.append((timestamp, item))
    if not dated:
        return
    earliest, latest = min(x[0] for x in dated), max(x[0] for x in dated)
    entry["first_seen_at"] = min(str(entry.get("first_seen_at") or earliest), earliest)
    entry["last_seen_at"] = max(str(entry.get("last_seen_at") or latest), latest)

    latest_map = saved.setdefault("latest", {})
    assert isinstance(latest_map, dict)
    latest_time, latest_item = max(dated, key=lambda x: x[0])
    current = latest_map.get(data_type, {})
    if not isinstance(current, dict) or latest_time >= str(current.get("timestamp") or ""):
        summary: dict[str, object] = {"timestamp": latest_time}
        for key in ("value", "unit", "type", "stage", "title"):
            if latest_item.get(key) is not None:
                summary[key] = latest_item[key]
        latest_map[data_type] = summary

    cutoff = observed_at - timedelta(hours=24)
    series_map = saved.setdefault("series_24h", {})
    assert isinstance(series_map, dict)
    existing = series_map.get(data_type, [])
    if not isinstance(existing, list):
        existing = []
    points = {
        str(point.get("timestamp")): point
        for point in existing
        if isinstance(point, dict)
        and isinstance(point.get("timestamp"), str)
        and datetime.fromisoformat(str(point["timestamp"]).replace("Z", "+00:00")) >= cutoff
    }
    for timestamp, item in dated:
        value = item.get("value")
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if parsed >= cutoff:
            points[timestamp] = {"timestamp": timestamp, "value": numeric, "unit": item.get("unit")}
    series_map[data_type] = [points[key] for key in sorted(points)[-2000:]]


async def rebuild_dashboard_history() -> dict[str, object]:
    """Reconstruct coverage and the latest 24 hours without resending records."""
    settings = get_settings()
    saved = store().load()
    token = saved.get("refresh_token")
    if not token:
        raise HTTPException(409, "Connect Google Health first")
    end = datetime.now(timezone.utc)
    history_start = datetime.combine(
        settings.google_history_start_date, datetime.min.time(), tzinfo=timezone.utc
    )
    saved["dashboard_rebuild"] = {"status": "running", "started_at": end.isoformat(), "data_type": None}
    saved["latest"] = {}
    saved["series_24h"] = {}
    coverage = saved.setdefault("coverage", {})
    assert isinstance(coverage, dict)
    for details in coverage.values():
        if isinstance(details, dict):
            details.pop("records_observed", None)
            details.pop("last_observed_at", None)
    store().save(saved)
    client = GoogleHealthClient(await access_token(str(token)))
    counts: dict[str, int] = {}

    def record_batch(data_type: str, items: list[dict]) -> None:
        if not items:
            return
        counts[data_type] = counts.get(data_type, 0) + len(items)
        update_dashboard_data(saved, data_type, items, end)

    for data_type in METRICS:
        saved["dashboard_rebuild"]["data_type"] = data_type
        store().save(saved)
        batch: list[dict] = []
        async for point in client.list_points(data_type, history_start, end):
            mapped = metric_record(data_type, point)
            if mapped:
                batch.append(mapped)
                if len(batch) >= settings.sync_batch_size:
                    record_batch(data_type, batch)
                    batch = []
        record_batch(data_type, batch)
        store().save(saved)

    saved["dashboard_rebuild"]["data_type"] = "sleep"
    store().save(saved)
    batch = []
    async for point in client.list_points("sleep", history_start, end):
        for item in sleep_records(point):
            batch.append(item)
            if len(batch) >= settings.sync_batch_size:
                record_batch("sleep-stages", batch)
                batch = []
    record_batch("sleep-stages", batch)
    store().save(saved)

    saved["dashboard_rebuild"]["data_type"] = "exercise"
    store().save(saved)
    batch = []
    async for point in client.list_points("exercise", history_start, end):
        mapped = workout_record(point)
        if mapped:
            batch.append(mapped)
            if len(batch) >= settings.sync_batch_size:
                record_batch("exercise", batch)
                batch = []
    record_batch("exercise", batch)

    saved["dashboard_rebuild"] = {
        "status": "complete", "started_at": end.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(), "data_type": None,
        "records_observed": sum(counts.values()),
    }
    store().save(saved)
    return {"status": "complete", "records_observed": sum(counts.values()), "categories": counts}


@app.post("/dashboard/rebuild", dependencies=[Depends(require_admin)])
async def dashboard_rebuild() -> dict[str, object]:
    if sync_lock.locked():
        raise HTTPException(409, "A synchronisation or rebuild is already running")
    async with sync_lock:
        return await rebuild_dashboard_history()


async def send_notification(title: str, body: str, kind: str = "info") -> bool:
    configured = get_settings().apprise_urls
    if not configured or not configured.get_secret_value().strip():
        return False

    def notify() -> bool:
        notifier = apprise.Apprise()
        for url in configured.get_secret_value().split():
            notifier.add(url)
        notify_type = getattr(apprise.NotifyType, kind.upper(), apprise.NotifyType.INFO)
        return bool(notifier.notify(title=title, body=body, notify_type=notify_type))

    return await asyncio.to_thread(notify)


async def send_batch(
    records: list[dict], sleep: list[dict], sync_time: datetime,
    workouts: list[dict] | None = None,
) -> None:
    settings = get_settings()
    payload = {
        "provider": "google",
        "sdkVersion": f"google-health-importer/{APP_VERSION}",
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
    history_start = datetime.combine(
        settings.google_history_start_date, datetime.min.time(), tzinfo=timezone.utc
    )
    record_count = sleep_count = workout_count = 0
    for data_type in METRICS:
        saved["sync"]["data_type"] = data_type
        store().save(saved)
        records: list[dict] = []
        metric_cutoff = (
            history_start
            if cutoff is None or (expanded_backfill and data_type in EXPANDED_METRICS)
            else cutoff
        )
        async for point in client.list_points(data_type, metric_cutoff, end):
            mapped = metric_record(data_type, point)
            if mapped and at_or_after(mapped.get("endDate") or mapped.get("startDate"), metric_cutoff):
                records.append(mapped)
                if len(records) >= settings.sync_batch_size:
                    await send_batch(records, [], end)
                    update_coverage(saved, data_type, records, end)
                    update_dashboard_data(saved, data_type, records, end)
                    record_count += len(records)
                    saved["sync"]["records_accepted"] = record_count
                    store().save(saved)
                    records = []
        if records:
            await send_batch(records, [], end)
            update_coverage(saved, data_type, records, end)
            update_dashboard_data(saved, data_type, records, end)
            record_count += len(records)
            saved["sync"]["records_accepted"] = record_count
            store().save(saved)
    saved["sync"]["data_type"] = "sleep"
    store().save(saved)
    sleep: list[dict] = []
    sleep_cutoff = cutoff or history_start
    async for point in client.list_points("sleep", sleep_cutoff, end):
        for stage in sleep_records(point):
            if at_or_after(
                stage.get("endDate") or stage.get("startDate"), sleep_cutoff
            ):
                sleep.append(stage)
                if len(sleep) >= settings.sync_batch_size:
                    await send_batch([], sleep, end)
                    update_coverage(saved, "sleep-stages", sleep, end)
                    update_dashboard_data(saved, "sleep-stages", sleep, end)
                    sleep_count += len(sleep)
                    saved["sync"]["sleep_stages_accepted"] = sleep_count
                    store().save(saved)
                    sleep = []
    if sleep:
        await send_batch([], sleep, end)
        update_coverage(saved, "sleep-stages", sleep, end)
        update_dashboard_data(saved, "sleep-stages", sleep, end)
        sleep_count += len(sleep)
        saved["sync"]["sleep_stages_accepted"] = sleep_count
    saved["sync"]["data_type"] = "exercise"
    store().save(saved)
    workouts: list[dict] = []
    workout_cutoff = history_start if expanded_backfill or cutoff is None else cutoff
    async for point in client.list_points("exercise", workout_cutoff, end):
        mapped = workout_record(point)
        if mapped and at_or_after(mapped.get("endDate") or mapped.get("startDate"), workout_cutoff):
            workouts.append(mapped)
            if len(workouts) >= settings.sync_batch_size:
                await send_batch([], [], end, workouts)
                update_coverage(saved, "exercise", workouts, end)
                update_dashboard_data(saved, "exercise", workouts, end)
                workout_count += len(workouts)
                saved["sync"]["workouts_accepted"] = workout_count
                store().save(saved)
                workouts = []
    if workouts:
        await send_batch([], [], end, workouts)
        update_coverage(saved, "exercise", workouts, end)
        update_dashboard_data(saved, "exercise", workouts, end)
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
    saved["last_success"] = dict(saved["sync"])
    store().save(saved)
    return {
        "status": "complete", "records": record_count,
        "sleep_stages": sleep_count, "workouts": workout_count,
    }


async def guarded_sync() -> dict[str, int | str]:
    async with sync_lock:
        saved_before = store().load()
        was_failing = int(saved_before.get("consecutive_failures", 0)) > 0
        try:
            result = await run_sync()
            saved = store().load()
            saved["consecutive_failures"] = 0
            if was_failing:
                sent = await send_notification(
                    "Personal health import recovered",
                    "Google Health synchronisation is completing normally again.",
                    "success",
                )
                saved["notifications"] = {
                    "last_type": "recovery", "last_sent": sent,
                    "last_attempt_at": datetime.now(timezone.utc).isoformat(),
                }
            store().save(saved)
            return result
        except Exception as exc:
            saved = store().load()
            progress = saved.get("sync", {})
            progress.update({
                "status": "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": str(exc)[:500],
            })
            saved["sync"] = progress
            failures = int(saved.get("consecutive_failures", 0)) + 1
            saved["consecutive_failures"] = failures
            threshold = max(get_settings().notification_failure_threshold, 1)
            if failures == threshold:
                sent = await send_notification(
                    "Personal health import needs attention",
                    f"Google Health synchronisation has failed {failures} times. Data type: {progress.get('data_type') or 'unknown'}.",
                    "failure",
                )
                saved["notifications"] = {
                    "last_type": "failure", "last_sent": sent,
                    "last_attempt_at": datetime.now(timezone.utc).isoformat(),
                }
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
