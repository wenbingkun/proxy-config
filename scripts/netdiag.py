#!/usr/bin/env python3
"""Collect and summarise the iPhone's network behaviour for an agent running in WSL.

Sources (all optional; whatever is reachable is used):
  router  the router's Mihomo controller: /logs stream (every new connection with its rule and
          policy chain) and a /connections snapshot. Sees the iPhone's traffic at home, where
          QX, Loon and Surge all hand most traffic to the router, but only what enters the
          core: ShellCrash lets Chinese domains and IPs bypass it.
  surge   Surge's HTTP API (surge/netdiag-api.example.sgmodule): per-request rule, policy,
          notes (DNS, rule evaluation, errors) and timing, plus DNS cache and events. Reached
          over the LAN, or over the USB cable (SURGE_API_USB, a usbmux port forward) on cellular.

Usage:
  netdiag.py record                 run the recorder in the foreground (systemd runs this)
  netdiag.py install-service        install and start the systemd user service
  netdiag.py status                 recorder, source and device reachability
  netdiag.py collect [--since 15m]  write a session directory and print a Markdown report
  netdiag.py get surge|router PATH  GET against an API (SSID/key redacted, read-only)

Config: ~/.config/ios-netdiag/env (KEY=VALUE, mode 600): ROUTER_API, ROUTER_SECRET,
SURGE_API, SURGE_KEY, DEVICE_IPS (comma separated); optional PYMOBILEDEVICE3 (path to the
binary) and SURGE_API_USB (e.g. http://127.0.0.1:16171) for the USB forward service; optional
ROUTER_SSH (ssh host alias), DEVICE_MACS (fixed MACs, same order as DEVICE_IPS) and
ROUTER_LAN_IF (default br-lan) to attribute the devices' IPv6 sources. Records: ~/.local/state/ios-netdiag.
Records hold browsing history; they stay on this machine and never go into the repo.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import selectors
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

CONFIG = Path(os.environ.get("NETDIAG_CONFIG", Path.home() / ".config/ios-netdiag/env"))
STATE = Path(os.environ.get("NETDIAG_STATE", Path.home() / ".local/state/ios-netdiag"))
RETAIN_DAYS = 7
SURGE_POLL = 2
SURGE_FAST_POLL = 0.5  # for 30 s after a burst: /v1/requests/recent only keeps 50 requests
SURGE_RETRY = 30
SERVICE = "ios-netdiag.service"
USB_SERVICE = "ios-netdiag-usb.service"
USBMUX = "127.0.0.1:27015"  # Windows Apple Devices usbmuxd, reachable from WSL (mirrored)

ROUTER_LINE = re.compile(
    r"^\[(?P<net>TCP|UDP)\] (?P<src>[0-9a-fA-F.:\[\]]+):(?P<sport>\d+)(?:\([^)]*\))? --> "
    r"(?P<host>.+?):(?P<port>\d+) (?:match (?P<rule>.+?) using (?P<policy>.+)|"
    r"doesn't match any rule using (?P<fallback>.+))$"
)
# Surge writes the SSID into SUBNET rule text and notes ("SUBNET SSID:Home Wi-Fi (module name)").
# The name may contain spaces, so it runs to a comma, a closing parenthesis, the " (" before a
# module name, or the end of the line.
SSID = re.compile(r"""SSID:(?:"[^"]*"|'[^']*'|.+?)(?=,|\)| \(|\n|$)""")
SRC = re.compile(r"(\S+):\d+(?:\([^)]*\))? --> ")
PROBLEM = re.compile(r"error|fail|timeout|timed out|refused|reset|unreachable|reject|denied", re.I)


def mask(text) -> str:
    """Hide the home SSID; apply before any truncation so no part of the name survives."""
    return SSID.sub("SSID:<home>", str(text))


def source_ip(payload: str) -> str | None:
    """The connection's source address in a Mihomo log line (the side before "-->")."""
    m = SRC.search(payload)
    return m[1].strip("[]") if m else None


def load_config() -> dict[str, str]:
    cfg: dict[str, str] = {}
    if CONFIG.exists():
        for line in CONFIG.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                cfg[key.strip()] = value.strip().strip("\"'")
    return cfg


def device_ips(cfg: dict[str, str]) -> list[str]:
    return [ip.strip() for ip in cfg.get("DEVICE_IPS", "").split(",") if ip.strip()]


def now() -> datetime:
    return datetime.now().astimezone()


def api_open(request, timeout):
    # Both finite GETs and the router log stream must bypass environment proxies.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout)


