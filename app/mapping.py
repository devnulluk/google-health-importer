import hashlib
import json
from typing import Any


METRICS = {
    "heart-rate": ("heartRate", "beatsPerMinute", "HEART_RATE", "bpm"),
    "heart-rate-variability": ("heartRateVariability", "rmssdMilliseconds", "HEART_RATE_VARIABILITY", "ms"),
    "oxygen-saturation": ("oxygenSaturation", "percentage", "OXYGEN_SATURATION", "%"),
    "daily-resting-heart-rate": ("dailyRestingHeartRate", "beatsPerMinute", "RESTING_HEART_RATE", "bpm"),
    "daily-respiratory-rate": ("dailyRespiratoryRate", "breathsPerMinute", "RESPIRATORY_RATE", "count/min"),
}


def stable_id(point: dict[str, Any]) -> str:
    source = point.get("name") or json.dumps(point, sort_keys=True, separators=(",", ":"))
    return "google-" + hashlib.sha256(source.encode()).hexdigest()[:40]


def source_info(point: dict[str, Any]) -> dict[str, Any]:
    source = point.get("dataSource", {})
    device = source.get("device", {})
    method = source.get("recordingMethod", "UNKNOWN").lower()
    return {
        "appId": "google-health-api",
        "name": source.get("platform", "Google Health"),
        "deviceManufacturer": device.get("manufacturer") or "Google",
        "deviceModel": device.get("displayName"),
        "deviceType": "fitness_band",
        "recordingMethod": method if method in {"automatic", "manual", "active"} else "unknown",
    }


def _times(body: dict[str, Any]) -> tuple[str, str, str | None]:
    sample = body.get("sampleTime", {})
    interval = body.get("interval", {})
    start = sample.get("physicalTime") or interval.get("startTime") or body.get("date")
    end = sample.get("physicalTime") or interval.get("endTime") or start
    offset = sample.get("utcOffset") or interval.get("startUtcOffset")
    return start, end, offset


def metric_record(data_type: str, point: dict[str, Any]) -> dict[str, Any] | None:
    body_key, value_key, metric_type, unit = METRICS[data_type]
    body = point.get(body_key, {})
    value = body.get(value_key)
    if value is None:
        return None
    start, end, offset = _times(body)
    if not start:
        return None
    return {
        "id": stable_id(point), "type": metric_type, "startDate": start,
        "endDate": end, "zoneOffset": offset, "source": source_info(point),
        "value": value, "unit": unit,
    }


def sleep_records(point: dict[str, Any]) -> list[dict[str, Any]]:
    sleep = point.get("sleep", point)
    parent_id = stable_id(point)
    result = []
    for index, stage in enumerate(sleep.get("sleepStages", [])):
        label = str(stage.get("type", "UNKNOWN")).lower()
        if label not in {"awake", "light", "deep", "rem"}:
            label = "unknown"
        result.append({
            "id": f"{parent_id}-{index}", "parentId": parent_id, "stage": label,
            "startDate": stage["startTime"], "endDate": stage["endTime"],
            "source": source_info(point),
        })
    return result
