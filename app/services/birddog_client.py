"""
Async HTTP client wrapping the BirdDog REST API v2.0.

All public methods return (status_code: int, data: Any).
status_code == 0 means a network/timeout error occurred.
"""
import asyncio
import json
import logging
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)

# BirdDog firmware v2 uses birddog-prefixed, lowercase paths for camera control.
# Older firmware uses /ptzControl and /focusControl without the prefix.
# Set legacy_paths=True on a client instance if the device uses the older paths.
LEGACY_PATH_MAP = {
    "/connectTo": "/ConnectTo",
    "/hostname": "/HostName",
    "/List": "/List",           # unchanged
    "/reset": "/reset",         # unchanged
    "/birddogptzcontrol":   "/ptzControl",
    "/birddogfocuscontrol": "/focusControl",
}


class BirdDogClient:
    """
    Async BirdDog REST client.

    Session reuse:
      The client can operate in two modes.  If an aiohttp.ClientSession is
      passed in (via the `session` kwarg or by entering the client as a
      context manager), all requests use that session — the efficient path
      for bulk operations.  Otherwise each call creates a short-lived
      session, fine for one-shot Flask-route usage.

      Bulk helpers (bulk_fetch_status, bulk_fetch_source, scan_subnet)
      create ONE ClientSession and share it across every receiver they
      contact, replacing what used to be N * M sessions per operation.
    """

    def __init__(
        self,
        ip: str,
        port: int = 8080,
        password: str = "birddog",
        timeout: int = 5,
        legacy_paths: bool = False,
        session: aiohttp.ClientSession | None = None,
    ):
        self.ip = ip
        self.port = port
        self.password = password
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.base_url = f"http://{ip}:{port}"
        self.legacy_paths = legacy_paths
        self._session = session
        self._owns_session = False  # True only when __aenter__ creates one

    async def __aenter__(self):
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
            self._owns_session = True
        return self

    async def __aexit__(self, *exc_info):
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None
            self._owns_session = False

    def _path(self, endpoint: str) -> str:
        if self.legacy_paths and endpoint in LEGACY_PATH_MAP:
            return LEGACY_PATH_MAP[endpoint]
        return endpoint

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Leash/1.0",
        }

    async def _request(self, method: str, endpoint: str, **kwargs) -> tuple[int, Any]:
        """Single code path for GET and POST — uses the shared session if set,
        else spins up a short-lived one."""
        url = f"{self.base_url}{self._path(endpoint)}"
        logger.debug("%s %s", method, url)
        try:
            if self._session is not None:
                async with self._session.request(method, url, **kwargs) as resp:
                    text = await resp.text()
                    if resp.status not in (200, 201):
                        logger.warning("%s %s → HTTP %d: %.200s", method, url, resp.status, text)
                    return resp.status, _try_json(text)
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.request(method, url, **kwargs) as resp:
                    text = await resp.text()
                    if resp.status not in (200, 201):
                        logger.warning("%s %s → HTTP %d: %.200s", method, url, resp.status, text)
                    return resp.status, _try_json(text)
        except asyncio.TimeoutError:
            logger.warning("Timeout %s %s", method, url)
            return 0, "timeout"
        except aiohttp.ClientError as exc:
            logger.warning("Error %s %s: %s", method, url, exc)
            return 0, str(exc)

    async def _get(self, endpoint: str) -> tuple[int, Any]:
        return await self._request("GET", endpoint, headers=self._headers())

    async def _post(self, endpoint: str, data: Any = None, raw_text: bool = False) -> tuple[int, Any]:
        if raw_text:
            return await self._request(
                "POST", endpoint,
                data=str(data) if data is not None else "",
                headers={**self._headers(), "Content-Type": "text/plain"},
            )
        return await self._request("POST", endpoint, json=data, headers=self._headers())

    # -------------------------------------------------------------------------
    # BasicDeviceInfo
    # -------------------------------------------------------------------------

    async def get_about(self) -> tuple[int, Any]:
        return await self._get("/about")

    async def get_hostname(self) -> tuple[int, Any]:
        return await self._get("/hostname")

    async def reboot(self) -> tuple[int, Any]:
        return await self._get("/reboot")

    async def restart(self) -> tuple[int, Any]:
        return await self._get("/restart")

    async def get_version(self) -> tuple[int, Any]:
        return await self._get("/version")

    # -------------------------------------------------------------------------
    # DeviceSettings
    # -------------------------------------------------------------------------

    async def get_analog_audio(self) -> tuple[int, Any]:
        return await self._get("/analogaudiosetup")

    async def set_analog_audio(self, data: dict) -> tuple[int, Any]:
        return await self._post("/analogaudiosetup", data)

    async def get_operation_mode(self) -> tuple[int, Any]:
        return await self._get("/operationmode")

    async def set_operation_mode(self, mode: str) -> tuple[int, Any]:
        # API expects plain text body: "encode" or "decode"
        return await self._post("/operationmode", mode, raw_text=True)

    async def get_video_output_interface(self) -> tuple[int, Any]:
        return await self._get("/videooutputinterface")

    async def set_video_output_interface(self, mode: str) -> tuple[int, Any]:
        return await self._post("/videooutputinterface", mode, raw_text=True)

    # -------------------------------------------------------------------------
    # NDI Encode
    # -------------------------------------------------------------------------

    async def get_encode_transport(self) -> tuple[int, Any]:
        return await self._get("/encodeTransport")

    async def set_encode_transport(self, data: dict) -> tuple[int, Any]:
        return await self._post("/encodeTransport", data)

    async def get_encode_setup(self) -> tuple[int, Any]:
        return await self._get("/encodesetup")

    async def set_encode_setup(self, data: dict) -> tuple[int, Any]:
        return await self._post("/encodesetup", data)

    # -------------------------------------------------------------------------
    # NDI Decode
    # -------------------------------------------------------------------------

    async def get_connect_to(self) -> tuple[int, Any]:
        return await self._get("/connectTo")

    async def set_connect_to(self, source_name: str) -> tuple[int, Any]:
        return await self._post("/connectTo", {"sourceName": source_name})

    async def get_decode_transport(self) -> tuple[int, Any]:
        return await self._get("/decodeTransport")

    async def set_decode_transport(self, data: dict) -> tuple[int, Any]:
        return await self._post("/decodeTransport", data)

    async def get_decode_setup(self) -> tuple[int, Any]:
        return await self._get("/decodesetup")

    async def set_decode_setup(self, data: dict) -> tuple[int, Any]:
        return await self._post("/decodesetup", data)

    async def get_decode_status(self) -> tuple[int, Any]:
        return await self._get("/decodestatus")

    # -------------------------------------------------------------------------
    # NDI Finder
    # -------------------------------------------------------------------------

    async def get_ndi_list(self) -> tuple[int, Any]:
        return await self._get("/List")

    async def reset_ndi(self) -> tuple[int, Any]:
        return await self._get("/reset")

    async def get_ndi_discovery_server(self) -> tuple[int, Any]:
        return await self._get("/NDIDisServer")

    async def set_ndi_discovery_server(self, data: dict) -> tuple[int, Any]:
        return await self._post("/NDIDisServer", data)

    async def get_ndi_group_name(self) -> tuple[int, Any]:
        return await self._get("/NDIGrpName")

    async def set_ndi_group_name(self, name: str) -> tuple[int, Any]:
        return await self._post("/NDIGrpName", name, raw_text=True)

    async def get_ndi_off_subnet(self) -> tuple[int, Any]:
        return await self._get("/NDIOffSnSrc")

    async def set_ndi_off_subnet(self, ip: str) -> tuple[int, Any]:
        return await self._post("/NDIOffSnSrc", ip, raw_text=True)

    # -------------------------------------------------------------------------
    # PTZ
    # -------------------------------------------------------------------------

    async def get_ptz_setup(self) -> tuple[int, Any]:
        return await self._get("/birddogptzsetup")

    async def set_ptz_setup(self, data: dict) -> tuple[int, Any]:
        return await self._post("/birddogptzsetup", data)

    # -------------------------------------------------------------------------
    # Camera image settings
    # -------------------------------------------------------------------------

    async def get_exposure(self) -> tuple[int, Any]:
        return await self._get("/birddogexpsetup")

    async def set_exposure(self, data: dict) -> tuple[int, Any]:
        return await self._post("/birddogexpsetup", data)

    async def get_white_balance(self) -> tuple[int, Any]:
        return await self._get("/birddogwbsetup")

    async def set_white_balance(self, data: dict) -> tuple[int, Any]:
        return await self._post("/birddogwbsetup", data)

    async def get_picture(self) -> tuple[int, Any]:
        return await self._get("/birddogpicsetup")

    async def set_picture(self, data: dict) -> tuple[int, Any]:
        return await self._post("/birddogpicsetup", data)

    async def get_colour_matrix(self) -> tuple[int, Any]:
        return await self._get("/birddogcmsetup")

    async def set_colour_matrix(self, data: dict) -> tuple[int, Any]:
        return await self._post("/birddogcmsetup", data)

    async def get_advanced(self) -> tuple[int, Any]:
        return await self._get("/birddogadvancesetup")

    async def set_advanced(self, data: dict) -> tuple[int, Any]:
        return await self._post("/birddogadvancesetup", data)

    async def get_external(self) -> tuple[int, Any]:
        return await self._get("/birddogexternalsetup")

    async def set_external(self, data: dict) -> tuple[int, Any]:
        return await self._post("/birddogexternalsetup", data)

    async def get_detail(self) -> tuple[int, Any]:
        return await self._get("/birddogdetsetup")

    async def set_detail(self, data: dict) -> tuple[int, Any]:
        return await self._post("/birddogdetsetup", data)

    async def get_gamma(self) -> tuple[int, Any]:
        return await self._get("/birddoggammasetup")

    async def set_gamma(self, data: dict) -> tuple[int, Any]:
        return await self._post("/birddoggammasetup", data)

    async def get_sil2_codec(self) -> tuple[int, Any]:
        return await self._get("/birddogsil2codec")

    async def set_sil2_codec(self, data: dict) -> tuple[int, Any]:
        return await self._post("/birddogsil2codec", data)

    async def get_sil2_enc(self) -> tuple[int, Any]:
        return await self._get("/birddogsil2enc")

    async def set_sil2_enc(self, data: dict) -> tuple[int, Any]:
        return await self._post("/birddogsil2enc", data)

    # -------------------------------------------------------------------------
    # Camera PTZ / focus / preset control
    # -------------------------------------------------------------------------

    async def ptz_move(
        self,
        pan: str = "STOP",
        tilt: str = "STOP",
        zoom: str = "STOP",
        speed: int = 5,
    ) -> tuple[int, Any]:
        """Send a PTZ velocity command to a BirdDog camera.

        BirdDog cameras (port 6791) combine pan+tilt into a single
        direction string on /birddogptz.  Zoom is a separate key.
        Speed is not part of this protocol.
        """
        _pantilt_map = {
            ("STOP",  "STOP"):  "stop",
            ("LEFT",  "STOP"):  "left",
            ("RIGHT", "STOP"):  "right",
            ("STOP",  "UP"):    "up",
            ("STOP",  "DOWN"):  "down",
            ("LEFT",  "UP"):    "leftup",
            ("RIGHT", "UP"):    "rightup",
            ("LEFT",  "DOWN"):  "leftdown",
            ("RIGHT", "DOWN"):  "rightdown",
        }
        payload: dict = {"PanTilt": _pantilt_map.get((pan, tilt), "stop")}
        if zoom != "STOP":
            payload["Zoom"] = zoom.lower()   # "tele" or "wide"
        return await self._post("/birddogptz", payload)

    async def ptz_stop(self) -> tuple[int, Any]:
        return await self.ptz_move("STOP", "STOP", "STOP")

    async def focus_control(self, action: str = "STOP") -> tuple[int, Any]:
        """action: NEAR | FAR | STOP | AUTO"""
        return await self._post("/birddogfocus", {"Focus": action.lower()})

    async def recall_preset(self, preset_number: int) -> tuple[int, Any]:
        return await self._post("/birddogRecallPreset", {"PresetNum": str(preset_number)})

    async def save_preset(self, preset_number: int) -> tuple[int, Any]:
        return await self._post("/birddogSavePreset", {"PresetNum": str(preset_number)})

    # -------------------------------------------------------------------------
    # Composite helpers
    # -------------------------------------------------------------------------

    async def fetch_status(self) -> dict:
        """Fetch hostname, current source, about info in one shot."""
        hostname_task = asyncio.create_task(self.get_hostname())
        connect_task = asyncio.create_task(self.get_connect_to())
        about_task = asyncio.create_task(self.get_about())

        h_code, h_data = await hostname_task
        c_code, c_data = await connect_task
        a_code, a_data = await about_task

        online = h_code == 200

        hostname = None
        if h_code == 200 and isinstance(h_data, str):
            hostname = h_data.strip()

        current_source = None
        if c_code == 200 and isinstance(c_data, dict):
            current_source = c_data.get("sourceName", "").strip()

        firmware = None
        serial = None
        video_format = None
        if a_code == 200 and isinstance(a_data, dict):
            firmware = a_data.get("FirmwareVersion")
            serial = a_data.get("SerialNumber")
            video_format = a_data.get("Format")

        return {
            "online": online,
            "hostname": hostname,
            "current_source": current_source,
            "firmware_version": firmware,
            "serial_number": serial,
            "video_format": video_format,
        }


