# Leash — NDI Source Control

Flask/Python application that centrally controls a network of **BirdDog NDI PLAY** receivers.
Replaces a QSYS Lua script as the single routing control point for up to 254 receivers on a shared subnet.
SQLite for development, PostgreSQL-ready for production.

Current version: **1.0.0**

---

## Features

| Feature | Summary |
|---|---|
| **Auto-scan** | Probes the full subnet concurrently, identifies BirdDog PLAY devices via `/about`, auto-creates receiver records |
| **Dashboard** | Grid of all receivers — live status, hostname, current source, inline source selector |
| **Source registry** | NDI sources persist in the DB with a **stable index** that never changes, even when a source goes offline |
| **Groups** | Tag receivers into named groups; send one source to all members at once |
| **Layouts** | Drag-and-drop spatial canvas pages that mirror your physical room floor plan |
| **Snapshots** | Save and recall routing state — full or partial (any subset of receivers) |
| **Schedules** | Cron-like automation — recall a snapshot on selected days at a set time |
| **Persistent enforcement** | After a scheduled recall, poll receivers and correct any source drift for a configurable window |
| **External API** | `/api/v1/` for QSYS, Crestron, AMX — route by stable index or source name |
| **Syslog audit log** | Every source change, discovery, scan, and device error written to syslog |
| **Auto-migrations** | DB schema is always current on startup — no manual migration commands needed |

---

## Project Structure

```
Leash/
├── app/
│   ├── __init__.py              # App factory — migrations, syslog, scheduler
│   ├── __version__.py           # Version string
│   ├── models.py                # All SQLAlchemy models
│   ├── routes/
│   │   ├── main.py              # HTML page routes
│   │   ├── api.py               # Internal REST API (/api/)
│   │   ├── groups_api.py        # Groups API
│   │   ├── layouts_api.py       # Layouts API
│   │   ├── snapshots_api.py     # Snapshots API
│   │   ├── schedules_api.py     # Schedules API
│   │   └── external_api.py      # External integration API (/api/v1/)
│   ├── services/
│   │   ├── birddog_client.py    # Async BirdDog REST client (full v2.0 coverage)
│   │   ├── scanner.py           # Subnet scanner
│   │   ├── scheduler.py         # APScheduler — recalls + enforcement
│   │   └── audit_log.py         # Structured syslog helpers
│   ├── static/
│   │   ├── css/style.css
│   │   └── js/
│   │       ├── main.js          # Shared: toast, scan, discover, reload
│   │       ├── groups.js
│   │       ├── layout.js
│   │       ├── snapshots.js
│   │       ├── schedules.js
│   │       └── receiver_detail.js
│   └── templates/
│       ├── base.html
│       ├── index.html
│       ├── receiver_detail.html
│       ├── groups.html
│       ├── layouts.html
│       ├── layout_view.html
│       ├── snapshots.html
│       ├── schedules.html
│       ├── sources.html
│       └── partials/
├── docs/
│   └── qsys_integration.lua     # Sample QSYS Lua integration script
├── migrations/                  # Alembic migration files (auto-managed)
├── config.py                    # Dev / Production config classes
├── run.py                       # Dev entry point
├── requirements.txt
├── leash.service                # systemd unit reference (written by install.sh)
├── install.sh                   # production install: service + systemctl
├── .env.example
├── CHANGELOG.md
└── README.md
```

---

## Quick Start (Development)

### 1. Clone and create a virtualenv

```bash
cd ~
git clone <repo-url> Leash
cd Leash

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env — at minimum set SECRET_KEY
# Leave DATABASE_URL commented out to use SQLite
```

### 3. Run

```bash
python run.py
```

Open `http://localhost:5000`.

> **No manual DB setup needed.** On first run, Leash automatically initialises
> the migrations directory, generates the initial schema migration, and applies
> it.  On subsequent runs it only applies pending migrations.

---

## Production Deployment (systemd)

### 1. Prepare

```bash
cd ~/Leash
cp .env.example .env
# Set FLASK_ENV=production, DATABASE_URL (PostgreSQL), and SECRET_KEY
```

### 2. Install the service

```bash
sudo bash ~/Leash/install.sh
```

The script writes a concrete `/etc/systemd/system/leash.service` with the correct user and paths, then enables and starts the service.

### 3. Logs

```bash
# Service logs (stdout/stderr from gunicorn)
sudo journalctl -u leash -f

# Audit log (source changes, discovery events, errors)
journalctl -t leash -f
# or
grep 'leash' /var/log/syslog | tail -50
```

### Gunicorn worker note

The service runs `--workers 1 --threads 4`.  **Do not increase workers.**
The background scheduler (APScheduler) lives inside the worker process —
multiple workers would fire every scheduled recall N times and run N
parallel enforcement pollers.  Threads handle HTTP concurrency instead.

