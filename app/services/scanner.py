"""
Subnet scanner — concurrently probes every host in the configured scan ranges
and identifies BirdDog devices via the /about endpoint.

Returns two separate lists so the caller can upsert each type independently:
  - decoders: BirdDog PLAY units  (NDIReceiver)
  - cameras:  all other BirdDog models (PTZCamera — P100, P120, A200GEN2, …)
"""
import asyncio
import logging
from typing import Optional

import aiohttp

from app.services.birddog_client import BirdDogClient
from app.services.netrange import suffix_from_ip

logger = logging.getLogger(__name__)

PLAY_MARKER = "BirdDog PLAY"


def _device_type(about: dict) -> Optional[str]:
    """Return 'decoder', 'camera', or None for non-BirdDog / unrecognised."""
    combined = f"{about.get('HardwareVersion', '')} {about.get('FirmwareVersion', '')}"
    if PLAY_MARKER in combined:
        return "decoder"
    if "BirdDog" in combined:
        return "camera"
    return None


def _extract_model(hw: str, fw: str) -> str:
    """Extract short model name, e.g. 'P120', 'A200GEN2', 'P100'."""
    text = (hw or fw).strip()
    if text.lower().startswith("birddog "):
        parts = text[8:].split()
        return parts[0] if parts else "Unknown"
    return "Unknown"


def _parse_decoder(suffix: str, ip: str, data: dict) -> dict:
    return {
        "ip_last_octet": suffix,
        "ip_address": ip,
        "hostname": (data.get("HostName") or "").strip(),
        "firmware_version": data.get("FirmwareVersion", ""),
        "hardware_version": data.get("HardwareVersion", ""),
        "serial_number": data.get("SerialNumber", ""),
        "video_format": data.get("Format", ""),
        "network_config_method": data.get("NetworkConfigMethod", ""),
        "gateway": data.get("GateWay", ""),
        "network_mask": data.get("NetworkMask", ""),
        "fallback_ip": data.get("FallbackIP", ""),
        "mcu_version": data.get("MCUVersion", ""),
    }


def _parse_camera(suffix: str, ip: str, data: dict) -> dict:
    hw = data.get("HardwareVersion", "")
    fw = data.get("FirmwareVersion", "")
    return {
        "ip_last_octet": suffix,
        "ip_address": ip,
        "hostname": (data.get("HostName") or "").strip(),
        "model": _extract_model(hw, fw),
        "firmware_version": fw,
        "hardware_version": hw,
        "serial_number": data.get("SerialNumber", ""),
        "mcu_version": data.get("MCUVersion", ""),
        "network_config_method": data.get("NetworkConfigMethod", ""),
        "gateway": data.get("GateWay", ""),
        "network_mask": data.get("NetworkMask", ""),
    }


async def scan_subnet(
    prefix: str,
    port: int = 8080,
    password: str = "birddog",
    timeout: int = 2,
    start: int = 1,
    end: int = 254,
    ips: Optional[list[str]] = None,
) -> tuple[list[dict], list[dict]]:
    """
    Probe every host in *ips* (full dotted addresses), or — when *ips* is
    None — prefix+start … prefix+end on the primary /24.
    Returns (decoders, cameras) — two lists of dicts.  Each dict's
    ``ip_last_octet`` is the address suffix relative to *prefix* (see
    app.services.netrange), so receivers off the primary /24 get e.g.
    "250.17" instead of colliding on the bare last octet.
    All probes share one aiohttp session and a concurrency semaphore.
    """
    if ips is None:
        ips = [f"{prefix}{i}" for i in range(start, end + 1)]
    # Most addresses in a big range are empty and just sit until the connect
    # timeout, so wider scans get more parallelism to keep wall time sane
    # (/24 ≈ 2 s, /21 ≈ 2032 hosts ≈ 16 s worst case).
    concurrency = 64 if len(ips) <= 256 else 256
    sem = asyncio.Semaphore(concurrency)
    connector = aiohttp.TCPConnector(limit=concurrency, enable_cleanup_closed=True)

    async with aiohttp.ClientSession(
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=timeout, connect=min(timeout, 2)),
    ) as session:
        async def _probe(ip: str) -> Optional[dict]:
            suffix = suffix_from_ip(ip, prefix)
            async with sem:
                client = BirdDogClient(ip, port=port, password=password,
                                       timeout=timeout, session=session)
                try:
                    code, data = await client.get_about()
                except Exception as exc:
                    logger.debug("Probe %s error: %s", ip, exc)
                    return None

            if code != 200 or not isinstance(data, dict):
                return None

            dtype = _device_type(data)
            if dtype == "decoder":
                logger.info("Found decoder at %s (%s)", ip, data.get("HostName", "?"))
                return {"_type": "decoder", **_parse_decoder(suffix, ip, data)}
            if dtype == "camera":
                logger.info("Found camera at %s (%s)", ip, data.get("HostName", "?"))
                return {"_type": "camera", **_parse_camera(suffix, ip, data)}
            return None

        results = await asyncio.gather(
            *[_probe(ip) for ip in ips],
            return_exceptions=False,
        )
        found = [r for r in results if r is not None]
        decoders = [{k: v for k, v in r.items() if k != "_type"} for r in found if r["_type"] == "decoder"]
        cameras  = [{k: v for k, v in r.items() if k != "_type"} for r in found if r["_type"] == "camera"]
        return decoders, cameras