def http_get(url: str, headers: dict[str, str], timeout: float = 8):
    req = urllib.request.Request(url, headers=headers)
    with api_open(req, timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def validate_key(key: str, name: str) -> str:
    if (len(key) < 24 or len(set(key)) < 8 or
            re.search(r'example|placeholder|replace|set.your.secret|your.?key', key, re.I)):
        raise ValueError(f"{name} must be a random key of at least 24 characters; placeholder/weak keys are refused")
    return key


def redact_json(value, secrets=()):
    if isinstance(value, str):
        text = mask(value)
        for secret in secrets:
            if secret:
                text = text.replace(secret, '<key>')
        return text
    if isinstance(value, list):
        return [redact_json(item, secrets) for item in value]
    if isinstance(value, dict):
        return {redact_json(key, secrets): redact_json(item, secrets) for key, item in value.items()}
    return value


def router_headers(cfg):
    if cfg.get("ROUTER_SECRET"):
        validate_key(cfg["ROUTER_SECRET"], "ROUTER_SECRET")
    return {"Authorization": f"Bearer {cfg['ROUTER_SECRET']}"} if cfg.get("ROUTER_SECRET") else {}


def surge_headers(cfg):
    validate_key(cfg.get("SURGE_KEY", ""), "SURGE_KEY")
    return {"X-Key": cfg["SURGE_KEY"]} if cfg.get("SURGE_KEY") else {}


_surge_last: list[str] = []


def api_get(cfg, source: str, path: str, timeout: float = 8):
    if source == "router":
        return http_get(cfg["ROUTER_API"].rstrip("/") + path, router_headers(cfg), timeout)
    bases = [cfg[k].rstrip("/") for k in ("SURGE_API", "SURGE_API_USB") if cfg.get(k)]
    bases.sort(key=lambda b: b not in _surge_last)  # the route that worked last time first
    error: Exception = RuntimeError("SURGE_API not configured")
    headers = surge_headers(cfg)
    for base in bases:
        try:
            data = http_get(base + path, headers, timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise
            error = exc
            continue
        except Exception as exc:  # noqa: BLE001 - try the next route
            error = exc
            continue
        _surge_last[:] = [base]
        return data
    raise error


def surge_route(cfg) -> str:
    if _surge_last and cfg.get("SURGE_API_USB") and _surge_last[0] == cfg["SURGE_API_USB"].rstrip("/"):
        return "USB"
    return "LAN"


# ---------------------------------------------------------------- recorder

class Writer:
    """Append JSON lines to records/<date>/<name>.jsonl, one file per day."""

    def __init__(self, name: str):
        self.name = name
        self.lock = threading.Lock()

    def write(self, obj: dict) -> None:
        t = now()
        obj = {"t": t.isoformat(timespec="milliseconds"), **obj}
        day = STATE / "records" / t.strftime("%Y-%m-%d")
        with self.lock:
            day.mkdir(parents=True, exist_ok=True)
            with open(day / f"{self.name}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def record_router(cfg, ips: list[str], out: Writer, status: Writer, tracker: "V6Tracker | None" = None) -> None:
    url = cfg["ROUTER_API"].rstrip("/") + "/logs?level=info"
    while True:
        try:
            req = urllib.request.Request(url, headers=router_headers(cfg))
            with api_open(req, 300) as resp:
                status.write({"source": "router", "state": "up"})
                for raw in resp:
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    payload = event.get("payload", "")
                    src = source_ip(payload)
                    if src in ips:
                        out.write({"level": event.get("type"), "payload": payload})
                    elif tracker and src and ":" in src:
                        observed = now()
                        label, version = tracker.classify(src, observed)
                        if label:
                            out.write({"t": observed.isoformat(timespec="milliseconds"), "level": event.get("type"),
                                       "payload": payload, "v6": label, "v6map": version})
        except Exception as exc:  # noqa: BLE001 - keep recording through any failure
            status.write({"source": "router", "state": "down", "error": str(exc)[:200]})
        time.sleep(5)


# ---------------------------------------------------------------- IPv6 attribution
#
# The iPhone also reaches Mihomo from temporary IPv6 addresses that DEVICE_IPS cannot list. With
# ROUTER_SSH and DEVICE_MACS set, the recorder reads the router's DHCP leases and IPv6 neighbour
# table, and attributes an IPv6 source to a device only while that address belongs to the device's
# fixed MAC. The recorder is the only writer of STATE/v6map.json (latest state) and of
# records/<date>/v6map.jsonl (one event per attempt); collect and status only read them, and all
# three judge an address with the same attribute().

V6_REFRESH = 30            # seconds between refreshes
V6_TRIGGER_GAP = 10        # earliest refresh after the previous attempt when an unknown source appears
V6_CURRENT = 90            # "current" only this long after the last successful refresh
V6_GRACE = 600             # an address stays attributable this long after it was last seen
V6_HOLD = timedelta(days=RETAIN_DAYS)  # how long a conflicted address is remembered
V6_STALE = 90              # an event describes the state for at most this long (timeline)
V6_SSH_TIMEOUT = 15        # whole ssh call, remote command included
V6_OUTPUT_LIMIT = 256 * 1024
V6_UNKNOWN_CAP = 512
V6_NEIGH_STATES = {"REACHABLE", "STALE", "DELAY", "PROBE", "PERMANENT"}
SSH_BIN = "ssh"
SSH_ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")
LAN_IF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,14}")
MAC = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}")


def v6_config(cfg: dict[str, str]) -> tuple[dict | None, str | None]:
    """(config, None) when enabled, (None, None) when not configured, (None, error) when invalid."""
    ssh, macs = cfg.get("ROUTER_SSH", "").strip(), cfg.get("DEVICE_MACS", "").strip()
    if not ssh and not macs:
        return None, None
    if not ssh or not macs:
        return None, "ROUTER_SSH and DEVICE_MACS must be set together"
    if not SSH_ALIAS.fullmatch(ssh):
        return None, "ROUTER_SSH must be a plain ssh host alias"
    lan_if = cfg.get("ROUTER_LAN_IF", "br-lan").strip()
    if not LAN_IF.fullmatch(lan_if):
        return None, "ROUTER_LAN_IF must be an interface name"
    ips = device_ips(cfg)
    mac_list = [m.strip().lower() for m in macs.split(",") if m.strip()]
    if len(ips) != len(mac_list):
        return None, f"DEVICE_IPS has {len(ips)} entries but DEVICE_MACS has {len(mac_list)}"
    for ip in ips:
        try:
            ipaddress.IPv4Address(ip)
        except ValueError:
            return None, "DEVICE_IPS entries must be IPv4 addresses"
    if any(not MAC.fullmatch(m) for m in mac_list):
        return None, "DEVICE_MACS entries must look like aa:bb:cc:dd:ee:ff"
    if len(set(ips)) != len(ips) or len(set(mac_list)) != len(mac_list):
        return None, "DEVICE_IPS and DEVICE_MACS must not repeat"
    devices = list(zip(ips, mac_list))
    fp = hashlib.sha256(json.dumps([ssh, lan_if, devices]).encode()).hexdigest()[:16]
    return {"ssh": ssh, "lan_if": lan_if, "devices": devices, "fp": fp}, None


def run_bounded(argv: list[str], timeout: float = V6_SSH_TIMEOUT, limit: int = V6_OUTPUT_LIMIT):
    """Run argv with one deadline over reading and exiting, and a cap on stdout+stderr together.

    Returns (returncode, stdout, stderr, failure, pid); failure is None, "timeout" or
    "output-too-large". The child is killed and reaped on every failure path."""
    deadline = time.monotonic() + timeout
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out_fd, err_fd = proc.stdout.fileno(), proc.stderr.fileno()
    chunks: dict[int, list[bytes]] = {out_fd: [], err_fd: []}
    total, failure = 0, None
    sel = selectors.DefaultSelector()
    try:
        for fd in chunks:
            sel.register(fd, selectors.EVENT_READ)
        while sel.get_map() and failure is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "timeout"
                break
            for key, _ in sel.select(remaining):
                data = os.read(key.fd, min(65536, limit - total + 1))
                if not data:
                    sel.unregister(key.fd)
                    continue
                chunks[key.fd].append(data)
                total += len(data)
                if total > limit:
                    failure = "output-too-large"
                    break
        if failure is None:
            try:
                proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                failure = "timeout"
    finally:
        sel.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        proc.stdout.close()
        proc.stderr.close()
    return proc.returncode, b"".join(chunks[out_fd]), b"".join(chunks[err_fd]), failure, proc.pid


def norm6(addr: str) -> str | None:
    try:
        ip = ipaddress.ip_address(addr.strip("[]"))
    except ValueError:
        return None
    return str(ip) if ip.version == 6 else None


LEASES_END, NEIGH_END = "@@netdiag-leases-end@@", "@@netdiag-neigh-end@@"
NEIGH_ALL_STATES = V6_NEIGH_STATES | {"FAILED", "INCOMPLETE", "NOARP", "NONE"}


class V6ParseError(ValueError):
    pass


def v6_remote_command(lan_if: str) -> str:
    # Every step must succeed (&&) and each table ends with its own marker, so a failed or cut-off
    # read can never look like an empty table. echo before a marker: the file may lack a final newline.
    return (f"cat /tmp/dhcp.leases && echo && echo {LEASES_END} && "
            f"ip -6 neigh show dev {lan_if} && echo {NEIGH_END}")


def parse_router_tables(text: str, lan_if: str) -> tuple[dict[str, str], dict[str, set[str]]]:
    """leases {ipv4: mac} and neighbours {ipv6: {macs}} from v6_remote_command's output.

    Raises V6ParseError unless both tables are complete (each marker exactly once, in order,
    nothing after the last) and every line has a known shape; empty tables are valid. Only
    usable neighbour entries count: on lan_if, with an lladdr, in a live state, and a global or
    unique-local address (link-local addresses are outside this attribution). An IPv4 address
    leased to two different MACs maps to "conflict", which never matches a device."""
    lines = text.split("\n")
    if lines.count(LEASES_END) != 1 or lines.count(NEIGH_END) != 1:
        raise V6ParseError("table markers missing or repeated (truncated output?)")
    i, j = lines.index(LEASES_END), lines.index(NEIGH_END)
    if j < i or any(x.strip() for x in lines[j + 1:]):
        raise V6ParseError("unexpected output order or trailing data")
    leases: dict[str, str] = {}
    for line in lines[:i]:
        parts = line.split()
        if not parts or (parts[0] == "duid" and len(parts) == 2):
            continue
        if len(parts) != 5 or not parts[0].isdigit():
            raise V6ParseError(f"unrecognised lease line ({len(parts)} fields)")
        try:
            ip = ipaddress.ip_address(parts[2])
        except ValueError:
            raise V6ParseError("lease line without an IP address") from None
        if ip.version == 6:
            continue  # DHCPv6 lease: carries an IAID, not a MAC
        mac = parts[1].lower()
        if not MAC.fullmatch(mac):
            raise V6ParseError("IPv4 lease line without a MAC")
        leases[str(ip)] = mac if leases.get(str(ip), mac) == mac else "conflict"
    neigh: dict[str, set[str]] = defaultdict(set)
    for line in lines[i + 1:j]:
        parts = line.split()
        if not parts:
            continue
        rest, dev, mac = parts[1:], lan_if, None
        if rest[:1] == ["dev"] and len(rest) >= 2:
            dev, rest = rest[1], rest[2:]
        if rest[:1] == ["lladdr"] and len(rest) >= 2:
            mac, rest = rest[1].lower(), rest[2:]
        rest = [t for t in rest if t not in ("router", "proxy")]
        if len(rest) != 1 or rest[0] not in NEIGH_ALL_STATES or norm6(parts[0]) is None \
                or (mac is not None and not MAC.fullmatch(mac)):
            raise V6ParseError("unrecognised neighbour line")
        addr = norm6(parts[0])
        if dev == lan_if and mac and rest[0] in V6_NEIGH_STATES and not ipaddress.ip_address(addr).is_link_local:
            neigh[addr].add(mac)
    return leases, dict(neigh)


def _iso(t: datetime | None) -> str | None:
    return t.isoformat(timespec="microseconds") if t else None


def _dt(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def v6_new_state(conf: dict, session: str, at: datetime, reason: str) -> dict:
    return {"schema": 1, "version": 0, "config_fp": conf["fp"], "session": session, "session_refreshed": False,
            "last_attempt_at": _iso(at), "last_success_at": None, "ok": False, "failure_reason": reason,
            "identity": {ip: "pending" for ip, _ in conf["devices"]}, "addrs": {}, "reset": reason}


def v6_expire(state: dict, at: datetime) -> list[str]:
    """Drop active addresses past expires_at and conflict holds past hold_until."""
    gone = []
    for addr, e in list(state["addrs"].items()):
        limit = e["hold_until"] if e["state"] == "hold" else e["expires_at"]
        if at > _dt(limit):
            del state["addrs"][addr]
            gone.append(addr)
    return gone


def _aware(text) -> datetime:
    t = datetime.fromisoformat(text) if isinstance(text, str) else None
    if t is None or t.tzinfo is None:
        raise ValueError("time must be an ISO string with a UTC offset")
    return t


def v6_validate(state, conf: dict) -> str | None:
    """None when state is a well-formed snapshot for conf; otherwise what is wrong with it."""
    try:
        if not isinstance(state, dict) or state.get("schema") != 1 or isinstance(state.get("schema"), bool):
            return "not a schema-1 object"
        if not isinstance(state.get("config_fp"), str) or state["config_fp"] != conf["fp"]:
            return "made with a different IPv6 config"
        if not isinstance(state.get("version"), int) or isinstance(state["version"], bool) or state["version"] < 0:
            return "bad version"
        for key in ("session_refreshed", "ok"):
            if not isinstance(state.get(key), bool):
                return f"bad {key}"
        if not isinstance(state.get("session"), str) or not isinstance(state.get("failure_reason"), (str, type(None))) \
                or not isinstance(state.get("reset"), (str, type(None))):
            return "bad session, failure_reason or reset"
        _aware(state.get("last_attempt_at"))
        if state.get("last_success_at") is not None:
            _aware(state["last_success_at"])
        ips = {ip for ip, _ in conf["devices"]}
        identity = state.get("identity")
        if not isinstance(identity, dict) or set(identity) != ips or \
                any(not isinstance(v, str) or v not in ("ok", "mismatch", "no-lease", "pending")
                    for v in identity.values()):
            return "bad identity"
        addrs = state.get("addrs")
        if not isinstance(addrs, dict):
            return "addrs is not an object"
        for addr, e in addrs.items():
            if norm6(addr) != addr or not isinstance(e, dict) or not isinstance(e.get("device"), str) \
                    or e["device"] not in ips:
                return "bad address entry"
            if not isinstance(e.get("state"), str) or e["state"] not in ("active", "hold") \
                    or not isinstance(e.get("present"), bool):
                return "bad address state"
            if not isinstance(e.get("clean_count"), int) or isinstance(e["clean_count"], bool) or e["clean_count"] < 0:
                return "bad clean_count"
            for key in ("first_seen", "last_seen", "expires_at"):
                _aware(e.get(key))
            if e["state"] == "hold" or e.get("hold_until") is not None:
                _aware(e.get("hold_until"))   # required for a hold; an active entry keeps None or a past one
        others = state.get("others", [])
        if not isinstance(others, list) or any(not isinstance(a, str) or norm6(a) != a for a in others):
            return "bad others"
    except ValueError as exc:
        return f"bad time ({exc})"
    return None


def v6_load(path: Path, conf: dict, session: str, at: datetime) -> dict:
    """State for a new recorder session: the saved one when it matches this config, else empty.

    Either way the session starts unrefreshed (ok=false, pending-first-refresh), so nothing is
    "current" until this session's first successful refresh. Conflict holds and their progress
    survive a restart."""
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return v6_new_state(conf, session, at, "pending-first-refresh (no saved state)")
    except (OSError, ValueError):
        return v6_new_state(conf, session, at, "pending-first-refresh (saved state unreadable, reset)")
    problem = v6_validate(saved, conf)
    if problem == "made with a different IPv6 config":
        return v6_new_state(conf, session, at, "pending-first-refresh (config changed, reset)")
    if problem:
        return v6_new_state(conf, session, at, f"pending-first-refresh (saved state invalid: {problem}, reset)")
    saved.update(session=session, session_refreshed=False, ok=False, last_attempt_at=_iso(at),
                 failure_reason="pending-first-refresh", reset=None)
    v6_expire(saved, at)
    return saved


def v6_apply_success(state: dict, conf: dict, leases: dict, neigh: dict, at: datetime) -> dict:
    """Fold one successful router read into state. Returns a summary for the event."""
    changes = {"added": 0, "revoked": 0, "conflicts": 0, "restored": 0}
    targets = {mac: ip for ip, mac in conf["devices"]}
    identity = {}
    for ip, mac in conf["devices"]:
        lease = leases.get(ip)
        identity[ip] = "ok" if lease == mac else ("no-lease" if lease is None else "mismatch")
    for e in state["addrs"].values():
        e["present"] = False
    for addr, e in list(state["addrs"].items()):
        macs = neigh.get(addr, set())
        clean = identity[e["device"]] == "ok" and macs == {conf_mac(conf, e["device"])}
        if e["state"] == "hold":
            e["clean_count"] = e["clean_count"] + 1 if clean else 0
            e["present"] = clean
            if e["clean_count"] >= 2:
                e.update(state="active", last_seen=_iso(at), expires_at=_iso(at + timedelta(seconds=V6_GRACE)))
                changes["restored"] += 1
            continue
        if identity[e["device"]] != "ok":
            del state["addrs"][addr]          # identity changed: revoke at once, no grace
            changes["revoked"] += 1
        elif macs and not clean:
            e.update(state="hold", clean_count=0, hold_until=_iso(at + V6_HOLD))
            changes["conflicts"] += 1
        elif clean:
            e.update(last_seen=_iso(at), expires_at=_iso(at + timedelta(seconds=V6_GRACE)), present=True)
    previous_others = set(state.get("others", []))
    for addr, macs in neigh.items():
        if addr in state["addrs"]:
            continue
        mine = [targets[m] for m in macs if m in targets]
        if not mine:
            continue
        ip = mine[0]
        entry = {"device": ip, "first_seen": _iso(at), "last_seen": _iso(at),
                 "expires_at": _iso(at + timedelta(seconds=V6_GRACE)), "hold_until": None,
                 "state": "active", "clean_count": 0, "present": True}
        if len(macs) > 1 or len(mine) > 1 or addr in previous_others:
            # Shared, or belonged to another device last time: hold until two clean reads in a row.
            entry.update(state="hold", hold_until=_iso(at + V6_HOLD), present=False,
                         clean_count=1 if (macs == {conf_mac(conf, ip)} and identity[ip] == "ok") else 0)
            changes["conflicts"] += 1
        elif identity[ip] != "ok":
            continue
        else:
            changes["added"] += 1
        state["addrs"][addr] = entry
    v6_expire(state, at)
    state["others"] = sorted(a for a, macs in neigh.items() if not macs & set(targets))
    state.update(version=state["version"] + 1, last_attempt_at=_iso(at), last_success_at=_iso(at), ok=True,
                 failure_reason=None, session_refreshed=True, identity=identity, reset=None)
    return changes


def conf_mac(conf: dict, ip: str) -> str:
    return dict(conf["devices"])[ip]


def v6_apply_failure(state: dict, reason: str, at: datetime) -> None:
    """A failed attempt never extends an address: only the attempt fields change."""
    v6_expire(state, at)
    state.update(version=state["version"] + 1, last_attempt_at=_iso(at), ok=False, failure_reason=reason)


def attribute(state: dict | None, addr: str, at: datetime) -> tuple[str | None, str | None]:
    """(label, device) for an IPv6 source at time `at`; label is None, "current" or "grace".

    current: the last attempt succeeded in this recorder session, the address was in that read,
    and it was at most V6_CURRENT seconds ago. grace: still before expires_at otherwise (a failed
    or pending attempt makes every address grace at once). Holds and devices whose identity is
    not ok are never attributed."""
    key = norm6(addr) if state else None
    e = state["addrs"].get(key) if key else None
    if not e or e["state"] != "active" or state["identity"].get(e["device"]) != "ok":
        return None, None
    if at > _dt(e["expires_at"]):
        return None, None
    last = _dt(state.get("last_success_at"))
    if (state.get("ok") and state.get("session_refreshed") and e.get("present") and last
            and (at - last).total_seconds() <= V6_CURRENT):
        return "current", e["device"]
    return "grace", e["device"]


def v6_status(state: dict) -> str:
    """Timeline status of one attempt: ok only when the read worked and every identity is ok."""
    if not state.get("ok"):
        return f"failed: {state.get('failure_reason')}"
    bad = [f"{ip} {v}" for ip, v in sorted(state["identity"].items()) if v != "ok"]
    return f"identity: {', '.join(bad)}" if bad else "ok"


def v6_incomplete(state: dict, conf: dict, at: datetime) -> list[str]:
    """Why a usable snapshot (see v6_snapshot) is not complete right now (empty list: it is).

    A failed or stale refresh still leaves the snapshot usable within the grace rules."""
    reasons = []
    if not state.get("ok"):
        reasons.append(f"last attempt failed: {state.get('failure_reason')}")
    last_attempt, last_success = _dt(state.get("last_attempt_at")), _dt(state.get("last_success_at"))
    if not last_attempt or (at - last_attempt).total_seconds() > V6_STALE:
        reasons.append("recorder is not refreshing")
    if not last_success or (at - last_success).total_seconds() > V6_CURRENT:
        reasons.append("no successful refresh in the last 90 s")
    reasons += [f"device {ip} identity {v}" for ip, v in sorted(state["identity"].items()) if v != "ok"]
    return reasons


def v6_read_state() -> dict | None:
    """The raw saved snapshot, unchecked; readers use v6_snapshot."""
    try:
        state = json.loads((STATE / "v6map.json").read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else None
    except (OSError, ValueError):
        return None


def v6_snapshot(conf: dict, at: datetime) -> tuple[dict | None, list[str]]:
    """(usable snapshot or None, INCOMPLETE reasons) for collect and status.

    A missing, unreadable, malformed or other-config snapshot is never used: callers fall back
    to IPv4 only. A usable one may still be incomplete (failed or stale refresh, identity)."""
    path = STATE / "v6map.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, ["no IPv6 attribution snapshot (recorder not run with ROUTER_SSH/DEVICE_MACS); IPv4 only"]
    except (OSError, ValueError) as exc:
        return None, [f"IPv6 attribution snapshot unreadable ({type(exc).__name__}); IPv4 only"]
    problem = v6_validate(state, conf)
    if problem:
        return None, [f"IPv6 attribution snapshot not used: {problem}; IPv4 only until the recorder refreshes"]
    return state, v6_incomplete(state, conf, at)


def v6_write_state(state: dict) -> None:
    path = STATE / "v6map.json"
    tmp = path.with_name(f".v6map.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class V6Tracker:
    """Recorder side: refreshes the attribution table and classifies IPv6 sources."""

    def __init__(self, conf: dict, status: "Writer", events: "Writer"):
        self.conf, self.status, self.events = conf, status, events
        self.lock = threading.Lock()
        self.wake = threading.Event()
        at = now()
        self.state = v6_load(STATE / "v6map.json", conf, at.isoformat(timespec="milliseconds"), at)
        self.unknown: dict[str, int] = {}
        self.counts = {"other_dropped": 0, "unknown_dropped": 0, "overflow": 0}
        self.up: bool | None = None
        self.last_attempt = 0.0
        self._persist(at, {"kind": "start", "lost_counts": "in-memory counts of the previous session are lost"})

    def classify(self, src: str, at: datetime) -> tuple[str | None, int | None]:
        with self.lock:
            state = self.state
            label, _device = attribute(state, src, at)
            if label:
                return label, state["version"]
            addr = norm6(src)
            if addr is None:
                return None, None
            if addr in set(state.get("others", [])):
                self.counts["other_dropped"] += 1
            elif addr in self.unknown or len(self.unknown) < V6_UNKNOWN_CAP:
                self.unknown[addr] = self.unknown.get(addr, 0) + 1
                self.wake.set()
            else:
                self.counts["overflow"] += 1
        return None, None

    def refresh_once(self) -> None:
        argv = [SSH_BIN, "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", self.conf["ssh"],
                v6_remote_command(self.conf["lan_if"])]
        at_mono = time.monotonic()
        try:
            rc, out, err, failure, _pid = run_bounded(argv)
            reason = failure or (f"exit-{rc}: {err.decode(errors='replace')[:200].strip()}" if rc else None)
            parsed = None if reason else parse_router_tables(out.decode(errors="replace"), self.conf["lan_if"])
        except V6ParseError as exc:
            reason, parsed = f"parse: {exc}", None
        except Exception as exc:  # noqa: BLE001 - a failed read is a failed attempt, not a dead thread
            reason, parsed = f"error: {type(exc).__name__}: {str(exc)[:160]}", None
        at = now()
        with self.lock:
            state = json.loads(json.dumps(self.state))  # work on a copy; swap under the lock
            unknown, self.unknown = self.unknown, {}
            counts, self.counts = self.counts, {k: 0 for k in self.counts}
        if parsed is not None:
            changes = v6_apply_success(state, self.conf, *parsed, at)
            missed = sum(n for a, n in unknown.items() if attribute(state, a, at)[0])
            extra = {"kind": "attempt", **changes, "missed_before_attribution": missed,
                     "unknown_dropped": sum(unknown.values()) - missed + counts["unknown_dropped"],
                     "other_dropped": counts["other_dropped"], "overflow": counts["overflow"]}
        else:
            v6_apply_failure(state, reason, at)
            extra = {"kind": "attempt", "unknown_dropped": sum(unknown.values()) + counts["unknown_dropped"],
                     "other_dropped": counts["other_dropped"], "overflow": counts["overflow"]}
        with self.lock:
            self.state = state
        self.last_attempt = at_mono
        self._persist(at, extra)
        up = parsed is not None
        if up != self.up:
            self.status.write({"source": "ipv6", "state": "up" if up else "down",
                               **({} if up else {"error": state["failure_reason"]})})
            self.up = up

    def _persist(self, at: datetime, extra: dict) -> None:
        state = self.state
        v6_write_state(state)
        active = [e for e in state["addrs"].values() if e["state"] == "active"]
        self.events.write({"t": _iso(at), "version": state["version"], "status": v6_status(state),
                           "ok": state["ok"], "failure_reason": state["failure_reason"],
                           "identity": state["identity"], "addresses": len(active),
                           "holds": len(state["addrs"]) - len(active), **extra,
                           **({"reset": state["reset"]} if state.get("reset") else {})})

    def run(self) -> None:
        while True:
            self.refresh_once()
            deadline = time.monotonic() + V6_REFRESH
            while time.monotonic() < deadline:
                self.wake.wait(max(0.0, deadline - time.monotonic()))
                if self.wake.is_set():
                    early = self.last_attempt + V6_TRIGGER_GAP - time.monotonic()
                    if early > 0:
                        time.sleep(min(early, max(0.0, deadline - time.monotonic())))
                    self.wake.clear()
                    break


def read_v6_events(first_day, last_day) -> list[dict]:
    rows = []
    day = first_day
    while day <= last_day:
        path = STATE / "records" / day.isoformat() / "v6map.jsonl"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                    row["_t"] = datetime.fromisoformat(row["t"])
                except (ValueError, KeyError, TypeError):
                    continue
                rows.append(row)
        day += timedelta(days=1)
    return sorted(rows, key=lambda r: r["_t"])


def v6_timeline(start: datetime, end: datetime) -> tuple[list[tuple[datetime, datetime, str]], list[dict]]:
    """Attribution status over [start, end] as (from, to, status) segments, and the window's events.

    Uses the last event before start (looked up across days, within retention) as the anchor.
    Each event describes the state from its time until the next event or V6_STALE seconds,
    whichever is first; whatever no event describes is "unknown (no refresh observed)"."""
    events = read_v6_events((start - timedelta(days=RETAIN_DAYS)).date(), end.date())
    before = [e for e in events if e["_t"] < start]
    inside = [e for e in events if start <= e["_t"] <= end]
    points = before[-1:] + inside
    segments: list[tuple[datetime, datetime, str]] = []
    cursor = start
    for i, e in enumerate(points):
        nxt = points[i + 1]["_t"] if i + 1 < len(points) else None
        lo = max(e["_t"], start)
        hi = min(x for x in (nxt, e["_t"] + timedelta(seconds=V6_STALE), end) if x is not None)
        if hi <= cursor:
            continue
        if lo > cursor:
            segments.append((cursor, lo, "unknown (no refresh observed)"))
        segments.append((max(lo, cursor), hi, e.get("status") or "unknown (event without status)"))
        cursor = hi
    if cursor < end:
        segments.append((cursor, end, "unknown (no refresh observed)"))
    merged: list[tuple[datetime, datetime, str]] = []
    for seg in segments:
        if merged and merged[-1][2] == seg[2] and merged[-1][1] == seg[0]:
            merged[-1] = (merged[-1][0], seg[1], seg[2])
        else:
            merged.append(seg)
    return merged, inside


def v6_sources(cfg: dict, start: datetime, end: datetime) -> list[str]:
    """Sources lines about IPv6 attribution for a collect window."""
    conf, error = v6_config(cfg)
    if error:
        return [f"INCOMPLETE: IPv6 attribution config error: {error}"]
    if not conf:
        return ["IPv6: not attributed (IPv4/explicit DEVICE_IPS only)"]
    segments, inside = v6_timeline(start, end)
    lines = []
    bad = [s for s in segments if s[2] != "ok"]
    for lo, hi, status in bad:
        lines.append(f"INCOMPLETE: IPv6 attribution {status} {lo:%m-%d %H:%M:%S}–{hi:%H:%M:%S}")
    if not bad:
        lines.append("IPv6 attribution: ok throughout the window")
    totals = {k: sum(e.get(k, 0) or 0 for e in inside)
              for k in ("missed_before_attribution", "unknown_dropped", "other_dropped", "overflow")}
    lines.append("IPv6 unattributed lines dropped (lower bounds; 0 does not prove nothing was missed): "
                 + ", ".join(f"{k} {v}" for k, v in totals.items()))
    return lines


def request_id(req: dict) -> tuple:
    """Surge numbers requests per tunnel session, so an id alone may repeat after a restart."""
    return (req.get("id"), req.get("startDate"))


def surge_key(req: dict) -> tuple:
    return (req.get("completed"), req.get("failed"), req.get("status"))


def burst_check(newest: int, ids: list) -> tuple[int, bool, bool]:
    """Track the newest request id. Returns (newest, burst, reset).

    Ids only grow within one tunnel session and /v1/requests/recent always holds the newest
    requests, so a newest id lower than the last poll's means Surge began numbering again
    (reset). A burst is a gap after the last newest id or more than 25 new requests."""
    ids = [i for i in ids if isinstance(i, int)]
    if not ids:
        return newest, False, False
    reset = max(ids) < newest
    if reset:
        newest = 0
    fresh = [i for i in ids if i > newest]
    burst = bool(newest and fresh and (min(fresh) > newest + 1 or len(fresh) > 25))
    return max(newest, *ids), burst, reset


def record_surge(cfg, out: Writer, status: Writer) -> None:
    seen: dict = {}
    up = None
    newest = 0
    fast_until = 0.0
    session = ""  # recorder's own label for one run of Surge request numbering
    while True:
        try:
            data = api_get(cfg, "surge", "/v1/requests/recent", timeout=5)
        except Exception as exc:  # noqa: BLE001
            if up is not False:
                status.write({"source": "surge", "state": "down", "error": str(exc)[:200]})
            up = False
            time.sleep(SURGE_RETRY)
            continue
        if up != surge_route(cfg):
            status.write({"source": "surge", "state": "up", "route": surge_route(cfg)})
        newest, burst, reset = burst_check(newest, [r.get("id") for r in data.get("requests", [])])
        if reset or up is None or up is False:
            # Numbering began again, or the API was away and may have restarted: never compare
            # ids across this point.
            session = now().isoformat(timespec="milliseconds")
            newest, burst, _ = burst_check(0, [r.get("id") for r in data.get("requests", [])])
        up = surge_route(cfg)
        if burst:
            fast_until = time.monotonic() + 30
        for req in data.get("requests", []):
            rid = request_id(req)
            key = surge_key(req)
            if seen.get(rid) != key:
                seen[rid] = key
                out.write({"session": session, "request": req})
        if len(seen) > 20000:
            for rid in list(seen)[:10000]:
                del seen[rid]
        time.sleep(SURGE_FAST_POLL if time.monotonic() < fast_until else SURGE_POLL)


def prune() -> None:
    cutoff = (now() - timedelta(days=RETAIN_DAYS)).strftime("%Y-%m-%d")
    for kind in ("records", "sessions"):
        base = STATE / kind
        if base.is_dir():
            for d in base.iterdir():
                if d.name[:10] < cutoff:
                    shutil.rmtree(d, ignore_errors=True)


def cmd_record(_args) -> int:
    cfg = load_config()
    if cfg.get("SURGE_API") or cfg.get("SURGE_API_USB"):
        surge_headers(cfg)
    if cfg.get("ROUTER_API"):
        router_headers(cfg)
    STATE.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE, 0o700)
    status = Writer("recorder")
    status.write({"source": "recorder", "state": "start", "devices": device_ips(cfg)})
    threads = []
    tracker = None
    if cfg.get("ROUTER_API") and device_ips(cfg):
        conf, error = v6_config(cfg)
        if error:
            # IPv6 attribution is optional: report the bad config and keep recording IPv4.
            status.write({"source": "ipv6", "state": "config-error", "error": error})
        elif conf:
            try:
                tracker = V6Tracker(conf, status, Writer("v6map"))
                threads.append(threading.Thread(name="ipv6", target=tracker.run, daemon=True))
            except Exception as exc:  # noqa: BLE001 - reported, and IPv4 recording goes on
                tracker = None
                status.write({"source": "ipv6", "state": "down", "error": f"start failed: {str(exc)[:200]}"})
        threads.append(threading.Thread(
            name="router", target=record_router, args=(cfg, device_ips(cfg), Writer("router"), status, tracker),
            daemon=True))
    if cfg.get("SURGE_API") or cfg.get("SURGE_API_USB"):
        threads.append(threading.Thread(
            name="surge", target=record_surge, args=(cfg, Writer("surge"), status), daemon=True))
    if not threads:
        print(f"nothing to record; configure {CONFIG}", file=sys.stderr)
        return 1
    for t in threads:
        t.start()
    next_prune = 0.0
    while True:
        if time.monotonic() >= next_prune:
            prune()
            next_prune = time.monotonic() + 3600
        for thread in threads:
            thread.join(timeout=1)
            if not thread.is_alive():
                status.write({"source": "recorder", "state": "failed", "worker": thread.name})
                raise RuntimeError(f"{thread.name} recorder worker exited; service must restart")


def cmd_install_service(_args) -> int:
    unit = Path.home() / ".config/systemd/user" / SERVICE
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(
        "[Unit]\nDescription=iPhone network diagnostics recorder (proxy-config scripts/netdiag.py)\n"
        "After=network-online.target\n\n[Service]\n"
        f"ExecStart={sys.executable} {Path(__file__).resolve()} record\n"
        "Restart=always\nRestartSec=10\n\n[Install]\nWantedBy=default.target\n"
    )
    units = [SERVICE]
    cfg = load_config()
    usb = re.fullmatch(r"https?://127\.0\.0\.1:(\d+)/?", cfg.get("SURGE_API_USB", ""))
    if cfg.get("PYMOBILEDEVICE3") and usb:
        # Forward a local port to Surge's API port on the iPhone through the USB cable.
        (unit.parent / USB_SERVICE).write_text(
            "[Unit]\nDescription=USB forward to Surge's HTTP API on the iPhone (proxy-config netdiag)\n\n"
            f"[Service]\nEnvironment=PYMOBILEDEVICE3_USBMUX={USBMUX}\n"
            f"ExecStart={cfg['PYMOBILEDEVICE3']} usbmux forward {usb[1]} 6171\n"
            "Restart=always\nRestartSec=30\n\n[Install]\nWantedBy=default.target\n"
        )
        units.append(USB_SERVICE)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    for name in units:
        subprocess.run(["systemctl", "--user", "enable", name], check=True)
        subprocess.run(["systemctl", "--user", "restart", name], check=True)
        print(f"installed {unit.parent / name}")
    return 0


# ---------------------------------------------------------------- reading records

def parse_since(text: str) -> timedelta:
    m = re.fullmatch(r"(\d+)([smhd])", text)
    if not m:
        raise argparse.ArgumentTypeError("use e.g. 90s, 15m, 2h, 1d")
    return timedelta(**{{"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}[m[2]]: int(m[1])})


def read_records(name: str, start: datetime, end: datetime) -> list[dict]:
    rows = []
    day = start.date()
    while day <= end.date():
        path = STATE / "records" / day.isoformat() / f"{name}.jsonl"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if start <= datetime.fromisoformat(row["t"]) <= end:
                    rows.append(row)
        day += timedelta(days=1)
    return rows


def parse_router(payload: str) -> dict | None:
    m = ROUTER_LINE.match(payload)
    if not m:
        return None
    d = m.groupdict()
    d["policy"] = d.get("policy") or d.get("fallback")
    return d


def surge_time(req: dict) -> str:
    ts = req.get("startDate") or req.get("time")
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts).astimezone().strftime("%H:%M:%S")
    return str(ts or "")


def surge_host(req: dict) -> str:
    return str(req.get("remoteHost") or req.get("URL") or req.get("remoteAddress") or "?")


def surge_problems(req: dict) -> list[str]:
    """Failures other than an intended REJECT (those are listed separately)."""
    if req.get("rejected"):
        return []
    issues = []
    if req.get("failed"):
        issues.append("failed")
    status = str(req.get("status") or "")
    if status and status.lower() not in {"completed", "active", "complete"}:
        issues.append(f"status={status}")
    for note in req.get("notes") or []:
        if PROBLEM.search(str(note)):
            issues.append(mask(note)[:240])
    for rec in req.get("timingRecords") or []:
        if isinstance(rec.get("durationInMillisecond"), (int, float)) and rec["durationInMillisecond"] > 3000:
            issues.append(f"slow {rec.get('name')}: {rec['durationInMillisecond']:.0f} ms")
        elif isinstance(rec.get("duration"), (int, float)) and rec["duration"] > 3:
            issues.append(f"slow {rec.get('name')}: {rec['duration'] * 1000:.0f} ms")
    return issues


def merge_surge(rows: list[dict], live: list[dict], start: datetime, end: datetime) -> tuple[list[dict], int]:
    """Latest record of each request that started in the window, from recorder rows and a fresh
    /v1/requests/recent. A request belongs to the window by its startDate (by the time it was
    recorded only when Surge gave no startDate). Requests that started earlier but were still
    updated in the window are only counted, never listed as new ones."""
    lo, hi = start.timestamp(), end.timestamp()
    latest: dict = {}
    seen_at: dict = {}
    session: dict = {}
    for row in rows:
        req = row["request"]
        latest[request_id(req)] = req
        seen_at.setdefault(request_id(req), datetime.fromisoformat(row["t"]).timestamp())
        if row.get("session"):
            session.setdefault(request_id(req), row["session"])
    for req in live:
        latest[request_id(req)] = req
        seen_at.setdefault(request_id(req), hi)
    inside, earlier = [], 0
    for rid, req in latest.items():
        ts = req.get("startDate")
        ts = ts if isinstance(ts, (int, float)) else seen_at[rid]
        if lo <= ts <= hi:
            inside.append({**req, "_session": session.get(rid)})
        elif ts < lo:
            earlier += 1
    return sorted(inside, key=lambda r: r.get("startDate") or 0), earlier


def id_gaps(reqs: list[dict]) -> tuple[int | None, int]:
    """Ids Surge skipped within each recorder session: requests the poll may have missed.

    Returns (skipped, unsegmented). Ids restart with each tunnel session, so they are only
    compared inside one recorder session; requests without one (fetched live, or recorded by an
    older recorder) are left out and counted. skipped is None when nothing can be compared."""
    by_session: dict = defaultdict(set)
    unsegmented = 0
    for r in reqs:
        if r.get("_session") and isinstance(r.get("id"), int):
            by_session[r["_session"]].add(r["id"])
        else:
            unsegmented += 1
    if not by_session:
        return None, unsegmented
    skipped = 0
    for ids in by_session.values():
        ids = sorted(ids)
        skipped += sum(b - a - 1 for a, b in zip(ids, ids[1:]))
    return skipped, unsegmented


# ---------------------------------------------------------------- report

def table(header: list[str], rows: list[list], limit: int = 60) -> str:
    def cell(v) -> str:
        return mask(v).replace("|", "\\|").replace("\n", " ")[:120]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows[:limit]]
    if len(rows) > limit:
        lines.append(f"\n…{len(rows) - limit} more rows in the session files")
    return "\n".join(lines)


def build_report(ctx: dict) -> str:
    start, end, ips = ctx["start"], ctx["end"], ctx["devices"]
    out = [f"# netdiag {start:%Y-%m-%d %H:%M:%S} → {end:%H:%M:%S} ({start:%z})", ""]
    out.append("## Sources")
    for line in ctx["sources"]:
        out.append(f"- {line}")

    router = [(r, parse_router(r["payload"])) for r in ctx["router"]]
    surge = ctx["surge"]
    active = ctx.get("router_connections")

    out += ["", "## Problems"]
    problems = []
    for req in surge:
        issues = surge_problems(req)
        if issues:
            problems.append([surge_time(req), "surge", surge_host(req), req.get("policyName", ""),
                             "; ".join(issues)])
    for row, parsed in router:
        if row.get("level") not in ("info", None) or (parsed and "REJECT" in (parsed["policy"] or "")):
            problems.append([row["t"][11:19], f"router/{row.get('level')}",
                             parsed["host"] if parsed else "", parsed["policy"] if parsed else "",
                             mask(row["payload"])[:240]])
    problems.sort(key=lambda p: p[0])
    out.append(table(["time", "source", "host", "policy", "detail"], problems) if problems else "none found")

    if surge:
        out += ["", f"## Surge requests ({len(surge)})"]
        groups: dict = defaultdict(lambda: {"n": 0, "rules": set(), "policies": set(), "remote": set(), "bad": 0})
        for req in surge:
            g = groups[surge_host(req)]
            g["n"] += 1
            g["rules"].add(str(req.get("rule", "")))
            g["policies"].add(" ← ".join(x for x in (req.get("policyName"), req.get("originalPolicyName")) if x))
            g["remote"].add(str(req.get("remoteAddress", "")))
            g["bad"] += bool(surge_problems(req))
        rows = [[h, g["n"], g["bad"], ", ".join(sorted(g["rules"])), ", ".join(sorted(g["policies"])),
                 ", ".join(sorted(g["remote"]))[:60]] for h, g in groups.items()]
        rows.sort(key=lambda r: (-r[2], -r[1]))
        out.append(table(["host", "n", "problems", "rule", "policy ← group", "remote"], rows))

    rejected = [r for r in surge if r.get("rejected")]
    if rejected:
        out += ["", f"## Surge rejected ({len(rejected)}; intended blocks unless a wanted host is here)"]
        groups = defaultdict(lambda: {"n": 0, "rules": set()})
        for req in rejected:
            g = groups[surge_host(req)]
            g["n"] += 1
            g["rules"].add(str(req.get("rule", ""))[:100])
        rows = sorted(([h, g["n"], ", ".join(sorted(g["rules"]))] for h, g in groups.items()), key=lambda r: -r[1])
        out.append(table(["host", "n", "rule"], rows))

    parsed_rows = [p for _, p in router if p]
    if parsed_rows:
        out += ["", f"## Router connections from {', '.join(ips)} ({len(parsed_rows)})"]
        groups = defaultdict(lambda: {"n": 0, "rules": set(), "policies": set(), "ports": set()})
        for p in parsed_rows:
            g = groups[p["host"]]
            g["n"] += 1
            g["rules"].add(p["rule"] or "")
            g["policies"].add(p["policy"] or "")
            g["ports"].add(f"{p['net']}/{p['port']}")
        rows = [[h, g["n"], ", ".join(sorted(g["ports"])), ", ".join(sorted(g["rules"])),
                 ", ".join(sorted(g["policies"]))] for h, g in groups.items()]
        rows.sort(key=lambda r: -r[1])
        out.append(table(["host", "n", "net/port", "rule", "policy [chain]"], rows))

    if surge and parsed_rows:
        both = sorted({surge_host(r).rsplit(":", 1)[0] for r in surge} & {p["host"] for p in parsed_rows})
        out += ["", f"## Hosts seen by both Surge and the router ({len(both)})",
                "Surge sent these to the router (DIRECT at home); the router's rule decided the exit."]
        out.append(", ".join(both[:80]) or "none")

    if active is not None:
        out += ["", f"## Router active connections now ({len(active)})",
                "Snapshot at collection time; these connections may have started before the window."]
        rows = []
        for conn in active:
            meta = conn.get("metadata", {})
            rows.append([meta.get("host") or meta.get("destinationIP", ""),
                         f"{meta.get('network', '')}/{meta.get('destinationPort', '')}",
                         conn.get("rule", ""), " ← ".join(conn.get("chains") or [])])
        out.append(table(["host/IP", "net/port", "rule", "policy chain"], rows)
                   if rows else "No active router connections from the device at collection time.")

    if not surge and not router:
        out += ["", "No new request or router log records collected in this window. "
                "This does not establish that the device had no traffic; check source coverage and the recorder."]
    out += ["", f"Session files: {ctx['session']}"]
    return mask("\n".join(out)) + "\n"


# ---------------------------------------------------------------- commands

def service_state() -> str:
    try:
        res = subprocess.run(["systemctl", "--user", "is-active", SERVICE], capture_output=True, text=True)
        return res.stdout.strip() or "unknown"
    except FileNotFoundError:
        return "no systemd"


def last_status(source: str) -> dict | None:
    end = now()
    rows = [r for r in read_records("recorder", end - timedelta(days=2), end) if r.get("source") == source]
    return rows[-1] if rows else None


def probe(cfg, source: str) -> str:
    key = "ROUTER_API" if source == "router" else "SURGE_API"
    if not cfg.get(key):
        return "not configured"
    try:
        api_get(cfg, source, "/version" if source == "router" else "/v1/outbound", timeout=4)
        return "reachable"
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code} (check the key)"
    except Exception as exc:  # noqa: BLE001
        return f"unreachable ({type(exc).__name__})"


def v6_status_line(cfg: dict) -> str:
    conf, error = v6_config(cfg)
    if error:
        return f"config error: {error}"
    if not conf:
        return "not configured (IPv4/explicit DEVICE_IPS only)"
    at = now()
    state, reasons = v6_snapshot(conf, at)
    if state is None:
        return f"INCOMPLETE: {'; '.join(reasons)}"
    labels = [attribute(state, a, at)[0] for a in state["addrs"]]
    holds = sum(e["state"] == "hold" for e in state["addrs"].values())
    line = (f"snapshot v{state['version']}, last success {state.get('last_success_at') or 'never'}, "
            f"addresses current {labels.count('current')} grace {labels.count('grace')} hold {holds}")
    return line + (f"; INCOMPLETE: {'; '.join(reasons)}" if reasons else "")


def cmd_status(_args) -> int:
    cfg = load_config()
    print(f"config: {CONFIG} ({'present' if CONFIG.exists() else 'missing'})")
    print(f"recorder service: {service_state()}")
    for source in ("router", "surge"):
        st = last_status(source)
        rec = f"{st['state']}{' via ' + st['route'] if st.get('route') else ''} since {st['t']}" if st else "no status yet"
        print(f"{source}: live {probe(cfg, source)}; recorder {rec}")
    if cfg.get("SURGE_API_USB"):
        host, port = USBMUX.split(":")
        try:
            import socket
            socket.create_connection((host, int(port)), timeout=2).close()
            usbmux = "up"
        except OSError:
            usbmux = "down (open the Windows Apple Devices app once after a reboot)"
        print(f"USB route: usbmuxd {usbmux}; forward service {subprocess.run(['systemctl', '--user', 'is-active', USB_SERVICE], capture_output=True, text=True).stdout.strip()}")
    print(f"ipv6 attribution: {v6_status_line(cfg)}")
    for ip in device_ips(cfg):
        res = subprocess.run(["ping", "-c", "1", "-W", "2", ip], capture_output=True)
        print(f"device {ip}: {'answers ping' if res.returncode == 0 else 'no ping reply (asleep or away)'}")
    return 0


def cmd_collect(args) -> int:
    cfg = load_config()
    end = now()
    start = end - args.since
    session = STATE / "sessions" / f"{end:%Y-%m-%dT%H%M%S}"
    session.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE, 0o700)

    def save(name: str, data) -> None:
        (session / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    sources = [f"recorder service: {service_state()}"]
    gaps = []  # live data that could not be fetched; the report says so instead of failing
    router_rows = read_records("router", start, end)
    save("router-log.json", router_rows)
    v6_rows = [r for r in router_rows if r.get("v6")]
    sources.append(f"router log lines from the device: {len(router_rows)}"
                   + (f" (IPv6 {len(v6_rows)}, of which grace {sum(r['v6'] == 'grace' for r in v6_rows)})"
                      if v6_rows else ""))
    sources += v6_sources(cfg, start, end)
    conf, _error = v6_config(cfg)
    v6_state = None
    if conf:
        v6_state, reasons = v6_snapshot(conf, end)
        gaps += [f"IPv6 current snapshot: {r}" for r in reasons]

    def from_device(conn: dict) -> bool:
        sip = conn.get("metadata", {}).get("sourceIP")
        return sip in device_ips(cfg) or bool(v6_state and sip and ":" in sip and attribute(v6_state, sip, end)[0])

    mine = None  # None means unavailable; an empty list is a successful empty snapshot.
    state = probe(cfg, "router")
    if state == "reachable":
        try:
            conns = api_get(cfg, "router", "/connections").get("connections") or []
            mine = [c for c in conns if from_device(c)]
            save("router-connections.json", mine)
            sources.append(f"router /connections now: {len(mine)} open from the device")
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"router /connections failed: {type(exc).__name__}: {str(exc)[:120]}")
    else:
        sources.append(f"router API: {state}")

    surge_rows = read_records("surge", start, end)
    live = []
    state = probe(cfg, "surge")
    if state == "reachable":
        try:
            live = api_get(cfg, "surge", "/v1/requests/recent").get("requests", [])
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"Surge /v1/requests/recent failed: {type(exc).__name__}: {str(exc)[:120]}")
        for name, path in (("surge-dns.json", "/v1/dns"), ("surge-events.json", "/v1/events"),
                           ("surge-modules.json", "/v1/modules"), ("surge-outbound.json", "/v1/outbound"),
                           ("surge-policy-groups.json", "/v1/policy_groups")):
            try:
                save(name, api_get(cfg, "surge", path))
            except Exception as exc:  # noqa: BLE001
                save(name, {"error": str(exc)})
                gaps.append(f"Surge {path} failed: {type(exc).__name__}")
        sources.append(f"Surge API: reachable via {surge_route(cfg)}, so Surge is the active VPN")
    else:
        sources.append(f"Surge API: {state}; if the repro used QX or Loon, only the router view exists")
    surge, earlier = merge_surge(surge_rows, live, start, end)
    save("surge-requests.json", surge)
    sources.append(f"Surge requests started in window: {len(surge)}"
                   + (f"; {earlier} earlier requests were still updated in it (not listed)" if earlier else ""))
    missed, unsegmented = id_gaps(surge)
    if missed is None:
        sources.append("Surge missed-request estimate: not possible (no recorder session data in window)")
    elif missed or unsegmented:
        sources.append(f"Surge request ids skipped in window: {missed} (possibly missed by the poll; counts are a lower bound)"
                       + (f"; {unsegmented} requests without session data not compared" if unsegmented else ""))
    sources += [f"INCOMPLETE: {g}" for g in gaps]
    for st in (last_status("router"), last_status("surge")):
        if st:
            sources.append(f"recorder {st['source']}: {st['state']} since {st['t']}"
                           + (f" ({st['error']})" if st.get("error") else ""))

    ctx = {"start": start, "end": end, "devices": device_ips(cfg), "sources": sources,
           "router": router_rows, "router_connections": mine, "surge": surge, "session": session}
    report = build_report(ctx)
    (session / "report.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


def cmd_get(args) -> int:
    cfg = load_config()
    if not args.path.startswith("/"):
        print("path must start with /", file=sys.stderr)
        return 2
    secrets = [cfg.get(key, "") for key in ("ROUTER_SECRET", "SURGE_KEY")]
    print(json.dumps(redact_json(api_get(cfg, args.source, args.path), secrets), ensure_ascii=False, indent=1))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("record").set_defaults(func=cmd_record)
    sub.add_parser("install-service").set_defaults(func=cmd_install_service)
    sub.add_parser("status").set_defaults(func=cmd_status)
    p = sub.add_parser("collect")
    p.add_argument("--since", type=parse_since, default=timedelta(minutes=15))
    p.set_defaults(func=cmd_collect)
    p = sub.add_parser("get")
    p.add_argument("source", choices=["surge", "router"])
    p.add_argument("path")
    p.set_defaults(func=cmd_get)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