def _try_json(text: str) -> Any:
    """Return parsed JSON if possible, otherwise return stripped string."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text.strip() if isinstance(text, str) else text


# ---------------------------------------------------------------------------
# Construction helpers (replace repeated constructor blocks across routes)
# ---------------------------------------------------------------------------

def client_config(app_config) -> dict:
    """Extract the BirdDog-related config keys into a plain dict usable by
    the bulk helpers.  Replaces several hand-built cfg_dict blocks."""
    return {
        "NDI_SUBNET_PREFIX": app_config["NDI_SUBNET_PREFIX"],
        "NDI_DEVICE_PORT": app_config["NDI_DEVICE_PORT"],
        "NDI_DEVICE_PASSWORD": app_config["NDI_DEVICE_PASSWORD"],
        "HTTP_TIMEOUT": app_config["HTTP_TIMEOUT"],
        "RECALL_CONCURRENCY": app_config.get("RECALL_CONCURRENCY", 10),
    }


def client_from_ip(ip: str, app_config) -> "BirdDogClient":
    """Build a BirdDogClient for a raw IP string using Flask app config."""
    return BirdDogClient(
        ip=ip,
        port=app_config["NDI_DEVICE_PORT"],
        password=app_config["NDI_DEVICE_PASSWORD"],
        timeout=app_config["HTTP_TIMEOUT"],
    )


def ptz_client_from_camera(camera, app_config) -> "BirdDogClient":
    """Build a BirdDogClient for PTZ/focus/preset control on a PTZ camera.

    BirdDog cameras expose PTZ control on port 6791 (separate from the
    NDI device REST API on port 8080).  CAMERA_PTZ_PORT overrides this.
    """
    ptz_port = app_config.get("CAMERA_PTZ_PORT", 6791)
    return BirdDogClient(
        ip=camera.ip_address,
        port=ptz_port,
        password=app_config.get("NDI_DEVICE_PASSWORD", "birddog"),
        timeout=app_config.get("HTTP_TIMEOUT", 5),
    )


def client_from_receiver(receiver, app_config) -> "BirdDogClient":
    """Build a BirdDogClient for a receiver object (uses receiver.ip_address)."""
    return client_from_ip(receiver.ip_address, app_config)


def client_from_camera(camera, app_config) -> "BirdDogClient":
    """Build a BirdDogClient for a PTZCamera object."""
    return client_from_ip(camera.ip_address, app_config)


# ---------------------------------------------------------------------------
# Sync helpers for use inside Flask route handlers
# ---------------------------------------------------------------------------

def run_async(coro):
    """Run an async coroutine from synchronous Flask code."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Inside an already-running loop (e.g. some WSGI wrappers) —
            # create a new loop in a thread instead.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def _bulk_timeout(timeout: int) -> aiohttp.ClientTimeout:
    """Build a ClientTimeout with a short connect deadline inside the total budget.

    Separating connect from read means a device that accepts the TCP handshake
    but then stops sending data will still be cut off at `timeout` seconds,
    while a device that never responds to SYN is cut off at connect_timeout.
    """
    connect_timeout = min(timeout, 3)
    return aiohttp.ClientTimeout(total=timeout, connect=connect_timeout)


