# Google Health importer

A small, private bridge from Google Health API to Open Wearables 0.6.3. It uses Google's read-only scopes and Open Wearables' supported Google SDK ingestion endpoint. It does not access the Open Wearables database.

The first sync imports all available authorised Google Health history in batches. Later syncs still enumerate Google's catalogue because the v4 API has no date-range filter, but only records newer than the saved checkpoint (with a ten-minute overlap) are sent to Open Wearables. Stable record IDs make retries duplicate-safe.

After connection, the importer runs automatically every 15 minutes. Runs never overlap, and temporary failures use an exponential backoff capped at six hours. `SYNC_INTERVAL_MINUTES` and `SYNC_BATCH_SIZE` can be overridden in Portainer.

## First deployment

1. Build the stack on Mobius from `compose.yml` and add the environment values in Portainer.
2. Point a secure reverse-proxy hostname at the importer's port (the compose example publishes port `8010`).
3. Create the Google OAuth web client only then, using `https://your-importer.example/oauth/callback` as the callback pattern.
4. Put the new client ID and secret directly into Portainer; do not store them in Obsidian or Git.
5. Visit `/oauth/start` once, sign in with `APP_ADMIN_USER` and `APP_SESSION_SECRET`, consent, then POST `/sync` using the same HTTP Basic credentials to start the initial import immediately. Scheduled syncs begin automatically after connection.

Generate `TOKEN_ENCRYPTION_KEY` with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Generate a long, unique `APP_SESSION_SECRET` with a password manager; it protects the connection, status, and sync controls.

The encrypted state volume contains the Google refresh token, incremental checkpoint, and aggregate sync progress. The authenticated `/status` endpoint reports the current data type and accepted counts without returning health values or tokens.
