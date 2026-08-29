import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


METRICS = {
    "heart-rate": ("heartRate", ("beatsPerMinute",), "HEART_RATE", "bpm", 1),
    "heart-rate-variability": (
        "heartRateVariability",
        ("rootMeanSquareOfSuccessiveDifferencesMilliseconds",),
        "HEART_RATE_VARIABILITY",
        "ms",
        1,
    ),
    "oxygen-saturation": ("oxygenSaturation", ("percentage",), "OXYGEN_SATURATION", "%", 1),
    "daily-resting-heart-rate": ("dailyRestingHeartRate", ("beatsPerMinute",), "RESTING_HEART_RATE", "bpm", 1),
    "daily-respiratory-rate": ("dailyRespiratoryRate", ("breathsPerMinute",), "RESPIRATORY_RATE", "count/min", 1),
    "daily-heart-rate-variability": ("dailyHeartRateVariability", ("averageHeartRateVariabilityMilliseconds",), "HEART_RATE_VARIABILITY", "ms", 1),
    "daily-sleep-temperature-derivations": ("dailySleepTemperatureDerivations", ("nightlyTemperatureCelsius",), "HKQuantityTypeIdentifierAppleSleepingWristTemperature", "degC", 1),
    "daily-oxygen-saturation": ("dailyOxygenSaturation", ("averagePercentage",), "OXYGEN_SATURATION", "%", 1),
    "respiratory-rate-sleep-summary": ("respiratoryRateSleepSummary", ("fullSleepStats", "breathsPerMinute"), "RESPIRATORY_RATE", "count/min", 1),
    "steps": ("steps", ("count",), "STEP_COUNT", "count", 1),
    "distance": ("distance", ("millimeters",), "DISTANCE", "m", 0.001),
    "active-energy-burned": ("activeEnergyBurned", ("kcal",), "ACTIVE_CALORIES_BURNED", "kcal", 1),
    "total-calories": ("totalCalories", ("kcal",), "TOTAL_CALORIES_BURNED", "kcal", 1),
    "active-zone-minutes": ("activeZoneMinutes", ("activeZoneMinutes",), "HKQuantityTypeIdentifierPhysicalEffort", "min", 1),
    "active-minutes": ("activeMinutes", (), "HKQuantityTypeIdentifierAppleExerciseTime", "min", 1),
    "time-in-heart-rate-zone": ("timeInHeartRateZone", (), "HKQuantityTypeIdentifierAppleExerciseTime", "min", 1),
    "vo2-max": ("vo2Max", ("vo2Max",), "VO2_MAX", "mL/kg/min", 1),
    "daily-vo2-max": ("dailyVo2Max", ("vo2Max",), "VO2_MAX", "mL/kg/min", 1),
    "run-vo2-max": ("runVo2Max", ("runVo2Max",), "VO2_MAX", "mL/kg/min", 1),
    "core-body-temperature": ("coreBodyTemperature", ("temperatureCelsius",), "BODY_TEMPERATURE", "degC", 1),
    "weight": ("weight", ("weightKilograms",), "WEIGHT", "kg", 1),
    "body-fat": ("bodyFat", ("percentage",), "BODY_FAT", "%", 1),
    "height": ("height", ("heightMillimeters",), "HEIGHT", "m", 0.001),
    "blood-glucose": ("bloodGlucose", ("milligramsPerDeciliter",), "BLOOD_GLUCOSE", "mg/dL", 1),
}

