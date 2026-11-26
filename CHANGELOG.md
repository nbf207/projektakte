# Changelog

All notable changes to **Projektakte** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- Full English UI localization (i18n) planned for a future release.

## [0.8.0] - 2026-11-26

### Added
- **CI test gate (`.github/workflows/ci.yml`):** Automated `manage.py check --deploy --fail-level WARNING` and full Django test suite on pushes to `main` and pull requests, plus a blocking `test` job before Docker publishing in `.github/workflows/docker-publish.yml`.
- **HTML Content-Security-Policy:** Strict `Content-Security-Policy` (`script-src 'self'`, `frame-ancestors 'none'`, `object-src 'none'`) on all HTML responses while keeping `sandbox; default-src 'none'` on streaming downloads.
- **Integration status overview:** `/einstellungen/anbindungen/` now clearly shows active/disabled status for Gemini AI, Paperless-ngx, and IMAP and hides inactive action buttons in favor of calm `.env` setup hints.
- **Operations & disaster recovery docs:** Added fresh-host restore from full ZIP backup and forgotten admin password reset (`changepassword`) to `docs/SELF-HOSTING.md`, plus standard OCI labels in `Dockerfile`.
- **Rate-limiting on `/setup/`:** Extended `PreAuthRateMiddleware` to throttle the one-time onboarding endpoint.

### Changed
- **Self-hosting feedback default:** Added `FEEDBACK_WIDGET_ENABLED` (default `0`), hiding the floating feedback button and Feedback settings tab and returning `404` for feedback endpoints unless explicitly enabled.
- **Strict Gemini model allowlist (`ALLOWED_GEMINI_MODELS`):** Centralized supported models (`gemini-3.5-flash-lite`, `gemini-3.8-flash`) across `config/settings.py` (fail-fast startup check) and `akte/preferences.py` (safe fallback for legacy DB values).
- **CSP-clean handover print button:** Extracted inline `onclick="window.print()"` from `templates/handover.html` into `static/handover.js`.
- **Theme toggle accessibility:** Synced dynamic `aria-label` on `#theme-toggle` in `static/theme.js`.

### Fixed
- **Review draft empty project crash:** Fixed a `UUID` `ValidationError` in `ReviewForm` when reopening a saved review draft with an empty `chronicle_project`.
- **Full test suite modernization:** Cleaned up legacy test mocks (`bounded_request`, `BoundedIMAP`, `LivePaperless`) and removed references to deprecated `0.7.11` routes so all 306 tests pass cleanly.

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