---

## PostgreSQL

```bash
sudo -u postgres psql
CREATE USER leash WITH PASSWORD 'your-password';
CREATE DATABASE leash OWNER leash;
\q
```

Update `.env`:

```
DATABASE_URL=postgresql://leash:your-password@localhost/leash
```

`psycopg2-binary` is already in `requirements.txt`.  Auto-migration handles
the schema on first startup.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `FLASK_ENV` | `development` | `development` or `production` |
| `SECRET_KEY` | insecure default | Flask session secret — **change in production** |
| `DATABASE_URL` | SQLite in project dir | Full database URI |
| `NDI_SUBNET_PREFIX` | `10.1.248.` | Fixed IP prefix for all receivers |
| `NDI_DEVICE_PORT` | `8080` | BirdDog HTTP API port |
| `NDI_DEVICE_PASSWORD` | `birddog` | BirdDog device password |
| `HTTP_TIMEOUT` | `5` | Per-device request timeout (seconds) |
| `RECALL_CONCURRENCY` | `10` | Max simultaneous device contacts during recalls and enforcement corrections |
| `ENFORCEMENT_INTERVAL` | `60` | How often (seconds) the enforcement poller checks active persistence windows |
| `API_KEY` | _(unset)_ | External API key for `/api/v1/`; leave unset for open LAN access |

---

## Scheduled Recalls and Persistent Enforcement

Schedules are managed at **Schedules** in the navbar.

1. Create a schedule: pick a snapshot, select days, set a time (local server time, 24-hour).
2. Enable **Persistent enforcement** and set a window (e.g. 60 minutes).

When a persistent schedule fires:

- The snapshot is recalled immediately (receivers batched at `RECALL_CONCURRENCY` at a time).
- `enforcing_until` is set to `now + window`.
- Every `ENFORCEMENT_INTERVAL` seconds, Leash polls every receiver in the snapshot
  via a lightweight `/connectTo` call and corrects any source drift.
- Receivers that were **offline** when the schedule fired are corrected automatically
  when they come back online during the window.
- **Multiple overlapping windows** on the same receiver: most-recently-fired schedule wins.
- Click **Stop** in the table or call `DELETE /api/schedules/<id>/enforcement` to end early.

Tune `RECALL_CONCURRENCY` and `ENFORCEMENT_INTERVAL` in `.env` to match your network size.

---

## API Reference

### Internal API (`/api/`)

#### Version

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/version` | Returns `{"version": "1.0.0"}` |

#### Subnet Scan

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/scan` | Scan subnet; upsert found BirdDog PLAY devices; mark missing offline |

Body (optional): `{"start": 1, "end": 254}`

#### Receivers

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/receivers` | List all |
| POST | `/api/receivers` | Add `{"ip_last_octet":"83","label":"..."}` |
| GET | `/api/receivers/<id>` | Get one |
| PUT | `/api/receivers/<id>` | Update label / IP octet |
| DELETE | `/api/receivers/<id>` | Remove |
| GET | `/api/receivers/bulk-reload` | Concurrent status refresh for all |
| GET | `/api/receivers/<id>/status` | Poll live status from device |
| POST | `/api/receivers/<id>/source` | Set NDI source `{"source_name":"..."}` |
| POST | `/api/receivers/<id>/reboot` | Reboot device |
| POST | `/api/receivers/<id>/restart` | Restart video subsystem |
| GET | `/api/receivers/<id>/settings/<group>` | Read a settings group |
| POST | `/api/receivers/<id>/settings/<group>` | Write a settings group |

**Settings groups:** `decode_setup`, `decode_transport`, `decode_status`,
`encode_setup`, `encode_transport`, `analog_audio`, `operation_mode`,
`video_output`, `ptz`, `exposure`, `white_balance`, `picture`, `colour_matrix`,
`advanced`, `external`, `detail`, `gamma`, `sil2_codec`, `sil2_enc`,
`ndi_discovery`, `ndi_group`, `ndi_offsubnet`

#### Sources

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/sources` | List all cached sources |
| POST | `/api/sources/discover` | Run discovery on a reference device and merge results |
| DELETE | `/api/sources/<id>` | Remove a source from the registry |

