from app.mapping import _zone_offset, metric_record, sleep_records, stable_id, workout_record


def test_heart_rate_mapping() -> None:
    point = {"name": "users/me/dataTypes/heart-rate/dataPoints/123", "dataSource": {"platform": "FITBIT", "device": {"displayName": "Fitbit Air"}}, "heartRate": {"sampleTime": {"physicalTime": "2026-08-27T08:00:00Z", "utcOffset": "0s"}, "beatsPerMinute": "72"}}
    record = metric_record("heart-rate", point)
    assert record is not None
    assert record["type"] == "HEART_RATE"
    assert record["value"] == "72"
    assert record["source"]["deviceModel"] == "Fitbit Air"


def test_sleep_stage_mapping_and_stable_ids() -> None:
    point = {"name": "sleeps/12345", "sleep": {"sleepStages": [{"startTime": "2026-08-26T22:00:00Z", "endTime": "2026-08-26T23:00:00Z", "type": "LIGHT"}, {"startTime": "2026-08-26T23:00:00Z", "endTime": "2026-08-27T00:00:00Z", "type": "DEEP"}]}}
    records = sleep_records(point)
    assert [record["stage"] for record in records] == ["light", "deep"]
    assert records[0]["parentId"] == stable_id(point)


def test_sleep_stage_mapping_uses_current_google_stages_field() -> None:
    point = {
        "name": "users/me/dataTypes/sleep/dataPoints/current-shape",
        "sleep": {
            "stages": [
                {
                    "startTime": "2026-08-28T00:00:00Z",
                    "endTime": "2026-08-28T01:00:00Z",
                    "type": "REM",
                }
            ]
        },
    }

    records = sleep_records(point)

    assert len(records) == 1
    assert records[0]["stage"] == "rem"


def test_hrv_uses_google_full_rmssd_field_name() -> None:
    point = {
        "heartRateVariability": {
            "sampleTime": {"physicalTime": "2026-08-27T08:00:00Z"},
            "rootMeanSquareOfSuccessiveDifferencesMilliseconds": "42.5",
        }
    }

    record = metric_record("heart-rate-variability", point)

    assert record is not None
    assert record["value"] == "42.5"


def test_daily_metric_date_is_converted_to_timestamp() -> None:
    point = {
        "dailyRestingHeartRate": {
            "date": {"year": 2026, "month": 8, "day": 27},
            "beatsPerMinute": "61",
        }
    }

    record = metric_record("daily-resting-heart-rate", point)

    assert record is not None
    assert record["startDate"] == "2026-08-27T00:00:00Z"
    assert record["endDate"] == "2026-08-27T00:00:00Z"


def test_google_duration_offsets_are_normalized() -> None:
    assert _zone_offset("3600s") == "+01:00"
    assert _zone_offset("-18000s") == "-05:00"
    assert _zone_offset("0s") == "+00:00"
    assert _zone_offset("+05:30") == "+05:30"
    assert _zone_offset("90s") is None


def test_daily_sleep_temperature_mapping() -> None:
    point = {
        "dailySleepTemperatureDerivations": {
            "date": {"year": 2026, "month": 8, "day": 28},
            "nightlyTemperatureCelsius": 34.7,
        }
    }
    record = metric_record("daily-sleep-temperature-derivations", point)
    assert record is not None
    assert record["type"] == "HKQuantityTypeIdentifierAppleSleepingWristTemperature"
    assert record["value"] == 34.7


def test_distance_is_converted_from_millimetres_to_metres() -> None:
    point = {
        "distance": {
            "interval": {
                "startTime": "2026-08-28T08:00:00Z",
                "endTime": "2026-08-28T09:00:00Z",
            },
            "millimeters": "5000000",
        }
    }
    record = metric_record("distance", point)
    assert record is not None
    assert record["value"] == 5000
    assert record["unit"] == "m"


def test_active_minutes_are_summed_and_breakdown_is_preserved() -> None:
    levels = [
        {"activityLevel": "LIGHT", "activeMinutes": "12"},
        {"activityLevel": "VIGOROUS", "activeMinutes": "5"},
    ]
    point = {
        "activeMinutes": {
            "interval": {
                "startTime": "2026-08-28T00:00:00Z",
                "endTime": "2026-08-29T00:00:00Z",
            },
            "activeMinutesByActivityLevel": levels,
        }
    }
    record = metric_record("active-minutes", point)
    assert record is not None
    assert record["value"] == 17
    assert record["metadata"] == {"activityLevels": levels}


def test_workout_mapping_keeps_summary_metrics() -> None:
    point = {
        "name": "users/me/dataTypes/exercise/dataPoints/run-1",
        "exercise": {
            "interval": {
                "startTime": "2026-08-28T08:00:00Z",
                "endTime": "2026-08-28T08:30:00Z",
                "startUtcOffset": "3600s",
            },
            "exerciseType": "RUNNING",
            "displayName": "Morning run",
            "metricsSummary": {
                "distanceMillimeters": 5000000,
                "steps": "4200",
                "averageHeartRateBeatsPerMinute": "145",
            },
        },
    }
    workout = workout_record(point)
    assert workout is not None
    assert workout["type"] == "running"
    assert workout["zoneOffset"] == "+01:00"
    assert {item["type"]: item["value"] for item in workout["values"]} == {
        "distance": 5000,
        "stepCount": 4200,
        "averageHeartRate": 145,
    }
