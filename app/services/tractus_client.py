"""
Tractus MV API client — single source of truth for available NDI sources.

The API returns sources grouped by computer name.  We only care about the
``name`` field inside each sources entry (e.g. ``"LEKO (Clock)"``).

Primary host is tried first; if it fails the fallback host(s) are tried in
order.  Hosts are configured via TRACTUS_MV_HOSTS (comma-separated IPs) and
TRACTUS_MV_PORT.

The second half of this module drives the Tractus "projector" — a source
overlaid full-frame on top of a viewer's multiview output — and lists the
viewers each host is running.  Those commands go to ALL hosts, not just the
first that answers (see broadcast()).
"""
import asyncio
import logging
from typing import Optional
from urllib.parse import quote

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


# ---------------------------------------------------------------------------
# Viewers + projector control
#
# Unlike /sources (first host that answers wins), projector commands are
# broadcast to EVERY configured host so mirrored Tractus servers stay in
# lock-step.
# ---------------------------------------------------------------------------

def _viewer_path(viewer: int, suffix: str) -> str:
    return f"/viewer/{int(viewer)}/{suffix}"


def project_source_path(viewer: int, source: str) -> str:
    """Path that overlays ``source`` full-frame on top of a viewer's output."""
    return _viewer_path(viewer, f"project/source/{quote(source, safe='')}")


def project_stop_path(viewer: int) -> str:
    """Path that removes the projected source, restoring the normal multiview."""
    return _viewer_path(viewer, "project/stop")


async def fetch_viewers_from(
    session: aiohttp.ClientSession, host: str, port: int,
) -> tuple[Optional[list[dict]], Optional[str]]:
    """GET /viewers from one host.  Returns (viewers, error)."""
    url = f"http://{host}:{port}/viewers"
    try:
        async with session.get(url, headers={"Accept": "application/json"}) as resp:
            if resp.status != 200:
                return None, f"HTTP {resp.status}"
            data = await resp.json(content_type=None)
            return _parse_viewers(data), None
    except asyncio.TimeoutError:
        return None, "timeout"
    except (aiohttp.ClientError, ValueError) as exc:
        return None, str(exc) or exc.__class__.__name__


async def fetch_viewers_all(
    hosts: list[str], port: int = 8901, timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, dict]:
    """Query every host concurrently.

    Returns ``{host: {"ok": bool, "error": str|None, "viewers": [...]}}``.
    """
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        results = await asyncio.gather(
            *(fetch_viewers_from(session, h, port) for h in hosts)
        )
    return {
        h: {"ok": viewers is not None, "error": error, "viewers": viewers or []}
        for h, (viewers, error) in zip(hosts, results)
    }


def _parse_viewers(data) -> list[dict]:
    """Reduce the (very wide) /viewers payload to what Leash needs."""
    out: list[dict] = []
    if not isinstance(data, list):
        return out
    for v in data:
        if not isinstance(v, dict) or not isinstance(v.get("viewerIndex"), int):
            continue
        proj = v.get("projectorSource")
        projected = (proj.get("ndiSource") or None) if isinstance(proj, dict) else None
        out.append({
            "index": v["viewerIndex"],
            "name": v.get("name") or f"Viewer {v['viewerIndex']}",
            "ndi_enabled": bool(v.get("ndiStatus")),
            "projected_source": projected,
        })
    return out


async def send_command(
    session: aiohttp.ClientSession, host: str, port: int, path: str,
) -> tuple[bool, int, Optional[str]]:
    """GET a Tractus command path.  Returns (ok, http_status, error).

    http_status is 0 when the host could not be reached at all.
    """
    url = f"http://{host}:{port}{path}"
    try:
        async with session.get(url) as resp:
            await resp.read()
            if resp.status == 200:
                return True, 200, None
            return False, resp.status, f"HTTP {resp.status}"
    except asyncio.TimeoutError:
        return False, 0, "timeout"
    except aiohttp.ClientError as exc:
        return False, 0, str(exc) or exc.__class__.__name__


async def broadcast(
    hosts: list[str], port: int, paths: list[str], timeout: int = DEFAULT_TIMEOUT,
) -> list[dict]:
    """Send every path to every host concurrently.

    Returns one result dict per (host, path): host, path, ok, http_status, error.
    """
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    pairs = [(h, p) for p in paths for h in hosts]
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        results = await asyncio.gather(
            *(send_command(session, h, port, p) for h, p in pairs)
        )
    return [
        {"host": h, "path": p, "ok": ok, "http_status": code, "error": error}
        for (h, p), (ok, code, error) in zip(pairs, results)
    ]