#### Groups

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/groups` | List all groups |
| POST | `/api/groups` | Create `{"name":"...","color":"#hex","description":"..."}` |
| GET | `/api/groups/<id>` | Get group with members |
| PUT | `/api/groups/<id>` | Update name / color / description |
| DELETE | `/api/groups/<id>` | Delete group |
| POST | `/api/groups/<id>/receivers` | Add members `{"receiver_ids":[1,2]}` |
| DELETE | `/api/groups/<id>/receivers` | Remove members `{"receiver_ids":[1,2]}` |
| POST | `/api/groups/<id>/source` | Send source to all online members `{"source_name":"..."}` |

#### Layouts

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/layouts` | List all layouts |
| POST | `/api/layouts` | Create `{"name":"...","bg_color":"#hex"}` |
| GET | `/api/layouts/<id>` | Get layout with positions |
| DELETE | `/api/layouts/<id>` | Delete layout |
| PUT | `/api/layouts/<id>/positions` | Replace all positions `[{"receiver_id":1,"x_pct":10,"y_pct":20},...]` |
| POST | `/api/layouts/<id>/receivers` | Add receiver to layout |
| DELETE | `/api/layouts/<id>/receivers/<receiver_id>` | Remove receiver from layout |

#### Snapshots

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/snapshots` | List all |
| POST | `/api/snapshots` | Capture `{"name":"...","receiver_ids":[1,2]}` — omit `receiver_ids` to capture all receivers |
| GET | `/api/snapshots/<id>` | Get snapshot with all entries |
| POST | `/api/snapshots/<id>/recall` | Recall — pass `{"receiver_ids":[1,2]}` to restore only a subset, omit for all |
| DELETE | `/api/snapshots/<id>` | Delete |

#### Schedules

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/schedules` | List all |
| POST | `/api/schedules` | Create (see body below) |
| GET | `/api/schedules/<id>` | Get one |
| PUT | `/api/schedules/<id>` | Update |
| DELETE | `/api/schedules/<id>` | Delete |
| PATCH | `/api/schedules/<id>/toggle` | Flip enabled flag |
| DELETE | `/api/schedules/<id>/enforcement` | Stop an active enforcement window early |

Schedule body:

```json
{
  "name": "Morning show",
  "snapshot_id": 3,
  "days_of_week": "0,1,2,3,4",
  "time_of_day": "08:30",
  "enabled": true,
  "persistent": true,
  "persist_minutes": 60
}
```

`days_of_week`: comma-separated integers, 0 = Monday … 6 = Sunday.
`time_of_day`: HH:MM, **local server time**, 24-hour.

---

### External Integration API (`/api/v1/`)

Designed for QSYS, Crestron, AMX, or any HTTP client.

**Authentication:** If `API_KEY` is set in `.env`, include it as:
- Header: `X-API-Key: <key>`
- Query string: `?api_key=<key>`

Leave `API_KEY` unset for unauthenticated access on a trusted LAN.

#### Sources

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/sources` | All sources — online and offline — with stable indexes |
| GET | `/api/v1/sources/online` | Currently-visible sources only |
| GET | `/api/v1/sources/<index>` | Look up by stable index |

#### Receivers

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/receivers` | All receivers with current source and source index |
| GET | `/api/v1/receivers/<octet>` | One receiver by IP last octet |

#### Routing

| Method | Endpoint | Body | Description |
|---|---|---|---|
| POST | `/api/v1/route` | `{"ip_octet":"83","source":4}` | Route one receiver |
| POST | `/api/v1/route/bulk` | `[{"ip_octet":"83","source":4},...]` | Route many concurrently |

`source` can be a **stable integer index** or a **source name string**.

Sample QSYS Lua script: `docs/qsys_integration.lua`

---

## Syslog Audit Log

All operational events are written to syslog (LOCAL0 facility, program tag `leash`).
The socket is auto-detected: `/dev/log` (Linux) or `/var/run/syslog` (macOS).

Log levels:

| Level | Events |
|---|---|
| INFO | Source routed successfully, scan complete, new receiver found, NDI source discovered, snapshot recalled, group send, schedule fired |
| WARNING | Source change failed (device rejected), receiver went offline, source went offline, enforcement drift detected |
| ERROR | Device communication failure (timeout, non-200 response) |

Each source-change entry includes a `via=` tag:

| Tag | Origin |
|---|---|
| `ui` | Receiver dashboard or detail page |
| `group:<name>` | Group bulk send |
| `snapshot:<name>` | Snapshot recall |
| `v1` | External API single route |
| `v1_bulk` | External API bulk route |
| `enforcement` | Persistent enforcement correction |

Filter Leash audit events:

```bash
journalctl -t leash -f
grep 'leash.audit' /var/log/syslog
```

---

## BirdDog Firmware Notes

Some older BirdDog firmware uses different endpoint capitalisation
(`/ConnectTo` instead of `/connectTo`, `/HostName` instead of `/hostname`).
If a device returns 404 on standard paths, instantiate `BirdDogClient` with
`legacy_paths=True`.

The subnet scanner identifies devices as BirdDog PLAY by checking that the
`HardwareVersion` field in the `/about` response contains `"BirdDog PLAY"`.
Other BirdDog models (encoders, PTZ cameras) are ignored by the scanner.
