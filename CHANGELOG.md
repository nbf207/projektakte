# Changelog

All notable changes to **Projektakte** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- Full English UI localization (i18n) planned for a future release.

## [0.7.30] - 2026-11-26

### Added
- **Multi-arch Docker publishing (`linux/amd64`, `linux/arm64`):** Automated GitHub Actions workflow publishing prebuilt images to `ghcr.io/nbf207/projektakte` with Buildx GHA caching and automated GitHub Releases.
- **One-time browser onboarding (`/setup/`):** Atomic, race-safe admin account creation when 0 users exist (enforcing Django password validators and permanently returning `404` once an account is created).
- **Startup safety & automatic pre-migration backups (`scripts/entrypoint.py`, `akte/updates.py`):**
  - Automatic atomic `/data/.secret-key` generation on first start if no explicit secret is configured.
  - SQLite `PRAGMA integrity_check` and downgrade guard (semantic version tuple check plus unknown-migration detection in `django_migrations`).
  - Automatic verified SQLite snapshot to `/data/backups/pre-migrate/` before applying pending migrations, with configurable retention (`PRE_MIGRATE_KEEP=5`).
  - One-command database rollback utility (`python manage.py rollback_db --list` / `--restore latest|<snapshot>`).
- **Mobile Capture-First workflow:** On smartphones, the quick-capture box opens immediately above the fold without scrolling, while navigation collapses into a sticky top-bar menu sheet.
- **Instant App-Shell (`static/instant-shell.js`):** Volatile in-memory tab cache with stale-while-revalidate background refresh and smooth View Transitions, tailored for low-power hardware like Raspberry Pi 4 (2 GB RAM).
- **English documentation & licensing:** Added English `README.md`, `docs/USER-GUIDE.md`, `docs/SELF-HOSTING.md`, demo screenshots, and GNU AGPL-3.0 `LICENSE`.

### Changed
- **iOS 27 Liquid Glass UI refresh:** Frosted translucent glass surfaces, continuous pointer proximity rim refraction, native CSS toggle switches, auto-growing textareas, and refined mobile layouts.
- **Default `compose.yaml`:** Switched to prebuilt GHCR images, named Docker volume `projektakte_data:/data`, and `depends_on: web: condition: service_healthy` for `worker` (with `deployment/compose-build.yaml` for local source builds).
- **HTML sanitization:** Replaced deprecated `bleach` with Rust-based `nh3`.
- **Standalone backups:** `python manage.py backup` no longer requires `--paperless-bundle` when Paperless-ngx is not configured.
- **Optional LAN HTTP mode:** Added explicit `ALLOW_INSECURE_HTTP=1` opt-in for private LAN/VPN deployments while keeping `HTTPS=1` as the default for public hosts.

### Removed
- Removed deprecated/unlinked legacy routes and unused middleware/templates without requiring database migrations.