EXPANDED_METRICS = set(METRICS) - {
    "heart-rate", "heart-rate-variability", "oxygen-saturation",
    "daily-resting-heart-rate", "daily-respiratory-rate",
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
    start = sample.get("physicalTime") or interval.get("startTime") or _date_timestamp(body.get("date"))
    end = sample.get("physicalTime") or interval.get("endTime") or start
    offset = _zone_offset(sample.get("utcOffset") or interval.get("startUtcOffset"))
    return start, end, offset


def _date_timestamp(value: Any) -> str | None:
    if not isinstance(value, dict):
        return value if isinstance(value, str) else None
    try:
        return datetime(
            int(value["year"]),
            int(value["month"]),
            int(value["day"]),
            tzinfo=timezone.utc,
        ).isoformat().replace("+00:00", "Z")
    except (KeyError, TypeError, ValueError):
        return None


def _zone_offset(value: Any) -> str | None:
    """Convert Google's duration offset (for example ``3600s``) to ``+01:00``."""
    if value is None:
        return None
    text = str(value)
    if re.fullmatch(r"[+-]\d{2}:\d{2}", text):
        return text
    if not text.endswith("s"):
        return None
    try:
        seconds = Decimal(text[:-1])
    except InvalidOperation:
        return None
    if seconds != seconds.to_integral_value() or int(seconds) % 60:
        return None
    total_minutes = int(seconds) // 60
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    if hours > 23:
        return None
    return f"{sign}{hours:02d}:{minutes:02d}"


def _nested_value(body: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = body
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def metric_record(data_type: str, point: dict[str, Any]) -> dict[str, Any] | None:
    body_key, value_path, metric_type, unit, scale = METRICS[data_type]
    body = point.get(body_key, {})
    metadata = None
    if data_type == "active-minutes":
        levels = body.get("activeMinutesByActivityLevel", [])
        value = sum(float(item.get("activeMinutes", 0)) for item in levels)
        metadata = {"activityLevels": levels}
    elif data_type == "time-in-heart-rate-zone":
        start, end, _ = _times(body)
        if not start or not end:
            return None
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        value = (end_dt - start_dt).total_seconds() / 60
        metadata = {"heartRateZone": body.get("heartRateZoneType")}
    else:
        value = _nested_value(body, value_path)
    if value is None:
        return None
    if scale != 1:
        value = float(value) * scale
    start, end, offset = _times(body)
    if not start:
        return None
    return {
        "id": stable_id(point), "type": metric_type, "startDate": start,
        "endDate": end, "zoneOffset": offset, "source": source_info(point),
        "value": value, "unit": unit, "metadata": metadata,
    }


WORKOUT_TYPES = {
    "WALKING": "walking", "RUNNING": "running", "BIKING": "cycling",
    "HIKING": "hiking", "SWIMMING": "swimming", "YOGA": "yoga",
    "PILATES": "pilates", "ROWING": "rowing", "ELLIPTICAL": "elliptical",
    "STAIR_CLIMBING": "stair_climbing", "WEIGHT_TRAINING": "weightlifting",
    "STRENGTH_TRAINING": "strength_training", "HIGH_INTENSITY_INTERVAL_TRAINING": "hiit",
    "BADMINTON": "badminton", "TENNIS": "tennis", "DANCE": "dance",
}


def workout_record(point: dict[str, Any]) -> dict[str, Any] | None:
    exercise = point.get("exercise", {})
    interval = exercise.get("interval", {})
    start = interval.get("startTime")
    end = interval.get("endTime")
    if not start or not end:
        return None
    summary = exercise.get("metricsSummary", {})
    values = []

    def add(kind: str, unit: str, value: Any, scale: float = 1) -> None:
        if value is not None:
            values.append({"type": kind, "unit": unit, "value": float(value) * scale})

    add("activeEnergyBurned", "kcal", summary.get("caloriesKcal"))
    add("distance", "m", summary.get("distanceMillimeters"), 0.001)
    add("stepCount", "count", summary.get("steps"))
    add("averageHeartRate", "bpm", summary.get("averageHeartRateBeatsPerMinute"))
    add("averageSpeed", "m/s", summary.get("averageSpeedMillimetersPerSecond"), 0.001)
    add("elevationAscended", "m", summary.get("elevationGainMillimeters"), 0.001)
    add("vo2Max", "mL/kg/min", summary.get("runVo2Max"))
    kind = str(exercise.get("exerciseType", "OTHER"))
    return {
        "id": stable_id(point), "type": WORKOUT_TYPES.get(kind, "other"),
        "startDate": start, "endDate": end,
        "zoneOffset": _zone_offset(interval.get("startUtcOffset")),
        "source": source_info(point), "title": exercise.get("displayName"),
        "values": values,
        "metadata": {
            "googleExerciseType": kind,
            "activeDuration": exercise.get("activeDuration"),
            "activeZoneMinutes": summary.get("activeZoneMinutes"),
            "heartRateZoneDurations": summary.get("heartRateZoneDurations"),
        },
    }


def sleep_records(point: dict[str, Any]) -> list[dict[str, Any]]:
    sleep = point.get("sleep", point)
    parent_id = stable_id(point)
    result = []
    # Google Health v4 currently returns ``stages``. Keep accepting the
    # earlier ``sleepStages`` spelling for compatibility with older exports.
    stages = sleep.get("stages", sleep.get("sleepStages", []))
    for index, stage in enumerate(stages):
        label = str(stage.get("type", "UNKNOWN")).lower()
        if label not in {"awake", "light", "deep", "rem"}:
            label = "unknown"
        result.append({
            "id": f"{parent_id}-{index}", "parentId": parent_id, "stage": label,
            "startDate": stage["startTime"], "endDate": stage["endTime"],
            "source": source_info(point),
        })
    return result
