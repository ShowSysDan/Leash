# Changelog — Leash NDI Source Control

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [1.0.0] — 2026-04-24

First stable release. Full replacement for the QSYS Lua NDI routing script.

### Added

#### Core
- Flask application factory with SQLite (dev) and PostgreSQL (production) support
- Auto-migrations on startup — no manual `flask db migrate / upgrade` needed
- Version number (`app/__version__.py`) shown in navbar and available at `GET /api/version`
- Syslog audit logging (`leash.audit` logger, LOCAL0 facility) for all source
  changes, discoveries, scan results, and device errors, each tagged with a
  `via=` field (ui / group / snapshot / v1 / enforcement)

#### Receiver Management
- Subnet scanner: concurrently probes all 254 addresses on the configured
  prefix, identifies BirdDog PLAY devices via `/about`, auto-creates receiver
  records with hostname, firmware, serial number, and network info
- Dashboard grid showing live status (online / offline), current NDI source,
  and a per-receiver source selector
- Offline receivers are dimmed with a last-seen timestamp and removable with
  one click
- Per-receiver detail page with tabbed settings covering all 21 BirdDog
  settings groups (Decode, Transport, Audio, PTZ, Picture, Exposure,
  White Balance, Gamma, Silicon2, etc.)
- Reboot and Restart Video actions from the detail page

#### Sources
- NDI source discovery via `/reset` + `/List` on any online receiver
- Sources persist in the database with a **stable 1-based index** that never
  changes even if the source goes offline — external systems can address by
  number indefinitely
- Source registry page shows all sources (online + offline) with index badge
- Only currently-online sources appear in receiver dropdowns

#### Groups / Tags
- Create named groups with a colour label
- Add or remove receivers from groups via a member picker modal
- Send the same NDI source to all online receivers in a group concurrently

#### Layouts
- Spatial canvas pages (16:9 aspect ratio) matching physical room floor plans
- Drag-and-drop receiver cards in Edit mode; positions stored as percentages
  so they scale to any screen size
- View mode: click a card's source dropdown to route directly from the map
- 30-second auto-poll keeps status current while a layout page is open

#### Snapshots
- Capture the current NDI source assignment for all (or selected) receivers
- Preview snapshot entries before recalling — shows saved source vs. current
- Recall applies all sources concurrently across all devices
- Per-recall progress modal

#### Scheduled Recalls
- Cron-like schedule: select days of the week and a local-server time
- APScheduler BackgroundScheduler checks every minute and fires due schedules
- Recall honours `RECALL_CONCURRENCY` (default 10) so devices are batched,
  not all contacted simultaneously
- Enable / disable toggle without deleting the schedule

#### Persistent Enforcement
- Mark a schedule as **Persistent** with an N-minute window (default 60)
- After firing, a second scheduler job polls receivers every `ENFORCEMENT_INTERVAL`
  seconds (default 60) using a lightweight single-call source check
- Source drift (receiver changed by any means) is detected and corrected
  automatically within one poll cycle
- Receivers that were offline when the schedule fired are corrected as soon
  as they come back online
- Multiple overlapping enforcement windows: most-recently-fired schedule wins
  per receiver
- **Stop** button cancels a window early; `DELETE /api/schedules/<id>/enforcement`

#### External Integration API (`/api/v1/`)
- Optional API key auth (`X-API-Key` header or `?api_key=` query param);
  leave `API_KEY` unset for open LAN access
- `GET /api/v1/sources` — all sources with stable indexes (online + offline)
- `GET /api/v1/sources/online` — currently-visible sources only
- `GET /api/v1/sources/<index>` — look up by stable index
- `GET /api/v1/receivers` — all receivers with current source and index
- `GET /api/v1/receivers/<octet>` — by IP last octet
- `POST /api/v1/route` — route one receiver; accepts index (int) or name (str)
- `POST /api/v1/route/bulk` — concurrent bulk routing
- Sample QSYS Lua integration script in `docs/qsys_integration.lua`

#### Service / Infrastructure
- systemd user service (`leash.service`) with:
  - `--workers 1 --threads 4` (required — one scheduler instance only)
  - `Restart=always`, `RestartSec=10s`
  - `StartLimitBurst=10 / StartLimitIntervalSec=300`
  - `StartLimitAction=none` — retries indefinitely, never gives up

### Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `FLASK_ENV` | `development` | `development` or `production` |
| `SECRET_KEY` | insecure default | Flask session secret — change in production |
| `DATABASE_URL` | SQLite in project dir | Full database URI |
| `NDI_SUBNET_PREFIX` | `10.1.248.` | Subnet prefix to scan |
| `NDI_DEVICE_PORT` | `8080` | BirdDog HTTP API port |
| `NDI_DEVICE_PASSWORD` | `birddog` | BirdDog device password |
| `HTTP_TIMEOUT` | `5` | Per-device request timeout (seconds) |
| `RECALL_CONCURRENCY` | `10` | Max simultaneous device contacts during recalls |
| `ENFORCEMENT_INTERVAL` | `60` | Enforcement poll cadence (seconds) |
| `API_KEY` | _(unset)_ | External API key; unset = open access |
