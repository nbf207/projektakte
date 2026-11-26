# Projektakte

**Capture on the go in seconds, plan and verify calmly at your desk.**

Projektakte is a self-hosted project logbook for vehicles, home projects, repairs, and long-running builds. On your phone, you throw in a quick text note, photo, PDF, or voice memo without stopping what you are doing; optional AI and OCR pre-structure the draft in the background, and later on desktop you review and confirm every cost or entry following a strict **suggestions instead of blind auto-booking** (human-in-the-loop) workflow.

> **Language note:** The web interface is currently in **German**. Full English localization (i18n) is planned for a future release. All repository documentation, Docker templates, and configuration guides are in English.

---

## Key Features

- **Mobile Capture-First:** On smartphones, the quick-capture box (text, microphone recording, camera photo, file upload, optional project auto-detection) opens immediately in the first viewport without scrolling; navigation stays tucked into the top bar until needed.
- **Projects & Chronicle:** Structured timelines per project with categories, custom fields, work steps, annotated before/after photos, optimistic locking, and reversible trash.
- **Parts & Maintenance Tracking:** Track spare parts separately from actual installation events (buying a part does not auto-log an installation), plus recurring maintenance schedules based on time intervals and/or meter readings (e.g. km, operating hours).
- **Receipt Inbox & Human-in-the-Loop AI:** Review invoices and receipts side-by-side with the original PDF/image and OCR text, split a single receipt across multiple projects or entries, and confirm once without double-counting. Optional integrations for **Paperless-ngx**, **Google Gemini**, and **IMAP** mail import.
- **Knowledge Base & Repair Library:** Store notes, torque specs, chunked work videos, and structured operator-supplied repair manuals with search.
- **Exports & Verified Backups:** Generate compact or photo-rich PDF reports and portable ZIP exports, alongside consistent SQLite + media backups with SHA-256 manifests.

---

## Screenshots

<!-- Drop your demo screenshots into docs/screenshots/ using the filenames below. -->

| Mobile Capture-First | Desktop Workspace |
| --- | --- |
| ![Mobile Capture-First view](docs/screenshots/mobile-capture.png) | ![Desktop project overview](docs/screenshots/desktop-overview.png) |

---

## Architecture at a Glance

Projektakte is intentionally built as a lean, self-contained **Django monolith** designed for data sovereignty and modest hardware such as a **Raspberry Pi 4 (2 GB RAM)**:

- **Stack:** Python 3.14, Django 5.2, SQLite (`IMMEDIATE` transactions + shared OS write gate), Waitress WSGI, WhiteNoise, `nh3` (Rust HTML sanitizer), and HTMX.
- **No JS build pipeline:** Plain CSS and vanilla JavaScript—no Node.js, Webpack, or SPA bundler required to build or run.
- **Two processes, one image:** `web` serves HTTP/HTML and static assets; a single serial `worker` handles background jobs (OCR sync, AI extraction, thumbnails, IMAP polling, exports, backups) via database leases.
- **Fast on low-power hardware:** GZip compression, immutable static caching, and a volatile in-memory browser tab shell (`instant-shell.js`) keep page transitions snappy without storing your database in the browser.

---

## Quickstart with Docker Compose

### 1. Local Demo (Loopback Only)

Create a local `.env` from the provided template. The defaults run in `DEMO_MODE=1` on `127.0.0.1` with synthetic sample data:

```sh
cp .env.example .env
```

*(PowerShell: `Copy-Item .env.example .env`)*

On Linux, prepare the data directory so the unprivileged container user (`UID/GID 10001`) can write to it, then build, migrate, seed the demo dataset, and start:

```sh
sudo install -d -m 0700 -o 10001 -g 10001 ./data
docker compose build
docker compose run --rm --no-deps web python manage.py migrate --noinput
docker compose run --rm --no-deps web python manage.py seed_demo
docker compose up -d
docker compose ps
```

Open [http://127.0.0.1:8010/](http://127.0.0.1:8010/) and sign in with the local synthetic demo account:
- **Username:** `demo`
- **Password:** `Projektakte-Demo-2026!`

### 2. Production Self-Hosting (Linux / Raspberry Pi)

For real data, use a fresh empty `APP_DATA_DIR`, set `DEMO_MODE=0`, `DEBUG=0`, `HTTPS=1`, a strong random `SECRET_KEY`, and your reverse-proxy host settings in `.env`. On low-memory hosts or when mounting secret files from `./secrets`, combine the root `compose.yaml` with the optional Pi override:

```sh
docker compose -f compose.yaml -f deployment/compose-pi.example.yaml config --quiet
docker compose -f compose.yaml -f deployment/compose-pi.example.yaml build
docker compose -f compose.yaml -f deployment/compose-pi.example.yaml run --rm --no-deps web python manage.py migrate --noinput
docker compose -f compose.yaml -f deployment/compose-pi.example.yaml run --rm --no-deps web python manage.py createsuperuser
docker compose -f compose.yaml -f deployment/compose-pi.example.yaml up -d
```

Do **not** run `seed_demo` on a production database. See the full [Self-Hosting & Operations Guide](docs/SELF-HOSTING.md) for reverse-proxy/TLS setup, Paperless-ngx, Gemini, IMAP, and verified backup/restore procedures.

---

## Documentation

- **[User Guide](docs/USER-GUIDE.md)** — Daily workflows: quick capture, chronicle, receipt review, maintenance schedules, parts, knowledge base, PWA/Android sharing, and exports.
- **[Self-Hosting & Operations Guide](docs/SELF-HOSTING.md)** — Production Docker deployment, Raspberry Pi tuning, reverse proxy & security model, integrations, backups, restore verification, and updates.
- **[Optional Deployment Templates](deployment/README.md)** — Raspberry Pi resource limits, secret file mounts, and optional update-guard examples.

---

## Development Note

Large parts of this project—including architecture iterations, backend features, security hardening, and UI polish—were built with AI coding assistance, primarily using **6.1 Sol**, **6 Astra**, **6.2 Sol**, and **4 Neon**. Core workflows were tested on a self-hosted Raspberry Pi 4.

---

## License

Projektakte is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**. See [LICENSE](LICENSE) for the full license text.
