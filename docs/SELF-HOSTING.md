# Projektakte · Self-Hosting & Operations Guide

This guide covers production deployment on Linux and Raspberry Pi (including 2 GB RAM models), reverse-proxy/TLS configuration, external integrations, consistent backups, verified restores, and safe updates.

For a quick loopback-only demo, see the root [README](../README.md).

---

## 1. Architecture Overview

Projektakte runs as two containers built from the same image (`compose.yaml`):

| Service | Command | Role |
| --- | --- | --- |
| `web` | `python serve.py --host 0.0.0.0 --port 8000` | Waitress WSGI server + Django + WhiteNoise static serving |
| `worker` | `python manage.py worker` | Single serial background worker (OCR sync, Gemini extraction, thumbnails, IMAP import, exports, backups, demo cleanup) |

- **Storage:** SQLite (`/data/db.sqlite3`) in `IMMEDIATE` transaction mode, coordinated across `web` and `worker` by a shared OS file lock (`akte/operations.py`). Media, backups, and feedback live under `/data/`.
- **Container hardening:** Both services run as unprivileged UID/GID `10001`, with `read_only: true`, `cap_drop: [ALL]`, `no-new-privileges:true`, and a 64 MiB `/tmp` tmpfs. Neither container needs Docker socket access.
- **Browser performance:** WhiteNoise serves versioned static assets with a 1-year cache (`WHITENOISE_MAX_AGE = 31536000`), `GZipMiddleware` compresses HTML responses, and `static/instant-shell.js` keeps the app shell and recently visited read-only views in volatile tab RAM (never persisted to disk, cleared immediately on any form submit or mutation).

---

## 2. Production Installation (Linux / Raspberry Pi)

1. **Prepare the directory and configuration:**
   Copy `.env.example` to `.env` in your deployment directory. Choose a dedicated empty `APP_DATA_DIR` for a new installation and make it writable by container UID/GID `10001`:

   ```sh
   cp .env.example .env
   sudo install -d -m 0700 -o 10001 -g 10001 ./data
   ```

2. **Configure production secrets and security flags in `.env`:**
   - Set `DEMO_MODE=0`, `DEBUG=0`, and `HTTPS=1`.
   - Generate a strong random Django secret key:
     ```sh
     python3 -c "import secrets; print(secrets.token_urlsafe(64))"
     ```
   - Provide secrets either directly in `.env` or via mounted files (`SECRET_KEY_FILE`, `PAPERLESS_TOKEN_FILE`, `GEMINI_API_KEY_FILE`)—never set both forms for the same secret. See [deployment/README.md](../deployment/README.md) and `deployment/compose-pi.example.yaml` for read-only `./secrets:/run/secrets:ro` mounts and CPU/PID limits.

3. **Configure reverse proxy and TLS:**
   - Terminate TLS at your own reverse proxy (Caddy, Nginx, Traefik, Cloudflare Tunnel, etc.).
   - By default, `compose.yaml` binds only to `127.0.0.1:${APP_PORT:-8010}:8000` on the host.
   - Replace the example domain and proxy placeholder in `.env`:

     ```dotenv
     PUBLIC_HOST=projektakte.example.com
     ALLOWED_HOSTS=projektakte.example.com,127.0.0.1
     CSRF_TRUSTED_ORIGINS=https://projektakte.example.com
     TRUSTED_PROXY=<proxy-ip-or-dns-name>
     ```

   - Keep `127.0.0.1` in `ALLOWED_HOSTS`—the internal container healthcheck uses it.
   - `TRUSTED_PROXY` must match the actual source IP or DNS name seen by the `web` container; only that proxy is trusted for forwarded scheme/client headers. Never use wildcard hosts.

4. **Build, migrate, create the first account, and start:**

   ```sh
   docker compose config --quiet
   docker compose build
   docker compose run --rm --no-deps web python manage.py migrate --noinput
   docker compose run --rm --no-deps web python manage.py check
   docker compose run --rm --no-deps web python manage.py createsuperuser
   docker compose up -d
   docker compose ps
   ```

   *(Add `-f compose.yaml -f deployment/compose-pi.example.yaml` to each `docker compose` command if using the Raspberry Pi override.)*

   Do **not** run `seed_demo` on a production database.

---

## 3. Optional Integrations

