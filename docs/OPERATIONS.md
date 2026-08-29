# Operations guide

This guide covers routine operation without exposing credentials or individual
health readings.

## Status and coverage

Both status routes require the same HTTP Basic credentials as the sync and
disconnect controls.

- `GET /status/view` — human-readable status and coverage table; refreshes once
  per minute.
- `GET /status` — JSON status for monitoring and automation.
- `GET /health` — unauthenticated liveness only. A successful liveness response
  does not prove that Google is connected or that a sync has completed.

The authenticated status includes:

- connection state;
- deployed application version;
- configured history start and sync interval;
- historical-backfill state;
- current and most recent successful sync summaries;
- records sent and first/last observed timestamps by data type.

`records_sent` is a transfer count, not a unique-record count. The importer uses
a ten-minute checkpoint overlap and stable identifiers, so safe retries may send
the same record again. Coverage never contains measurement values.

## Routine checks

Weekly:

1. Confirm `/health` responds.
2. Confirm `/status/view` says **Connected** and **Complete**.
3. Confirm the last checkpoint is recent.
4. Look for a failed status or a data type whose last-observed timestamp has
   stopped advancing unexpectedly.
5. Confirm the latest importer and Open Wearables backups completed.

After an upgrade:

1. Confirm the expected Git revision is deployed.
2. Confirm `/health` responds.
3. Wait for one scheduled run and confirm its status is complete.
4. Check that the checkpoint advanced and no validation error was recorded.
5. Verify representative coverage categories without disclosing readings.

## Expected gaps

A zero count in one incremental run means only that Google supplied no new
records of that kind during the checkpoint window. It does not erase earlier
coverage. A category absent from coverage after a historical backfill may mean
the device or Google account did not supply that data type.

Total calories are a derived Google metric. The importer requests completed,
UTC-midnight-aligned daily rollups newest-first. Google may stop serving older
derived totals before the configured history date; supported recent totals are
retained and other data types continue.

## Failure handling

- Transient Google `429` and `5xx` responses are retried with exponential
  backoff.
- Scheduled failures back off up to six hours; a later successful run returns
  to the configured interval.
- Runs do not overlap.
- A failed run retains its aggregate error and progress in encrypted state.
- Stable record IDs make retrying safe.

If a run fails repeatedly, capture the status, failing data type, HTTP status and
error reason. Do not copy OAuth codes, tokens, API keys or health values into an
issue or public log.

