"""
Subnet scanner — concurrently probes every host on the configured subnet
and identifies BirdDog PLAY decoder units via the /about endpoint.
"""
import asyncio
import logging
from typing import Optional

import aiohttp

from app.services.birddog_client import BirdDogClient

logger = logging.getLogger(__name__)

# Detection: HardwareVersion must contain this string.
# BirdDog encoders / other models won't match.
PLAY_HARDWARE_MARKER = "BirdDog PLAY"


def _is_play_device(about: dict) -> bool:
    hw = about.get("HardwareVersion", "")
    fw = about.get("FirmwareVersion", "")
    return PLAY_HARDWARE_MARKER in hw or PLAY_HARDWARE_MARKER in fw


def _parse_about(octet: int, ip: str, data: dict) -> dict:
    return {
        "ip_last_octet": str(octet),
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
        "device_status": data.get("Status", ""),
    }


async def scan_subnet(
    prefix: str,
    port: int = 8080,
    password: str = "birddog",
    timeout: int = 2,
    start: int = 1,
    end: int = 254,
) -> list[dict]:
    """
    Probe every host from prefix+start to prefix+end (inclusive).
    Returns a list of dicts for discovered BirdDog PLAY devices.
    Uses a semaphore to cap concurrent connections and a single shared
    aiohttp session so a full-subnet scan uses one connection pool
    instead of creating a fresh session per probe.
    """
    sem = asyncio.Semaphore(64)

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async def _probe(octet: int) -> Optional[dict]:
            ip = f"{prefix}{octet}"
            async with sem:
                client = BirdDogClient(ip, port=port, password=password,
                                       timeout=timeout, session=session)
                try:
                    code, data = await client.get_about()
                except Exception as exc:
                    logger.debug("Probe %s error: %s", ip, exc)
                    return None

            if code == 200 and isinstance(data, dict) and _is_play_device(data):
                logger.info("Found BirdDog PLAY at %s (%s)", ip, data.get("HostName", "?"))
                return _parse_about(octet, ip, data)
            return None

        results = await asyncio.gather(
            *[_probe(i) for i in range(start, end + 1)],
            return_exceptions=False,
        )
        return [r for r in results if r is not None]
