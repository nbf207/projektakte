# Projektakte · Self-Hosting & Operations Guide

This guide covers production deployment on Linux and Raspberry Pi (including 2 GB RAM models), reverse-proxy/TLS setup, optional integrations, automatic pre-migration snapshots, verified backups, and safe rollbacks.

For a quick local start, see the root [README](../README.md).

---

## 1. Architecture Overview

Projektakte runs as two containers from the same multi-architecture image (`ghcr.io/nbf207/projektakte`, supporting `linux/amd64` and `linux/arm64`):

| Service | Command | Role |
| --- | --- | --- |
| `web` | `python scripts/entrypoint.py --host 0.0.0.0 --port 8000` | Startup safety checks, auto-secret/migrate, then Waitress WSGI + Django + WhiteNoise |
| `worker` | `python manage.py worker` | Single serial background worker (OCR sync, Gemini extraction, thumbnails, IMAP import, exports, backups); waits for `web` to become `healthy` |

- **Storage:** SQLite (`/data/db.sqlite3`) in `IMMEDIATE` transaction mode, coordinated across `web` and `worker` by a shared OS file lock (`akte/operations.py`). Media, backups, and feedback live under `/data/`.
- **Container hardening:** Both services run as unprivileged UID/GID `10001`, with `read_only: true`, `cap_drop: [ALL]`, `no-new-privileges:true`, and a 64 MiB `/tmp` tmpfs. Neither container needs Docker socket access.
- **Browser performance:** WhiteNoise serves versioned static assets with a 1-year cache (`WHITENOISE_MAX_AGE = 31536000`), `GZipMiddleware` compresses HTML responses, and `static/instant-shell.js` keeps the app shell and recently visited read-only views in volatile tab RAM (never persisted to disk, cleared immediately on any form submit or mutation).

---

## 2. Production Installation (Linux / Raspberry Pi)

1. **Prepare configuration:**
   Place `compose.yaml` and `.env.example` in your deployment folder and copy `.env.example` to `.env`:

   ```sh
   cp .env.example .env
   ```

   By default, `compose.yaml` uses the named Docker volume `projektakte_data:/data` (no manual `chown` required). If you prefer a host bind-mount (`./data:/data`), make it writable by UID/GID `10001`:
   ```sh
   sudo install -d -m 0700 -o 10001 -g 10001 ./data
   ```

2. **Configure HTTPS & reverse proxy in `.env`:**
   - Terminate TLS at your reverse proxy (Caddy, Nginx, Traefik, Cloudflare Tunnel, etc.). `compose.yaml` binds only to `127.0.0.1:${APP_PORT:-8010}:8000` on the host.
   - For any public/internet-facing installation, set:

     ```dotenv
     DEMO_MODE=0
     DEBUG=0
     HTTPS=1
     ALLOW_INSECURE_HTTP=0
     PUBLIC_HOST=projektakte.example.com
     ALLOWED_HOSTS=projektakte.example.com,127.0.0.1
     CSRF_TRUSTED_ORIGINS=https://projektakte.example.com
     TRUSTED_PROXY=<proxy-ip-or-dns-name>
     ```

   - Always keep `127.0.0.1` in `ALLOWED_HOSTS` for the internal healthcheck.
   - `TRUSTED_PROXY` must match the actual source IP or DNS name seen by the `web` container. Never use wildcard hosts.
   - For a private LAN/VPN-only installation without TLS, you may explicitly set `HTTPS=0` and `ALLOW_INSECURE_HTTP=1`.
   - **Secret key:** Leave `SECRET_KEY` empty to auto-generate a persistent random key atomically at `/data/.secret-key` (`chmod 0600`) on first start, or provide `SECRET_KEY` / `SECRET_KEY_FILE` explicitly (never set both).