def _bulk_connector(concurrency: int) -> aiohttp.TCPConnector:
    """Return a TCPConnector whose pool cap matches the concurrency limit.

    enable_cleanup_closed=True ensures sockets to unresponsive hosts are
    actively cleaned up rather than leaking until the GC collects them.
    keepalive_timeout matches the pool limit's useful life for short-lived
    bulk operations.
    """
    return aiohttp.TCPConnector(
        limit=concurrency,
        enable_cleanup_closed=True,
        keepalive_timeout=30,
    )


async def bulk_fetch_source(receivers: list, config: dict) -> list[dict]:
    """Lightweight poll: fetch ONLY the current NDI source from each receiver.

    Uses one HTTP call per device instead of the three required by bulk_fetch_status,
    making it suitable for frequent enforcement checks.
    Returns list of {"id", "online", "current_source"} dicts.
    All requests share one aiohttp session (and one connection pool) for reuse.
    """
    prefix = config.get("NDI_SUBNET_PREFIX", "10.1.248.")
    port = config.get("NDI_DEVICE_PORT", 8080)
    password = config.get("NDI_DEVICE_PASSWORD", "birddog")
    timeout = config.get("HTTP_TIMEOUT", 5)
    concurrency = config.get("RECALL_CONCURRENCY", 10)
    sem = asyncio.Semaphore(concurrency)

    connector = _bulk_connector(concurrency)
    async with aiohttp.ClientSession(
        connector=connector,
        timeout=_bulk_timeout(timeout),
    ) as session:
        async def _one(recv):
            async with sem:
                ip = f"{prefix}{recv['ip_last_octet']}"
                client = BirdDogClient(ip, port=port, password=password,
                                       timeout=timeout, session=session)
                code, data = await client.get_connect_to()
                online = code == 200
                current = None
                if online and isinstance(data, dict):
                    raw = data.get("sourceName", "")
                    current = raw.strip() if raw else None
                return {"id": recv["id"], "online": online, "current_source": current}

        return await asyncio.gather(*[_one(r) for r in receivers], return_exceptions=False)


