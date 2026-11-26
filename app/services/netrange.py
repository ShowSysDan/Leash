"""
Network-range helpers for device discovery and addressing.

Leash originally assumed every BirdDog lived on one /24 and stored only the
last octet of each device's IP.  To support wider NDI VLANs (e.g. a /21 or
/20) without breaking existing data, receivers now store an *address suffix*
relative to ``NDI_SUBNET_PREFIX``:

  prefix "10.1.248."   IP 10.1.248.83  → suffix "83"       (unchanged)
  prefix "10.1.248."   IP 10.1.250.17  → suffix "250.17"
  prefix "10.1.248."   IP 10.2.0.9     → suffix "2.0.9"

The suffix is the shortest run of trailing octets that, appended to the
leading octets of the prefix, reproduces the full IP.  Existing rows (plain
last octets on the primary /24) keep working exactly as before, and the
external API can keep routing by bare octet for the primary /24.

Scan ranges are configured in ``NDI_SCAN_RANGES`` as a comma-separated list
of CIDR blocks and/or dash ranges:

  10.1.248.0/21
  10.1.248.0/24, 10.1.250.0/24
  10.1.248.10-10.1.249.200
  10.1.248.10-200            (short form: last octet range)
  10.1.251.7                 (single host)

Empty means "the primary /24 given by NDI_SUBNET_PREFIX" (legacy behaviour).
"""
from __future__ import annotations

import ipaddress

# Hard ceiling so a typo (e.g. 10.0.0.0/8) cannot fire 16 million probes.
MAX_SCAN_HOSTS = 8192


def _prefix_octets(prefix: str) -> list[str]:
    return [p for p in (prefix or "").strip().split(".") if p != ""]


def ip_from_suffix(suffix: str, prefix: str) -> str:
    """Build a full dotted IP from a stored suffix and the subnet prefix."""
    s = str(suffix or "").strip()
    tail = s.split(".")
    if len(tail) >= 4:
        return s
    lead = _prefix_octets(prefix)[: 4 - len(tail)]
    return ".".join(lead + tail)


def suffix_from_ip(ip: str, prefix: str) -> str:
    """Return the shortest suffix of *ip* relative to *prefix* (see module doc)."""
    octets = str(ip).strip().split(".")
    lead = _prefix_octets(prefix)
    shared = 0
    for i in range(min(3, len(lead))):
        if octets[i] == lead[i]:
            shared += 1
        else:
            break
    return ".".join(octets[shared:])


def normalize_address(value, prefix: str) -> tuple[bool, str]:
    """
    Validate user input for a device address and return (ok, suffix).

    Accepts a bare last octet ("83"), a partial suffix ("250.17") or a full
    IPv4 address ("10.1.250.17").  The host part must not be .0 or .255.
    """
    if value is None:
        return False, ""
    s = str(value).strip()
    parts = s.split(".")
    if not s or len(parts) > 4 or not all(p.isdigit() for p in parts):
        return False, s
    try:
        ip = ipaddress.IPv4Address(ip_from_suffix(s, prefix))
    except ValueError:
        return False, s
    last = int(str(ip).rsplit(".", 1)[-1])
    if last < 1 or last > 254:
        return False, s
    return True, suffix_from_ip(str(ip), prefix)


def _parse_one(token: str, prefix: str) -> list[ipaddress.IPv4Address]:
    token = token.strip()
    if not token:
        return []
    if "/" in token:
        net = ipaddress.IPv4Network(token, strict=False)
        if net.num_addresses > MAX_SCAN_HOSTS + 2:
            raise ValueError(f"{token} is too large (max {MAX_SCAN_HOSTS} hosts)")
        hosts = list(net.hosts()) if net.prefixlen < 31 else list(net)
    elif "-" in token:
        lo_s, hi_s = (x.strip() for x in token.split("-", 1))
        lo = ipaddress.IPv4Address(ip_from_suffix(lo_s, prefix) if lo_s.count(".") < 3 else lo_s)
        if hi_s.count(".") < 3:
            # Short form: "10.1.248.10-200" → same leading octets as lo.
            lead = str(lo).split(".")[: 4 - len(hi_s.split("."))]
            hi = ipaddress.IPv4Address(".".join(lead + hi_s.split(".")))
        else:
            hi = ipaddress.IPv4Address(hi_s)
        if hi < lo:
            raise ValueError(f"{token}: end is before start")
        if int(hi) - int(lo) + 1 > MAX_SCAN_HOSTS:
            raise ValueError(f"{token} is too large (max {MAX_SCAN_HOSTS} hosts)")
        hosts = [ipaddress.IPv4Address(i) for i in range(int(lo), int(hi) + 1)]
    else:
        hosts = [ipaddress.IPv4Address(ip_from_suffix(token, prefix) if token.count(".") < 3 else token)]
    # Never probe .0 / .255 host parts — on a /21 these are valid hosts in
    # theory, but BirdDogs never sit there and it keeps parity with /24 scans.
    return [h for h in hosts if 0 < int(h) % 256 < 255]


def parse_scan_ranges(spec: str, prefix: str) -> list[str]:
    """
    Expand an NDI_SCAN_RANGES spec into a sorted, de-duplicated IP list.
    Raises ValueError with a readable message on bad input.
    """
    spec = (spec or "").strip()
    if not spec:
        spec = f"{ip_from_suffix('1', prefix)}-254"
    seen: set[ipaddress.IPv4Address] = set()
    for token in spec.replace(";", ",").replace("\n", ",").split(","):
        try:
            seen.update(_parse_one(token, prefix))
        except ValueError as exc:
            raise ValueError(f"Invalid scan range '{token.strip()}': {exc}") from None
        if len(seen) > MAX_SCAN_HOSTS:
            raise ValueError(f"Scan ranges cover more than {MAX_SCAN_HOSTS} hosts")
    return [str(ip) for ip in sorted(seen)]


def describe_scan_ranges(spec: str, prefix: str) -> str:
    """Short human label for the scan modal, e.g. '10.1.248.0/21 (2,032 hosts)'."""
    try:
        n = len(parse_scan_ranges(spec, prefix))
    except ValueError:
        return (spec or "").strip()
    label = (spec or "").strip() or f"{ip_from_suffix('1', prefix)}–254"
    return f"{label} ({n:,} hosts)"