3. **Start and complete first-run setup:**

   ```sh
   docker compose config --quiet
   docker compose up -d
   docker compose ps
   ```

   *(Optional overrides: add `-f deployment/compose-pi.example.yaml` for Raspberry Pi CPU/PID limits and `./secrets:/run/secrets:ro` mounts, or `-f deployment/compose-build.yaml` to build locally from source.)*

   Open your URL in a browser: when 0 users exist, `/login/` redirects to `/setup/`, where you create your first admin account (enforcing Django's password validators atomically). Once the first user exists, `/setup/` permanently returns `404`. Alternatively, you can still create an account via CLI (`docker compose exec web python manage.py createsuperuser`).

---

## 3. Enabling Optional Integrations Anytime Later

All external integrations are disabled by default (`AI_ENABLED=0`, empty URLs/credentials) and can be enabled or changed at any time without reinstalling or risking existing data—simply edit `.env` and run `docker compose up -d`:

- **Google Gemini AI:** Set `AI_ENABLED=1`, `GEMINI_MODEL` (supported values: `gemini-3.5-flash-lite` or `gemini-3.8-flash`), `GEMINI_API_KEY` (or `GEMINI_API_KEY_FILE`), and configure your monthly budget guardrails (`AI_MONTHLY_WARN`, `AI_MONTHLY_LIMIT`, token prices, `GEMINI_ORIGINAL_MAX_MB`). Invalid model names fail fast at startup.
- **Paperless-ngx:** Set `PAPERLESS_URL` (reachable from `worker`) and `PAPERLESS_TOKEN` (or `PAPERLESS_TOKEN_FILE`). Paperless stays a separate system with its own database and backup lifecycle.
- **IMAP Mail Import:** Configure `MAIL_IMAP_HOST`, `MAIL_IMAP_USER`, `MAIL_IMAP_PASSWORD`, `MAIL_ALLOWED_SENDER`, and `MAIL_CAPTURE_USER`.
- **Feedback Widget (optional):** Disabled by default (`FEEDBACK_WIDGET_ENABLED=0`) for clean self-hosting. Set `FEEDBACK_WIDGET_ENABLED=1` if you want the floating feedback button and `/einstellungen/feedback/` tab enabled.
- **Repair Library:** Operator-owned manuals and media live strictly in runtime `/data/` outside version control.

---

## 4. Safe Updates, Pre-Migration Snapshots & Rollback

### How Automatic Startup Safety Works (`scripts/entrypoint.py`)
Every time `web` starts (with `AUTO_MIGRATE=1`, the default):
1. **Integrity check:** Runs `PRAGMA integrity_check` on `/data/db.sqlite3`.
2. **Downgrade guard:** Compares `/data/.app_version` (as real semantic version tuples) and checks `django_migrations` for migrations unknown to the running image. If the database is newer than the container image, startup aborts cleanly with a clear error message instead of corrupting data.
3. **Pre-migration SQLite snapshot:** If—and only if—new Django migrations are pending on an existing database, Projektakte acquires the shared `operation_gate()`, creates a verified `sqlite3.Connection.backup()` snapshot under `/data/backups/pre-migrate/db-before-v<version>-<timestamp>.sqlite3`, and rotates older automatic snapshots (keeping the newest `PRE_MIGRATE_KEEP=5`). If the snapshot fails, migrations are not run.
4. **Migration & version marker:** Runs `manage.py migrate --noinput` under `operation_gate()` and atomically updates `/data/.app_version`.

*(Set `AUTO_MIGRATE=0` in `.env` if you prefer running migrations manually during a maintenance window.)*

### Updating to a New Version
Pinning explicit version tags in `compose.yaml` (e.g. `ghcr.io/nbf207/projektakte:1.0.1`) gives deterministic updates:

```sh
# 1. Update the image tag in compose.yaml (or .env PROJEKTAKTE_IMAGE)
docker compose pull
docker compose up -d
docker compose ps
```

### Rolling Back After an Update
If an update causes issues and you want to return to the previous version:

1. Stop `web` and `worker`:
   ```sh
   docker compose stop web worker
   ```
2. Pin the previous image tag in `compose.yaml`.
3. If the newer version had applied database migrations, list and restore the automatic pre-migration snapshot:
   ```sh
   docker compose run --rm --no-deps web python manage.py rollback_db --list
   docker compose run --rm --no-deps web python manage.py rollback_db --restore latest
   ```
   `rollback_db` verifies snapshot integrity, saves a safety copy of the current database (`db-before-rollback-*.sqlite3`), restores the snapshot atomically under `operation_gate()`, and clears `.app_version` so the older image boots cleanly.
4. Start services again:
   ```sh
   docker compose up -d
   docker compose ps
   ```

---

## 5. Full Application Backups & Verified Restore

Pre-migration snapshots protect `db.sqlite3` automatically during updates. For disaster recovery (including uploaded media and feedback), create full verified ZIP backups:

- **In-app:** *Einstellungen → System & Sicherung* (keeps 10 managed backups by default).
- **CLI (standalone without Paperless):**
  ```sh
  docker compose exec web python manage.py backup /data/backups/projektakte-backup.zip
  ```
- **CLI (with Paperless-ngx configured):** Pass `--paperless-bundle /path/to/paperless-bundle.zip` so both archives stay coordinated.

Test restores into a **separate empty directory** first—never directly over your live data directory:

```sh
docker compose run --rm --no-deps web python manage.py restore /data/backups/projektakte-backup.zip /tmp/restore-test
```

### Disaster Recovery (Restoring onto a Fresh Host)

To rebuild Projektakte on a brand-new server or SD card from a full ZIP backup:

1. Prepare a clean deployment folder with `compose.yaml` and your backed-up `.env` (plus `./secrets` if you use `*_FILE` mounts).
2. Create an empty data directory owned by UID/GID `10001` (or start with an empty named volume) and place your verified `projektakte-backup.zip` where the container can read it (e.g. `./restore-input/projektakte-backup.zip`).
3. Verify and unpack the archive into the fresh data directory **before** starting `web` and `worker`:
   ```sh
   docker compose run --rm --no-deps \
     -v "$(pwd)/restore-input:/restore:ro" \
     web python manage.py restore /restore/projektakte-backup.zip /data
   ```
   `restore` verifies archive paths, SHA-256 checksums, and SQLite integrity before writing anything.
4. Start the stack and verify both containers become `healthy`:
   ```sh
   docker compose up -d
   docker compose ps
   ```
   If you restore an older backup onto a newer image, `entrypoint.py` will automatically create a pre-migration snapshot and apply any newer migrations. If Paperless-ngx was used, restore Paperless separately from its matching backup.

> Back up `.env`, secret files, Compose overrides, and external services separately, and copy backup archives off the host disk.

---

## 6. Security Model & Maintenance Commands

- **Default-deny auth boundary:** All routes except `/login/`, `/setup/` (only while 0 users exist), `/manifest.json`, `/sw.js`, and the isolated `/demo/` boundary require authentication. `/admin/` returns `404` for non-staff users. Both `/login/` and `/setup/` are rate-limited per client IP.
- **Security headers & CSP:** Dynamic HTML responses send `Content-Security-Policy` (`default-src 'self'; script-src 'self'; ...; frame-ancestors 'none'`), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, and `Cache-Control: no-store, private`. Streamed user files receive a stricter `sandbox; default-src 'none'` CSP; active formats (`.html`, `.svg`, `.js`, etc.) are forced to download; Markdown is sanitized with `nh3` (Rust).
- **Reset a forgotten admin password:**
  ```sh
  docker compose exec web python manage.py changepassword <username>
  ```
- **Maintenance commands:**
  - `python manage.py check` — configuration sanity check.
  - `python manage.py cleanup` — previews orphan/temporary cleanup before applying.
  - `python manage.py rebuild_search` — rebuilds the SQLite FTS search index.
  - `python manage.py rollback_db --list` — lists automatic pre-migration SQLite snapshots.