- **Paperless-ngx:** Set `PAPERLESS_URL` (reachable from the `worker` container) and `PAPERLESS_TOKEN` (or `PAPERLESS_TOKEN_FILE`). Paperless remains an independent system with its own database and backup lifecycle.
- **Google Gemini:** Set `AI_ENABLED=1`, `GEMINI_MODEL`, `GEMINI_API_KEY` (or `GEMINI_API_KEY_FILE`), and review the token pricing/budget guardrails (`AI_MONTHLY_WARN`, `AI_MONTHLY_LIMIT`, `AI_INPUT_USD_PER_M`, `AI_OUTPUT_USD_PER_M`, `AI_REQUEST_RESERVE`, `GEMINI_ORIGINAL_MAX_MB`). Set `AI_ENABLED=0` to run completely without AI.
- **IMAP Mail Import:** Configure `MAIL_IMAP_HOST`, `MAIL_IMAP_USER`, `MAIL_IMAP_PASSWORD`, `MAIL_ALLOWED_SENDER`, and `MAIL_CAPTURE_USER`. Leave empty to disable mail polling.
- **Repair Library:** Operator-owned workshop manuals and media stay strictly in runtime data outside version control.

---

## 4. Security Model

- **Default-deny auth boundary:** All routes except `/login/`, `/manifest.json`, `/sw.js` (and the isolated `/demo/` boundary when enabled) require authentication. `/admin/` returns `404` for non-staff users.
- **Pre-auth protection:** Oversized (> 64 KiB) or chunked login bodies are rejected before session parsing; IP-based login rate limiting runs in memory without sleeping worker threads.
- **Transport & cookie hardening:** `HttpOnly`, `Secure`, `SameSite=Lax` cookies, HSTS (`1 year`, `includeSubDomains`, `preload`), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, and `Cache-Control: no-store, private` on dynamic HTML.
- **Safe file & Markdown handling:** Streamed user files receive a sandbox CSP (`sandbox; default-src 'none'`); active formats (`.html`, `.svg`, `.js`, etc.) are forced to `application/octet-stream` downloads. User Markdown is sanitized through `nh3` (Rust).

---

## 5. Backups & Verified Restore

### Creating a Consistent Backup
- **In-app:** Trigger and download backups under *Einstellungen → System & Sicherung* (default retention keeps 10 managed backups; older backups are pruned only after a new backup succeeds).
- **CLI:**
  ```sh
  docker compose exec web python manage.py backup /data/backups/projektakte-backup.zip
  ```
  If Paperless-ngx is connected, you can bundle a verified Paperless archive via `--paperless-bundle /path/to/paperless-bundle.zip`.

Each backup acquires the shared OS write gate, runs `sqlite3.Connection.backup()` + `PRAGMA integrity_check`, stores already-compressed media with `ZIP_STORED` (low CPU/RAM overhead on Pi 4), writes a SHA-256 `manifest.json`, and verifies the archive before replacing the destination atomically.

> **Important:** Back up your `.env`, secret files, Compose overrides, and external services (e.g. Paperless-ngx) separately, and copy archives off the host disk to protect against storage failure.

### Verifying & Restoring a Backup
Always test restores into a **separate empty directory** first—never directly over your live data directory:

```sh
docker compose run --rm --no-deps web python manage.py restore /data/backups/projektakte-backup.zip /tmp/restore-test
```

The command validates paths, SHA-256 checksums, and SQLite integrity. Point a test instance's `DATA_DIR` at the restored directory and verify projects, media, receipt originals, and costs before cutting over.

---

## 6. Routine Operations & Updates

- **Check service health & logs:**
  ```sh
  docker compose ps
  docker compose logs --tail 50 web worker
  ```
- **Update procedure:**
  1. Note your current version and ensure you have a fresh verified backup (plus external service backups if applicable).
  2. Pull/copy the new release code.
  3. Rebuild the image (`docker compose build`)—this automatically runs `collectstatic` inside the image.
  4. Apply schema migrations if needed:
     ```sh
     docker compose run --rm --no-deps web python manage.py migrate --noinput
     docker compose run --rm --no-deps web python manage.py check
     ```
  5. Recreate and start services (`docker compose up -d`) and verify `docker compose ps` reports both `web` and `worker` as `healthy`.
- **Maintenance commands:**
  - `python manage.py cleanup` — previews orphan/temporary cleanup before applying.
  - `python manage.py rebuild_search` — rebuilds the SQLite FTS search index if needed.
- **Public Demo (`/demo/`):** Isolated via `DemoBoundaryMiddleware` and `DemoRouter` onto a separate `demo.sqlite3` database with memory-only uploads (max 2 MiB), no external API calls, and automatic cleanup of expired visitor sessions.
