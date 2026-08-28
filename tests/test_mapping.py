from app.mapping import metric_record, sleep_records, stable_id


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
