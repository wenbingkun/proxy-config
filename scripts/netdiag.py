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
  netdiag.py get surge|router PATH  raw GET against an API (read-only)

Config: ~/.config/ios-netdiag/env (KEY=VALUE, mode 600): ROUTER_API, ROUTER_SECRET,
SURGE_API, SURGE_KEY, DEVICE_IPS (comma separated); optional PYMOBILEDEVICE3 (path to the
binary) and SURGE_API_USB (e.g. http://127.0.0.1:16171) for the USB forward service. Records: ~/.local/state/ios-netdiag.
Records hold browsing history; they stay on this machine and never go into the repo.
"""
from __future__ import annotations

import argparse
import json
import os
import re
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


def http_get(url: str, headers: dict[str, str], timeout: float = 8):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def router_headers(cfg):
    return {"Authorization": f"Bearer {cfg['ROUTER_SECRET']}"} if cfg.get("ROUTER_SECRET") else {}


def surge_headers(cfg):
    return {"X-Key": cfg["SURGE_KEY"]} if cfg.get("SURGE_KEY") else {}


_surge_last: list[str] = []


def api_get(cfg, source: str, path: str, timeout: float = 8):
    if source == "router":
        return http_get(cfg["ROUTER_API"].rstrip("/") + path, router_headers(cfg), timeout)
    bases = [cfg[k].rstrip("/") for k in ("SURGE_API", "SURGE_API_USB") if cfg.get(k)]
    bases.sort(key=lambda b: b not in _surge_last)  # the route that worked last time first
    error: Exception = RuntimeError("SURGE_API not configured")
    for base in bases:
        try:
            data = http_get(base + path, surge_headers(cfg), timeout)
        except urllib.error.HTTPError:
            raise
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


def record_router(cfg, ips: list[str], out: Writer, status: Writer) -> None:
    url = cfg["ROUTER_API"].rstrip("/") + "/logs?level=info"
    while True:
        try:
            req = urllib.request.Request(url, headers=router_headers(cfg))
            with urllib.request.urlopen(req, timeout=300) as resp:
                status.write({"source": "router", "state": "up"})
                for raw in resp:
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    payload = event.get("payload", "")
                    if source_ip(payload) in ips:
                        out.write({"level": event.get("type"), "payload": payload})
        except Exception as exc:  # noqa: BLE001 - keep recording through any failure
            status.write({"source": "router", "state": "down", "error": str(exc)[:200]})
        time.sleep(5)


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
    STATE.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE, 0o700)
    status = Writer("recorder")
    status.write({"source": "recorder", "state": "start", "devices": device_ips(cfg)})
    threads = []
    if cfg.get("ROUTER_API") and device_ips(cfg):
        threads.append(threading.Thread(
            target=record_router, args=(cfg, device_ips(cfg), Writer("router"), status), daemon=True))
    if cfg.get("SURGE_API"):
        threads.append(threading.Thread(
            target=record_surge, args=(cfg, Writer("surge"), status), daemon=True))
    if not threads:
        print(f"nothing to record; configure {CONFIG}", file=sys.stderr)
        return 1
    for t in threads:
        t.start()
    while True:
        prune()
        time.sleep(3600)


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

    if not surge and not parsed_rows:
        out += ["", "No traffic from the device in this window. Check that the iPhone is on home Wi-Fi, "
                "the recorder is running (`netdiag.py status`) and the window covers the repro."]
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
    sources.append(f"router log lines from the device: {len(router_rows)}")
    state = probe(cfg, "router")
    if state == "reachable":
        try:
            conns = api_get(cfg, "router", "/connections").get("connections") or []
            mine = [c for c in conns if c.get("metadata", {}).get("sourceIP") in device_ips(cfg)]
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
           "router": router_rows, "surge": surge, "session": session}
    report = build_report(ctx)
    (session / "report.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


def cmd_get(args) -> int:
    cfg = load_config()
    if not args.path.startswith("/"):
        print("path must start with /", file=sys.stderr)
        return 2
    print(json.dumps(api_get(cfg, args.source, args.path), ensure_ascii=False, indent=1))
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
