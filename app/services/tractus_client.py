"""
Tractus MV API client — single source of truth for available NDI sources.

The API returns sources grouped by computer name.  We only care about the
``name`` field inside each sources entry (e.g. ``"LEKO (Clock)"``).

Primary host is tried first; if it fails the fallback host(s) are tried in
order.  Hosts are configured via TRACTUS_MV_HOSTS (comma-separated IPs) and
TRACTUS_MV_PORT.
"""
import asyncio
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 5


async def fetch_sources(
    hosts: list[str],
    port: int = 8901,
    timeout: int = DEFAULT_TIMEOUT,
) -> Optional[list[str]]:
    """
    Try each host in order.  Return a flat list of NDI source name strings
    from the first host that responds successfully, or None if all fail.

    Expected response schema (array at top level):
      [{"computerName": "...", "sources": [{"name": "...", "sourceName": "..."}, ...]}, ...]
    """
    url_path = "/sources"
    client_timeout = aiohttp.ClientTimeout(total=timeout)

    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        for host in hosts:
            url = f"http://{host}:{port}{url_path}"
            try:
                async with session.get(url, headers={"Accept": "application/json"}) as resp:
                    if resp.status != 200:
                        logger.warning("Tractus MV %s returned HTTP %d", url, resp.status)
                        continue
                    data = await resp.json(content_type=None)
                    names = _parse_sources(data)
                    logger.debug("Tractus MV %s: %d source(s)", host, len(names))
                    return names
            except asyncio.TimeoutError:
                logger.warning("Tractus MV %s: timeout", host)
            except aiohttp.ClientError as exc:
                logger.warning("Tractus MV %s: %s", host, exc)

    logger.error("Tractus MV: all hosts unreachable (%s)", ", ".join(hosts))
    return None


def _parse_sources(data) -> list[str]:
    """Extract the ``name`` field from every entry in every computer's sources list."""
    names: list[str] = []
    if not isinstance(data, list):
        return names
    for computer in data:
        if not isinstance(computer, dict):
            continue
        for entry in computer.get("sources", []):
            if not isinstance(entry, dict):
                continue
            name = entry.get("name", "").strip()
            if name:
                names.append(name)
    return names
