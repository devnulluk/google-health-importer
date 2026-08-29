# Backup and recovery

The personal health platform has two distinct backup responsibilities:

1. **Open Wearables** contains the durable health history and is the critical
   data backup.
2. **Google Health Importer** contains encrypted connection/checkpoint state and
   can be rebuilt from Git plus deployment settings.

## What to back up

### Open Wearables

Back up the database using its database engine's consistent backup mechanism,
plus any persistent application volumes and the exact deployment definition.
Do not rely on copying live database files while the database is writing.

The backup must be sufficient to restore:

- users and provider links;
- health records, sleep stages and workouts;
- schema/migration state;
- application configuration required to interpret the database.

### Google Health Importer

Back up:

- the `google_health_importer_data` Docker volume, which contains encrypted
  `state.json`;
- the deployed Compose definition and Git revision;
- a separately protected inventory of required deployment-secret names;
- the token-encryption key in the existing secret manager or password vault.

The encrypted state file is unusable without the matching token-encryption key.
Do not put either item in Git, Obsidian, backup logs or screenshots.

## Recommended schedule

- Open Wearables database: daily, with at least one off-host copy.
- Importer state volume: daily and before upgrades.
- Deployment definitions: version-controlled on every change.
- Restore test: quarterly and after material schema or deployment changes.

Use retention appropriate to the available storage, for example seven daily,
five weekly and twelve monthly recovery points. Encryption and access controls
should apply both in transit and at rest.

## Importer backup procedure

Use the storage platform's snapshot/backup facility for the named Docker volume.
If a file-level backup is required, stop or pause the importer briefly, copy the
volume contents with metadata preserved, then start it again. Record:

- backup time in UTC;
- source host and volume name;
- deployed Git revision;
- backup tool result and artifact checksum;
- retention/expiry date.

Never print or archive environment-variable values as part of the job log.

## Importer restore procedure

1. Recreate the stack from the intended Git revision without starting the
   scheduler yet.
2. Restore the importer volume to the expected volume name.
3. Restore deployment secrets through Portainer or the chosen secret manager.
4. Verify the token-encryption key matches the restored encrypted state.
5. Start the importer and check `/health`.
6. Open authenticated `/status/view`; confirm connection, checkpoint and
   backfill state are readable.
7. Allow one scheduled sync and confirm completion.

If encrypted state cannot be decrypted, do not attempt to edit it. Reconnect
Google OAuth to create fresh state; the stable IDs and checkpoint overlap make a
controlled re-import duplicate-safe at the destination.

## Open Wearables restore test

Perform restore tests in an isolated environment, never over the live database.

1. Provision an empty compatible database and application instance.
2. Restore the database and persistent volumes.
3. Run required migrations for the restored application revision.
4. Verify user/provider counts and representative category counts.
5. Verify earliest/latest timestamps for heart rate, HRV, sleep, oxygen,
   activity and workouts without exporting measurement values.
6. Record the recovery-point objective achieved and elapsed restore time.
7. Destroy the isolated test environment securely when validation is complete.

## Recovery priorities

1. Restore Open Wearables and verify its database.
2. Restore importer state and deployment secrets.
3. Confirm the Open Wearables API is reachable from the importer.
4. Resume scheduled imports.
5. Use coverage timestamps to confirm continuity across the outage.