async def bulk_fetch_status(receivers: list, config: dict) -> list[dict]:
    """Concurrently fetch status for a list of receiver dicts.

    All requests share one aiohttp session and connection pool.
    Concurrency is bounded by a semaphore (RECALL_CONCURRENCY) to prevent
    flooding the switch when called on 200+ receivers simultaneously.
    """
    prefix = config.get("NDI_SUBNET_PREFIX", "10.1.248.")
    port = config.get("NDI_DEVICE_PORT", 8080)
    password = config.get("NDI_DEVICE_PASSWORD", "birddog")
    timeout = config.get("HTTP_TIMEOUT", 5)
    concurrency = config.get("RECALL_CONCURRENCY", 10)
    # fetch_status fires 3 sub-tasks per receiver; keep inner parallelism in check.
    sem = asyncio.Semaphore(concurrency)

    connector = _bulk_connector(concurrency)
    async with aiohttp.ClientSession(
        connector=connector,
        timeout=_bulk_timeout(timeout),
    ) as session:
        async def _one(recv):
            async with sem:
                ip = f"{prefix}{recv['ip_last_octet']}"
                client = BirdDogClient(ip, port=port, password=password,
                                       timeout=timeout, session=session)
                status = await client.fetch_status()
                status["id"] = recv["id"]
                return status

        return await asyncio.gather(*[_one(r) for r in receivers], return_exceptions=False)
