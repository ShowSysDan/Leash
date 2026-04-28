"""
Process-level diagnostics for the Leash worker.

Exposes a single helper, get_tcp_summary(), that reports how many TCP
connections this Python process currently holds, broken down by state
so stuck or leaked sockets are easy to spot.

States of interest:
  ESTABLISHED   active connections
  CLOSE_WAIT    peer closed but we haven't — most common "leak" symptom
  TIME_WAIT     waiting for the OS reuse timer
  FIN_WAIT1/2   our side closed, peer hasn't acknowledged yet
  LAST_ACK      we sent FIN after a CLOSE_WAIT
  SYN_SENT      handshake outbound — long-lived = unreachable peer
  LISTEN        accepting sockets

If psutil isn't available the function falls back to /proc/net/tcp on
Linux and returns a degraded view.
"""
from __future__ import annotations

import os
from typing import Any

try:
    import psutil  # type: ignore
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False


_LIVE_STATES = {"ESTABLISHED", "SYN_SENT", "SYN_RECV"}
_DEAD_STATES = {"CLOSE_WAIT", "TIME_WAIT", "LAST_ACK", "FIN_WAIT1", "FIN_WAIT2", "CLOSING"}


def get_tcp_summary() -> dict[str, Any]:
    """Return a dict with counts of TCP sockets owned by this process."""
    if _HAS_PSUTIL:
        return _psutil_summary()
    return _proc_summary()


def _psutil_summary() -> dict[str, Any]:
    proc = psutil.Process(os.getpid())
    try:
        # net_connections() is the modern name; fall back to connections() on older psutil
        conns = proc.net_connections(kind="tcp") if hasattr(proc, "net_connections") else proc.connections(kind="tcp")
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        return {"available": False, "reason": "permission denied querying process sockets"}

    by_state: dict[str, int] = {}
    samples: list[dict[str, Any]] = []
    for c in conns:
        state = c.status or "NONE"
        by_state[state] = by_state.get(state, 0) + 1
        if len(samples) < 30:
            raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else None
            laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else None
            samples.append({"state": state, "remote": raddr, "local": laddr})

    total = sum(by_state.values())
    live = sum(by_state.get(s, 0) for s in _LIVE_STATES)
    dead = sum(by_state.get(s, 0) for s in _DEAD_STATES)
    listen = by_state.get("LISTEN", 0)

    return {
        "available": True,
        "source": "psutil",
        "pid": proc.pid,
        "total": total,
        "active": live,
        "dead_or_lingering": dead,
        "listening": listen,
        "by_state": by_state,
        "sample": samples,
    }


def _proc_summary() -> dict[str, Any]:
    """Linux-only fallback that reads /proc/net/tcp* for this process."""
    try:
        my_inodes = _own_socket_inodes()
    except OSError:
        return {"available": False, "reason": "psutil missing and /proc not available"}

    state_names = {
        "01": "ESTABLISHED", "02": "SYN_SENT", "03": "SYN_RECV", "04": "FIN_WAIT1",
        "05": "FIN_WAIT2", "06": "TIME_WAIT", "07": "CLOSE", "08": "CLOSE_WAIT",
        "09": "LAST_ACK", "0A": "LISTEN", "0B": "CLOSING",
    }
    by_state: dict[str, int] = {}

    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path) as fh:
                next(fh, None)
                for line in fh:
                    parts = line.split()
                    if len(parts) < 10:
                        continue
                    inode = parts[9]
                    if inode not in my_inodes:
                        continue
                    name = state_names.get(parts[3].upper(), parts[3])
                    by_state[name] = by_state.get(name, 0) + 1
        except FileNotFoundError:
            continue

    total = sum(by_state.values())
    live = sum(by_state.get(s, 0) for s in _LIVE_STATES)
    dead = sum(by_state.get(s, 0) for s in _DEAD_STATES)
    listen = by_state.get("LISTEN", 0)

    return {
        "available": True,
        "source": "/proc/net/tcp",
        "pid": os.getpid(),
        "total": total,
        "active": live,
        "dead_or_lingering": dead,
        "listening": listen,
        "by_state": by_state,
        "sample": [],
    }


def _own_socket_inodes() -> set[str]:
    """Return socket inode numbers owned by this process from /proc/<pid>/fd."""
    fd_dir = f"/proc/{os.getpid()}/fd"
    inodes: set[str] = set()
    for name in os.listdir(fd_dir):
        try:
            target = os.readlink(os.path.join(fd_dir, name))
        except OSError:
            continue
        if target.startswith("socket:["):
            inodes.add(target[len("socket:["):-1])
    return inodes
