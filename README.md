# Leash — NDI Receiver Control

Flask/Python application for managing and controlling a network of BirdDog NDI
receivers (replacing a QSYS Lua script).  Supports up to 83 receivers on a
shared subnet, with SQLite for development and PostgreSQL for production.

---

## Features

- **Auto-scan** — concurrently probes all 254 addresses on the subnet and
  auto-detects BirdDog PLAY devices via `/about`.  Hostname, firmware version,
  serial number, and network info are captured on first contact and cached.
- **Dashboard** — grid view of all receivers showing live hostname, current NDI
  source, and online/offline status.  Offline devices are dimmed and can be
  removed with one click.
- **Source caching** — NDI sources are **stored in the database** so they
  remain available across sessions without re-running discovery.
- **Bulk reload** — concurrently polls all known receivers in parallel
  (asyncio + aiohttp).  Marks any that don't respond as offline.
- **Source discovery** — triggers `/reset` + `/List` on a reference device and
  merges results into the DB, preserving previously-seen sources.
- **Per-receiver settings** — tabbed settings page covering Decode, Transport,
  Audio, PTZ, Picture, Exposure, White Balance, Gamma, and more.
- **Full BirdDog REST API v2.0 coverage** — every documented endpoint is
  wrapped in `BirdDogClient`.

---

## Project Structure

```
Leash/
├── app/
│   ├── __init__.py          # Flask app factory
│   ├── models.py            # SQLAlchemy models (NDIReceiver, NDISource)
│   ├── routes/
│   │   ├── main.py          # HTML page routes
│   │   └── api.py           # JSON REST API
│   ├── services/
│   │   └── birddog_client.py  # Async BirdDog HTTP client
│   ├── static/
│   │   ├── css/style.css
│   │   └── js/main.js       # Dashboard JS
│   │   └── js/receiver_detail.js
│   └── templates/
│       ├── base.html
│       ├── index.html       # Receiver dashboard
│       ├── receiver_detail.html
│       ├── sources.html
│       └── partials/
├── migrations/              # Flask-Migrate / Alembic
├── config.py                # Dev / Production config
├── run.py                   # Dev entry point
├── requirements.txt
├── leash.service            # systemd service template
└── .env.example
```

---

## Quick Start (Development)

### 1. Clone & set up virtualenv

```bash
cd ~
git clone <repo-url> Leash
cd Leash

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — at minimum set SECRET_KEY
# Leave DATABASE_URL commented out to use SQLite
```

### 3. Initialise the database

```bash
source venv/bin/activate
export FLASK_APP=run.py
flask db init        # only needed once
flask db migrate -m "initial"
flask db upgrade
```

### 4. Run development server

```bash
python run.py
```

Open `http://localhost:5000` in your browser.

---

## Running as a systemd Service (Production)

### 1. Prepare the environment

```bash
cd ~/Leash
cp .env.example .env
# Edit .env: set FLASK_ENV=production, DATABASE_URL, SECRET_KEY

source venv/bin/activate
export FLASK_APP=run.py
flask db upgrade
deactivate
```

### 2. Install the service

The service file uses `%i` (instance name) and `%h` (home directory)
specifiers, so the service must be installed as a user service **or** with the
instance name set to your username.

```bash
# Copy to systemd user services
mkdir -p ~/.config/systemd/user
cp ~/Leash/leash.service ~/.config/systemd/user/leash.service

systemctl --user daemon-reload
systemctl --user enable leash
systemctl --user start leash
systemctl --user status leash
```

To start automatically at boot without needing to log in:

```bash
sudo loginctl enable-linger $USER
```

### 3. Check logs

```bash
journalctl --user -u leash -f
```

---

## Switching to PostgreSQL

1. Install PostgreSQL and create a database:

   ```bash
   sudo -u postgres psql
   CREATE USER leash WITH PASSWORD 'your-password';
   CREATE DATABASE leash OWNER leash;
   \q
   ```

2. Update `.env`:

   ```
   DATABASE_URL=postgresql://leash:your-password@localhost/leash
   ```

3. Run migrations:

   ```bash
   source venv/bin/activate
   export FLASK_APP=run.py
   flask db upgrade
   ```

`psycopg2-binary` is already in `requirements.txt`.

---

## Auto-Scan Workflow

1. Click **Scan Network** in the toolbar (or `POST /api/scan`).
2. Leash concurrently probes `10.1.248.1` → `10.1.248.254` (configurable range).
3. Any device whose `/about` response contains `"HardwareVersion": "BirdDog PLAY"` is upserted.
4. Hostname, firmware, serial, and network info are cached immediately.
5. Any receiver already in the DB that **didn't** respond is marked **offline**.
6. Offline receivers can be removed individually via the trash button, or will
   reappear as online on the next scan if they come back.

Polling (`Reload All`) refreshes `/hostname` and `/connectTo` on every known
receiver, keeping hostnames current even after a player moves to a new location.

---

## API Reference

### Scan

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/scan` | Scan subnet, upsert found BirdDog PLAY devices, mark missing as offline |

Body (optional): `{"start": 1, "end": 254}` to limit scan range.

### Receivers

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/receivers` | List all receivers |
| POST | `/api/receivers` | Add a receiver `{"index":1,"ip_last_octet":"168","label":"..."}` |
| GET | `/api/receivers/<id>` | Get one receiver |
| PUT | `/api/receivers/<id>` | Update label / IP octet |
| DELETE | `/api/receivers/<id>` | Remove receiver |
| GET | `/api/receivers/bulk-reload` | Concurrent status refresh for all receivers |
| GET | `/api/receivers/<id>/status` | Poll live status from device |
| POST | `/api/receivers/<id>/source` | Set NDI source `{"source_name":"..."}` |
| POST | `/api/receivers/<id>/reboot` | Reboot device |
| POST | `/api/receivers/<id>/restart` | Restart video subsystem |
| GET | `/api/receivers/<id>/settings/<group>` | Get settings group |
| POST | `/api/receivers/<id>/settings/<group>` | Apply settings group |

**Settings groups:** `decode_setup`, `decode_transport`, `decode_status`,
`encode_setup`, `encode_transport`, `analog_audio`, `operation_mode`,
`video_output`, `ptz`, `exposure`, `white_balance`, `picture`, `colour_matrix`,
`advanced`, `external`, `detail`, `gamma`, `sil2_codec`, `sil2_enc`,
`ndi_discovery`, `ndi_group`, `ndi_offsubnet`

### Sources

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/sources` | List cached sources |
| POST | `/api/sources/discover` | Run `/reset` + `/List` and cache results |
| DELETE | `/api/sources/<id>` | Remove a cached source |

---

## BirdDog Device Firmware Notes

Some older BirdDog firmware uses different endpoint capitalisation
(e.g. `/ConnectTo` vs `/connectTo`, `/HostName` vs `/hostname`).  If a device
returns 404 on standard paths, instantiate `BirdDogClient` with
`legacy_paths=True`.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FLASK_ENV` | `development` | `development` or `production` |
| `SECRET_KEY` | (insecure default) | Flask session secret — **change in production** |
| `DATABASE_URL` | SQLite in project dir | Full DB URI |
| `NDI_SUBNET_PREFIX` | `10.1.248.` | Fixed IP prefix for all receivers |
| `NDI_DEVICE_PORT` | `8080` | BirdDog HTTP API port |
| `NDI_DEVICE_PASSWORD` | `birddog` | BirdDog device password |
| `NDI_MAX_RECEIVERS` | `83` | Maximum receiver count |
| `HTTP_TIMEOUT` | `5` | Per-request timeout in seconds |
