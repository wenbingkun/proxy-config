#!/usr/bin/env python3
"""Offline checks for netdiag's IPv6 attribution: config, router tables, the state contract,
bounded ssh, the history timeline, the recorder hook and cross-process readers."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import netdiag  # noqa: E402

IP, MAC = "192.0.2.60", "3a:00:00:00:00:01"
OTHER_IP, OTHER_MAC = "192.0.2.61", "3a:00:00:00:00:02"
A1 = "2001:db8:1::a"            # the device's temporary address
A1_LONG = "2001:0db8:0001:0000:0000:0000:0000:000a"
A2 = "2001:db8:1::b"            # another device's address
ULA = "fd00:1::c"
CFG = {"DEVICE_IPS": IP, "DEVICE_MACS": MAC.upper(), "ROUTER_SSH": "miwifi"}


def conf():
    c, err = netdiag.v6_config(CFG)
    assert c and err is None, err
    return c


def tables(lease=MAC, neigh=((A1, MAC),), extra=""):
    """What v6_remote_command prints: both tables, each closed by its marker."""
    leases = f"1 {lease} {IP} iPhone *\n" if lease else ""
    leases += f"1 {OTHER_MAC} {OTHER_IP} other *"          # the lease file may lack a final newline
    lines = "".join(f"{a} lladdr {m} STALE\n" for a, m in neigh)
    return f"{leases}\n\n{netdiag.LEASES_END}\n{lines}{extra}{netdiag.NEIGH_END}\n"


def apply(state, text, at):
    return netdiag.v6_apply_success(state, conf(), *netdiag.parse_router_tables(text, "br-lan"), at)


def fresh(at):
    return netdiag.v6_new_state(conf(), "s1", at, "pending-first-refresh")


def main() -> int:
    check_config()
    check_tables()
    check_identity()
    check_conflict_and_hold()
    check_failure_and_grace()
    check_restart()
    check_run_bounded()
    check_timeline()
    check_tracker_and_recorder()
    check_cross_process_and_collect()
    check_strict_parse()
    check_bad_snapshots()
    check_config_change_readers()
    check_record_survives_tracker_failure()
    print("netdiag IPv6 attribution tests passed")
    return 0


def check_config() -> None:
    assert netdiag.v6_config({"DEVICE_IPS": IP}) == (None, None)
    c = conf()
    assert c["devices"] == [(IP, MAC)] and c["lan_if"] == "br-lan" and c["ssh"] == "miwifi"
    bad = [
        {"DEVICE_IPS": IP, "DEVICE_MACS": MAC},                                   # partial
        {"DEVICE_IPS": IP, "ROUTER_SSH": "miwifi"},                               # partial
        dict(CFG, DEVICE_MACS=f"{MAC},{OTHER_MAC}"),                              # count mismatch
        dict(CFG, DEVICE_MACS="3a:00:00:00:00"),                                  # invalid MAC
        dict(CFG, DEVICE_IPS="192.0.2.999"),                                      # invalid IP
        dict(CFG, DEVICE_IPS=f"{IP},{IP}", DEVICE_MACS=f"{MAC},{OTHER_MAC}"),     # duplicate IP
        dict(CFG, DEVICE_IPS=f"{IP},{OTHER_IP}", DEVICE_MACS=f"{MAC},{MAC.upper()}"),  # duplicate MAC
        dict(CFG, ROUTER_SSH="-oProxyCommand=x"), dict(CFG, ROUTER_SSH="mi wifi"),
        dict(CFG, ROUTER_SSH="miwifi;id"), dict(CFG, ROUTER_LAN_IF="br-lan;id"),
        dict(CFG, ROUTER_LAN_IF="$(id)"),
    ]
    for cfg in bad:
        c, err = netdiag.v6_config(cfg)
        assert c is None and err, cfg
    assert netdiag.v6_config(dict(CFG, ROUTER_SSH="other"))[0]["fp"] != conf()["fp"]  # alias is in the fingerprint


def check_tables() -> None:
    extra = (f"{A2} lladdr {OTHER_MAC} REACHABLE\n"
             "2001:db8:1::d FAILED\n"
             "2001:db8:1::e STALE\n"
             f"2001:db8:1::f dev br-miot lladdr {MAC} STALE\n"
             f"fe80::1 lladdr {MAC} STALE\n"
             f"{ULA} lladdr {MAC.upper()} DELAY\n"
             f"2001:db8:1::10 lladdr {MAC} INCOMPLETE\n")
    leases, neigh = netdiag.parse_router_tables(tables(neigh=((A1_LONG, MAC),), extra=extra), "br-lan")
    assert leases == {IP: MAC, OTHER_IP: OTHER_MAC}, leases
    assert neigh == {A1: {MAC}, A2: {OTHER_MAC}, ULA: {MAC}}, neigh
    assert netdiag.norm6(A1_LONG) == netdiag.norm6(f"[{A1}]") == A1 and netdiag.norm6("192.0.2.1") is None


def check_identity() -> None:
    t0 = netdiag.now()
    s = fresh(t0)
    apply(s, tables(), t0)
    assert netdiag.attribute(s, A1_LONG, t0) == ("current", IP)
    assert netdiag.v6_status(s) == "ok" and netdiag.v6_incomplete(s, conf(), t0) == []
    # The lease now carries another MAC: revoke at once, no grace, and say so.
    t1 = t0 + timedelta(seconds=30)
    changes = apply(s, tables(lease=OTHER_MAC), t1)
    assert changes["revoked"] == 1 and netdiag.attribute(s, A1, t1) == (None, None), changes
    assert netdiag.v6_status(s).startswith("identity:") and s["ok"] is True
    assert any("identity mismatch" in r for r in netdiag.v6_incomplete(s, conf(), t1))
    # Revoked means gone: when the lease is right again, an address not seen since stays unattributed.
    t2 = t1 + timedelta(seconds=30)
    apply(s, tables(neigh=()), t2)
    assert A1 not in s["addrs"] and netdiag.attribute(s, A1, t2) == (None, None)
    s = fresh(t0)
    apply(s, tables(lease=None), t0)
    assert netdiag.attribute(s, A1, t0) == (None, None)
    assert any("identity no-lease" in r for r in netdiag.v6_incomplete(s, conf(), t0))
    # Another device's address is never attributed.
    s = fresh(t0)
    apply(s, tables(neigh=((A1, MAC), (A2, OTHER_MAC))), t0)
    assert netdiag.attribute(s, A2, t0) == (None, None) and s["others"] == [A2]


def check_conflict_and_hold() -> None:
    t = netdiag.now()
    step = timedelta(seconds=30)
    s = fresh(t)
    apply(s, tables(), t)
    t += step
    assert apply(s, tables(neigh=((A1, MAC), (A1, OTHER_MAC))), t)["conflicts"] == 1
    assert s["addrs"][A1]["state"] == "hold" and netdiag.attribute(s, A1, t) == (None, None)
    t += step
    apply(s, tables(), t)                                   # first clean read
    assert s["addrs"][A1]["state"] == "hold" and s["addrs"][A1]["clean_count"] == 1
    t += step
    apply(s, tables(neigh=()), t)                           # absent: not a clean read, resets
    assert s["addrs"][A1]["clean_count"] == 0
    t += step
    apply(s, tables(), t)
    netdiag.v6_apply_failure(s, "timeout", t + step)        # a failed attempt neither counts nor resets
    assert s["addrs"][A1]["clean_count"] == 1
    t += 2 * step
    apply(s, tables(neigh=((A1, MAC), (A1, OTHER_MAC))), t)  # conflict again resets
    assert s["addrs"][A1]["clean_count"] == 0
    t += step
    apply(s, tables(), t)
    t += step
    assert apply(s, tables(), t)["restored"] == 1
    assert netdiag.attribute(s, A1, t) == ("current", IP)
    # A hold outlives the 10-minute attribution window and is only dropped after V6_HOLD.
    t += step
    apply(s, tables(neigh=((A1, OTHER_MAC),)), t)            # moved to another MAC
    assert s["addrs"][A1]["state"] == "hold"
    late = t + timedelta(seconds=netdiag.V6_GRACE * 3)
    apply(s, tables(neigh=()), late)
    assert A1 in s["addrs"] and s["addrs"][A1]["state"] == "hold"
    apply(s, tables(), late + step)                          # reappearing is not a new address
    assert s["addrs"][A1]["state"] == "hold" and s["addrs"][A1]["clean_count"] == 1
    hold_until = netdiag._dt(s["addrs"][A1]["hold_until"])
    apply(s, tables(neigh=()), hold_until)
    assert A1 in s["addrs"]
    apply(s, tables(neigh=()), hold_until + timedelta(seconds=1))
    assert A1 not in s["addrs"]
    # An address another device used in the previous read needs two clean reads too.
    s = fresh(t)
    apply(s, tables(neigh=((A2, OTHER_MAC),)), t)
    apply(s, tables(neigh=((A2, MAC),)), t + step)
    assert s["addrs"][A2]["state"] == "hold" and s["addrs"][A2]["clean_count"] == 1
    apply(s, tables(neigh=((A2, MAC),)), t + 2 * step)
    assert netdiag.attribute(s, A2, t + 2 * step) == ("current", IP)


def check_failure_and_grace() -> None:
    t0 = netdiag.now()
    s = fresh(t0)
    apply(s, tables(), t0)
    expires = s["addrs"][A1]["expires_at"]
    # The first failure makes every address grace at once.
    netdiag.v6_apply_failure(s, "timeout", t0 + timedelta(seconds=30))
    assert netdiag.attribute(s, A1, t0 + timedelta(seconds=31)) == ("grace", IP)
    # Failing every 30 s for over 10 minutes: the snapshot keeps changing, the address never extends.
    versions = set()
    for k in range(2, 24):
        at = t0 + timedelta(seconds=30 * k)
        netdiag.v6_apply_failure(s, "exit-255: refused", at)
        versions.add(s["version"])
        assert s["last_success_at"] == netdiag._iso(t0)
        if A1 in s["addrs"]:
            assert s["addrs"][A1]["expires_at"] == expires
        reasons = netdiag.v6_incomplete(s, conf(), at)
        assert any("last attempt failed" in r for r in reasons), reasons
    assert len(versions) == 22 and A1 not in s["addrs"]
    edge = netdiag._dt(expires)
    s = fresh(t0)
    apply(s, tables(), t0)
    assert netdiag.attribute(s, A1, edge)[0] == "grace"
    assert netdiag.attribute(s, A1, edge + timedelta(milliseconds=1)) == (None, None)
    # The 90 s edge, with no further attempt at all (the recorder stalled).
    at90 = t0 + timedelta(seconds=netdiag.V6_CURRENT)
    assert netdiag.attribute(s, A1, at90)[0] == "current"
    assert netdiag.attribute(s, A1, at90 + timedelta(milliseconds=1))[0] == "grace"
    reasons = netdiag.v6_incomplete(s, conf(), at90 + timedelta(seconds=1))
    assert any("not refreshing" in r for r in reasons) and any("no successful refresh" in r for r in reasons)
    # Present in an older read but missing from the latest successful one: grace, not current.
    apply(s, tables(neigh=()), t0 + timedelta(seconds=30))
    assert netdiag.attribute(s, A1, t0 + timedelta(seconds=31))[0] == "grace"


def check_restart() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "v6map.json"
        t0 = netdiag.now()
        s = fresh(t0)
        apply(s, tables(neigh=((A1, MAC), (ULA, MAC))), t0)
        apply(s, tables(neigh=((A1, MAC), (ULA, MAC), (ULA, OTHER_MAC))), t0 + timedelta(seconds=5))
        apply(s, tables(neigh=((A1, MAC), (ULA, MAC))), t0 + timedelta(seconds=8))  # ULA: first clean read
        assert s["addrs"][ULA]["clean_count"] == 1
        path.write_text(json.dumps(s))
        t1 = t0 + timedelta(seconds=10)
        r = netdiag.v6_load(path, conf(), "s2", t1)
        assert r["ok"] is False and r["failure_reason"] == "pending-first-refresh" and not r["session_refreshed"]
        # Ten seconds after a good read, but this session has not refreshed: grace, never current.
        assert netdiag.attribute(r, A1, t1) == ("grace", IP)
        assert any("pending-first-refresh" in x for x in netdiag.v6_incomplete(r, conf(), t1))
        assert r["addrs"][ULA]["state"] == "hold" and r["addrs"][ULA]["clean_count"] == 1
        apply(r, tables(neigh=((A1, MAC), (ULA, MAC))), t1 + timedelta(seconds=1))
        assert r["addrs"][ULA]["state"] == "active" and netdiag.attribute(r, A1, t1 + timedelta(seconds=2))[0] == "current"
        other = netdiag.v6_config(dict(CFG, ROUTER_SSH="other"))[0]
        r = netdiag.v6_load(path, other, "s3", t1)
        assert r["addrs"] == {} and "config changed" in r["reset"]
        path.write_text("{broken")
        r = netdiag.v6_load(path, conf(), "s4", t1)
        assert r["addrs"] == {} and "unreadable" in r["reset"]
        r = netdiag.v6_load(Path(tmp) / "missing.json", conf(), "s5", t1)
        assert r["addrs"] == {} and r["ok"] is False


def gone(pid: int) -> bool:
    return not Path(f"/proc/{pid}").exists()


def check_run_bounded() -> None:
    py = sys.executable
    limit = netdiag.V6_OUTPUT_LIMIT
    start = time.monotonic()
    rc, out, err, failure, pid = netdiag.run_bounded(
        [py, "-c", "import sys\nwhile True: sys.stdout.write('x' * 4096)"], timeout=10)
    assert failure == "output-too-large" and len(out) + len(err) <= limit + 1, (failure, len(out))
    assert time.monotonic() - start < 2 and gone(pid)
    _, out, err, failure, pid = netdiag.run_bounded([py, "-c", "import sys; sys.stdout.write('y' * 300000)"])
    assert failure == "output-too-large" and len(out) <= limit + 1 and gone(pid)
    _, out, err, failure, pid = netdiag.run_bounded(
        [py, "-c", "import sys; sys.stdout.write('a' * 200000); sys.stdout.flush(); sys.stderr.write('b' * 100000)"])
    assert failure == "output-too-large" and len(out) + len(err) <= limit + 1 and gone(pid)
    start = time.monotonic()
    _, _, _, failure, pid = netdiag.run_bounded([py, "-c", "import time; time.sleep(30)"], timeout=1)
    assert failure == "timeout" and time.monotonic() - start < 3 and gone(pid)
    # Both pipes closed (EOF) but the process keeps running: the deadline still covers the wait.
    start = time.monotonic()
    _, _, _, failure, pid = netdiag.run_bounded(
        [py, "-c", "import os, time; os.close(1); os.close(2); time.sleep(30)"], timeout=1)
    assert failure == "timeout" and time.monotonic() - start < 3 and gone(pid)
    rc, out, err, failure, pid = netdiag.run_bounded([py, "-c", "import sys; print('ok'); sys.exit(3)"])
    assert rc == 3 and failure is None and out == b"ok\n" and gone(pid)


def write_events(state: Path, events: list[dict]) -> None:
    for e in events:
        day = state / "records" / e["t"][:10]
        day.mkdir(parents=True, exist_ok=True)
        with open(day / "v6map.jsonl", "a") as f:
            f.write(json.dumps(e) + "\n")


def check_timeline() -> None:
    tz = netdiag.now().tzinfo
    base = netdiag.datetime(2026, 10, 8, 12, 0, tzinfo=tz)
    start, end = base, base + timedelta(minutes=10)

    def ev(sec: int, status: str = "ok") -> dict:
        return {"t": (base + timedelta(seconds=sec)).isoformat(), "status": status}

    def run(events):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(netdiag, "STATE", Path(tmp)):
            write_events(Path(tmp), events)
            return netdiag.v6_timeline(start, end)[0]

    def statuses(segs):
        return [(int((a - base).total_seconds()), int((b - base).total_seconds()), s) for a, b, s in segs]

    healthy = [ev(s) for s in range(-30, 600, 30)]
    assert statuses(run(healthy)) == [(0, 600, "ok")]
    # Already failing before the window; the window only holds the recovery.
    segs = statuses(run([ev(-20, "failed: timeout")] + [ev(s) for s in range(60, 600, 30)]))
    assert segs[0] == (0, 60, "failed: timeout") and segs[-1] == (60, 600, "ok"), segs
    # No event in the window, with and without an anchor.
    assert statuses(run([ev(-30)])) == [(0, 60, "ok"), (60, 600, "unknown (no refresh observed)")]
    assert statuses(run([])) == [(0, 600, "unknown (no refresh observed)")]
    assert statuses(run([ev(-300)])) == [(0, 600, "unknown (no refresh observed)")]
    # Recorder stopped after the last event.
    segs = statuses(run([ev(s) for s in range(-30, 300, 30)]))
    assert segs == [(0, 360, "ok"), (360, 600, "unknown (no refresh observed)")], segs
    # A failure inside the window stays listed after the recovery.
    events = [ev(s) for s in range(-30, 120, 30)] + [ev(120, "failed: exit-255")] + [ev(s) for s in range(150, 600, 30)]
    assert (120, 150, "failed: exit-255") in statuses(run(events))
    # The anchor lives in the previous day's file (window starts just after midnight).
    midnight = netdiag.datetime(2026, 10, 8, 0, 0, 10, tzinfo=tz)
    with tempfile.TemporaryDirectory() as tmp, mock.patch.object(netdiag, "STATE", Path(tmp)):
        write_events(Path(tmp), [{"t": (midnight - timedelta(seconds=40)).isoformat(), "status": "failed: timeout"}])
        segs = netdiag.v6_timeline(midnight, midnight + timedelta(minutes=2))[0]
    assert (midnight - timedelta(seconds=40)).date() != midnight.date()
    assert segs[0][2] == "failed: timeout" and segs[0][1] == midnight + timedelta(seconds=50), segs


FAKE_SSH = """#!/usr/bin/env python3
import json, os, sys
d = os.environ["FAKE_SSH_DIR"]
open(os.path.join(d, "argv.json"), "w").write(json.dumps(sys.argv[1:]))
sys.stdout.write(open(os.path.join(d, "out.txt")).read())
sys.exit(int(open(os.path.join(d, "rc.txt")).read()))
"""


def fake_ssh(tmp: Path, text: str, rc: int = 0) -> Path:
    exe = tmp / "fake-ssh"
    exe.write_text(FAKE_SSH)
    exe.chmod(0o700)
    (tmp / "out.txt").write_text(text)
    (tmp / "rc.txt").write_text(str(rc))
    return exe


class Stop(BaseException):
    pass


def check_tracker_and_recorder() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        state_dir = tmp / "state"
        state_dir.mkdir()
        exe = fake_ssh(tmp, tables(neigh=((A1, MAC), (A2, OTHER_MAC))))
        with mock.patch.dict(os.environ, {"FAKE_SSH_DIR": str(tmp)}), \
                mock.patch.multiple(netdiag, STATE=state_dir, SSH_BIN=str(exe)):
            status, events = netdiag.Writer("recorder"), netdiag.Writer("v6map")
            tr = netdiag.V6Tracker(conf(), status, events)
            assert tr.state["ok"] is False                         # pending until the first read
            # Lines seen before the first refresh are not written; they are counted.
            assert tr.classify(A1, netdiag.now()) == (None, None)
            tr.refresh_once()
            argv = json.loads((tmp / "argv.json").read_text())
            assert argv[:4] == ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5"] and argv[4] == "miwifi", argv
            assert argv[5] == netdiag.v6_remote_command("br-lan") and len(argv) == 6, argv
            assert argv[5].count(" && ") == 4, argv           # every step must succeed
            snap = netdiag.v6_read_state()
            assert snap["ok"] and snap["version"] == tr.state["version"]
            label, version = tr.classify(A1_LONG, netdiag.now())
            assert label == "current" and version == snap["version"]
            assert tr.classify(A2, netdiag.now()) == (None, None) and tr.counts["other_dropped"] == 1
            assert tr.classify("not:an-address", netdiag.now()) == (None, None) and not tr.unknown
            for k in range(netdiag.V6_UNKNOWN_CAP + 5):
                tr.classify(f"2001:db8:9::{k:x}", netdiag.now())
            assert len(tr.unknown) == netdiag.V6_UNKNOWN_CAP and tr.counts["overflow"] == 5
            (tmp / "rc.txt").write_text("255")
            tr.refresh_once()
            assert tr.state["ok"] is False and tr.state["failure_reason"].startswith("exit-255")
            rows = netdiag.read_v6_events(netdiag.now().date(), netdiag.now().date())
            first = [r for r in rows if r.get("kind") == "attempt"][0]
            assert first["missed_before_attribution"] == 1 and first["status"] == "ok", first
            last = rows[-1]
            assert last["status"].startswith("failed: exit-255") and last["overflow"] == 5, last
            assert last["unknown_dropped"] == netdiag.V6_UNKNOWN_CAP, last
            assert tr.unknown == {} and tr.counts["overflow"] == 0          # settled and cleared
            assert any(r.get("kind") == "start" for r in rows)
            # The log stream: the device's IPv6 line is recorded with its label, another device's is not.
            (tmp / "rc.txt").write_text("0")
            tr.refresh_once()
            lines = [json.dumps({"type": "info", "payload": p}).encode() + b"\n" for p in (
                f"[TCP] [{A1}]:1 --> a.example:443 match X using DIRECT",
                f"[TCP] [{A2}]:1 --> b.example:443 match X using DIRECT",
                f"[TCP] {IP}:1 --> c.example:443 match X using DIRECT")]
            calls = []

            def opener(_req, _timeout):
                calls.append(1)
                if len(calls) > 1:
                    raise Stop()
                return mock.MagicMock(__enter__=lambda s: iter(lines), __exit__=lambda *a: False)

            out = netdiag.Writer("router")
            with mock.patch.object(netdiag, "api_open", opener), mock.patch.object(netdiag.time, "sleep"):
                try:
                    netdiag.record_router({"ROUTER_API": "http://r"}, [IP], out, status, tr)
                except Stop:
                    pass
            day = state_dir / "records" / netdiag.now().strftime("%Y-%m-%d")
            written = [json.loads(x) for x in (day / "router.jsonl").read_text().splitlines()]
            assert [w["payload"].split("-->")[1].split(":")[0].strip() for w in written] == ["a.example", "c.example"]
            assert written[0]["v6"] == "current" and written[0]["v6map"] == tr.state["version"]
            assert "v6" not in written[1]


def check_cross_process_and_collect() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        state_dir = tmp / "state"
        state_dir.mkdir()
        exe = fake_ssh(tmp, tables(neigh=((A1, MAC), (A2, OTHER_MAC))))
        env = dict(os.environ, FAKE_SSH_DIR=str(tmp), NETDIAG_STATE=str(state_dir))
        code = ("import sys; sys.path.insert(0, %r); import netdiag; netdiag.SSH_BIN = %r; "
                "c, _ = netdiag.v6_config(%r); "
                "t = netdiag.V6Tracker(c, netdiag.Writer('recorder'), netdiag.Writer('v6map')); t.refresh_once(); "
                "print(t.state['version'])") % (str(ROOT / "scripts"), str(exe), CFG)
        version = int(subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                                     check=True).stdout.strip())
        conn = lambda src, host: {"metadata": {"sourceIP": src, "host": host, "network": "tcp",  # noqa: E731
                                               "destinationPort": "443"}, "rule": "Domain", "chains": ["DIRECT"]}
        snapshot = {"connections": [conn(A1_LONG, "mine.example"), conn(A2, "other.example"), conn(IP, "v4.example")]}
        cfg = dict(CFG, ROUTER_API="http://r")
        with mock.patch.multiple(netdiag, STATE=state_dir, load_config=lambda: cfg, service_state=lambda: "active",
                                 api_get=lambda *_a, **_k: snapshot,
                                 probe=lambda _c, source: "reachable" if source == "router" else "not configured"):
            assert netdiag.v6_read_state()["version"] == version
            line = netdiag.v6_status_line(cfg)
            assert f"snapshot v{version}" in line and "current 1" in line and "INCOMPLETE" not in line, line
            out = io.StringIO()
            with redirect_stdout(out):
                assert netdiag.cmd_collect(mock.Mock(since=timedelta(minutes=5))) == 0
            report = out.getvalue()
            for needle in ("mine.example", "v4.example", "IPv6 attribution", "lower bounds"):
                assert needle in report, (needle, report)
            assert "other.example" not in report, report
            # Damaged snapshot: readers fall back to IPv4 and say why.
            (state_dir / "v6map.json").write_text("{broken")
            out = io.StringIO()
            with redirect_stdout(out):
                netdiag.cmd_collect(mock.Mock(since=timedelta(minutes=5)))
            report = out.getvalue()
            assert "mine.example" not in report and "v4.example" in report, report
            assert "INCOMPLETE: IPv6 current snapshot: IPv6 attribution snapshot unreadable" in report, report
            assert "INCOMPLETE: IPv6 attribution snapshot unreadable" in netdiag.v6_status_line(cfg)
        for extra, needle in (({"ROUTER_SSH": "", "DEVICE_MACS": ""}, "IPv6: not attributed (IPv4/explicit DEVICE_IPS only)"),
                              ({"DEVICE_MACS": "bad"}, "INCOMPLETE: IPv6 attribution config error")):
            with tempfile.TemporaryDirectory() as tmp2:
                c2 = dict(cfg, **extra)
                out = io.StringIO()
                with mock.patch.multiple(netdiag, STATE=Path(tmp2), load_config=lambda: c2,
                                         service_state=lambda: "active", api_get=lambda *_a, **_k: snapshot,
                                         probe=lambda _c, s: "reachable" if s == "router" else "not configured"), \
                        redirect_stdout(out):
                    netdiag.cmd_collect(mock.Mock(since=timedelta(minutes=5)))
                report = out.getvalue()
                assert needle in report and "mine.example" not in report and "v4.example" in report, (needle, report)


def check_strict_parse() -> None:
    """A read that is cut off, reordered or of an unknown shape is a failure, never an empty table."""
    ok = tables(neigh=((A1, MAC),), extra=f"{A2} dev br-lan lladdr {OTHER_MAC} router REACHABLE\n2001:db8::9 FAILED\n")
    leases, neigh = netdiag.parse_router_tables(ok, "br-lan")
    assert leases[IP] == MAC and neigh == {A1: {MAC}, A2: {OTHER_MAC}}
    empty = f"\n{netdiag.LEASES_END}\n{netdiag.NEIGH_END}\n"
    assert netdiag.parse_router_tables(empty, "br-lan") == ({}, {})       # empty tables are valid
    leases_only = ok.split(netdiag.LEASES_END)[0]
    bad = {
        "leases only (no markers)": leases_only,
        "neighbour table cut off": ok.split(netdiag.NEIGH_END)[0],
        "old separator": ok.replace(netdiag.LEASES_END, "---").replace(netdiag.NEIGH_END + "\n", ""),
        "markers reversed": f"{netdiag.NEIGH_END}\n{netdiag.LEASES_END}\n",
        "marker twice": ok + netdiag.NEIGH_END + "\n",
        "trailing data": ok + "junk\n",
        "unknown neighbour shape": tables(extra=f"{A2} lladdr {OTHER_MAC} STALE extra\n"),
        "bad neighbour MAC": tables(extra=f"{A2} lladdr zz:zz STALE\n"),
        "lladdr without MAC": tables(extra="2001:db8:1::e lladdr STALE\n"),
        "unknown lease shape": f"garbage line\n{netdiag.LEASES_END}\n{netdiag.NEIGH_END}\n",
        "IPv4 lease without MAC": f"1 nomac {IP} h *\n{netdiag.LEASES_END}\n{netdiag.NEIGH_END}\n",
    }
    for name, text in bad.items():
        try:
            netdiag.parse_router_tables(text, "br-lan")
        except netdiag.V6ParseError:
            continue
        raise AssertionError(f"accepted: {name}")
    two = f"1 {MAC} {IP} a *\n1 {OTHER_MAC} {IP} b *\n{netdiag.LEASES_END}\n{netdiag.NEIGH_END}\n"
    assert netdiag.parse_router_tables(two, "br-lan")[0][IP] == "conflict"   # never matches a device
    s = fresh(netdiag.now())
    apply(s, two, netdiag.now())
    assert s["identity"][IP] == "mismatch"
    # Through refresh_once with rc=0: the cut-off read is a failed attempt, not a healthy refresh.
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "state").mkdir()
        exe = fake_ssh(tmp, tables())
        with mock.patch.dict(os.environ, {"FAKE_SSH_DIR": str(tmp)}), \
                mock.patch.multiple(netdiag, STATE=tmp / "state", SSH_BIN=str(exe)):
            tr = netdiag.V6Tracker(conf(), netdiag.Writer("recorder"), netdiag.Writer("v6map"))
            tr.refresh_once()
            good_at = tr.state["last_success_at"]
            assert tr.state["ok"] and netdiag.attribute(tr.state, A1, netdiag.now())[0] == "current"
            for text in (leases_only, ok.split(netdiag.NEIGH_END)[0], ""):
                (tmp / "out.txt").write_text(text)
                tr.refresh_once()
                assert tr.state["ok"] is False and tr.state["failure_reason"].startswith("parse:"), tr.state
                assert tr.state["last_success_at"] == good_at
                assert netdiag.attribute(tr.state, A1, netdiag.now())[0] == "grace"
                snap, reasons = netdiag.v6_snapshot(conf(), netdiag.now())
                assert snap and any("last attempt failed: parse:" in r for r in reasons), reasons


def valid_snapshot(at):
    s = fresh(at)
    apply(s, tables(neigh=((A1, MAC), (A2, OTHER_MAC))), at)
    apply(s, tables(neigh=((A1, MAC), (ULA, MAC), (ULA, OTHER_MAC))), at)   # ULA becomes a hold
    return s


def check_bad_snapshots() -> None:
    """Well-formed JSON with a broken structure is never used, never crashes, and restarts empty."""
    at = netdiag.now()
    base = valid_snapshot(at)
    assert netdiag.v6_validate(base, conf()) is None and base["addrs"][ULA]["state"] == "hold"

    def broken(edit):
        s = json.loads(json.dumps(base))
        edit(s)
        return s

    cases = {
        "only schema, fp and addrs": {"schema": 1, "config_fp": conf()["fp"], "addrs": {}},
        "no identity": broken(lambda s: s.pop("identity")),
        "no version": broken(lambda s: s.pop("version")),
        "version is text": broken(lambda s: s.update(version="7")),
        "addrs is a list": broken(lambda s: s.update(addrs=[])),
        "entry without expires_at": broken(lambda s: s["addrs"][A1].pop("expires_at")),
        "hold without hold_until": broken(lambda s: s["addrs"][ULA].update(hold_until=None)),
        "time without offset": broken(lambda s: s["addrs"][A1].update(expires_at="2026-10-08T12:00:00")),
        "time not a time": broken(lambda s: s.update(last_attempt_at="yesterday")),
        "unknown device": broken(lambda s: s["addrs"][A1].update(device="192.0.2.99")),
        "unnormalised address key": broken(lambda s: s["addrs"].update({A1_LONG: s["addrs"].pop(A1)})),
        "identity value unknown": broken(lambda s: s["identity"].update({IP: "fine"})),
        "ok is text": broken(lambda s: s.update(ok="true")),
        "not an object": [1, 2],
        "device is a list": broken(lambda s: s["addrs"][A1].update(device=[IP])),
        "device is an object": broken(lambda s: s["addrs"][A1].update(device={IP: 1})),
    }
    snapshot = {"connections": [
        {"metadata": {"sourceIP": A1, "host": "mine.example", "network": "tcp", "destinationPort": "443"},
         "rule": "Domain", "chains": ["DIRECT"]},
        {"metadata": {"sourceIP": IP, "host": "v4.example", "network": "tcp", "destinationPort": "443"},
         "rule": "Domain", "chains": ["DIRECT"]}]}
    cfg = dict(CFG, ROUTER_API="http://r")
    for name, bad in cases.items():
        assert netdiag.v6_validate(bad, conf()), name
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / "v6map.json").write_text(json.dumps(bad))
            with mock.patch.multiple(netdiag, STATE=state_dir, load_config=lambda: cfg,
                                     service_state=lambda: "active", api_get=lambda *_a, **_k: snapshot,
                                     probe=lambda _c, s: "reachable" if s == "router" else "not configured"):
                snap, reasons = netdiag.v6_snapshot(conf(), at)
                assert snap is None and "not used" in reasons[0], (name, reasons)
                assert "IPv4 only" in netdiag.v6_status_line(cfg), name
                out = io.StringIO()
                with redirect_stdout(out):
                    assert netdiag.cmd_collect(mock.Mock(since=timedelta(minutes=5))) == 0
                report = out.getvalue()
                assert "v4.example" in report and "mine.example" not in report, (name, report)
                assert "IPv6 attribution snapshot not used" in report, (name, report)
                r = netdiag.v6_load(state_dir / "v6map.json", conf(), "s9", at)
                assert r["addrs"] == {} and "reset" in (r["reset"] or ""), (name, r["reset"])
                assert netdiag.v6_validate(r, conf()) is None, name
                tr = netdiag.V6Tracker(conf(), netdiag.Writer("recorder"), netdiag.Writer("v6map"))
                assert tr.state["addrs"] == {} and tr.state["ok"] is False, name
    # Every field of a valid snapshot, swapped for each wrong JSON type: always a reason, never an exception.
    def paths(obj, prefix=()):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield prefix + (k,)
                yield from paths(v, prefix + (k,))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                yield prefix + (i,)
                yield from paths(v, prefix + (i,))

    def swapped(path, value):
        s = json.loads(json.dumps(base))
        node = s
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = value
        return s

    # Swaps that are valid values in their own right; everything else must be refused.
    allowed = {(("addrs",), "{}"), (("failure_reason",), "'x'"), (("last_success_at",), "None"),
               (("reset",), "'x'"), (("session",), "'x'"), (("version",), "0"), (("addrs", A1, "present"), "True"),
               (("addrs", ULA, "present"), "True"), (("addrs", A1, "hold_until"), "None")}
    tried = 0
    for path in list(paths(base)):
        for value in ([], {}, None, 0, -1, 1.5, True, "x", [IP], {IP: IP}):
            bad = swapped(path, value)
            if bad == base:
                continue
            problem = netdiag.v6_validate(bad, conf())          # must not raise
            tried += 1
            if problem is None:
                assert (path, repr(value)) in allowed, (path, value)
    assert tried > 300, tried
    with tempfile.TemporaryDirectory() as tmp, mock.patch.object(netdiag, "STATE", Path(tmp)):
        for path in list(paths(base))[:40]:
            (Path(tmp) / "v6map.json").write_text(json.dumps(swapped(path, [IP])))
            netdiag.v6_status_line(dict(CFG))                    # must not raise
            netdiag.v6_snapshot(conf(), at)
            netdiag.v6_load(Path(tmp) / "v6map.json", conf(), "s11", at)
    # A valid snapshot survives the round trip through v6_load.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "v6map.json"
        path.write_text(json.dumps(base))
        r = netdiag.v6_load(path, conf(), "s10", at)
        assert A1 in r["addrs"] and r["addrs"][ULA]["state"] == "hold" and r["reset"] is None


def check_config_change_readers() -> None:
    """A snapshot made for another config is not used by collect or status, even when healthy."""
    at = netdiag.now()
    base = valid_snapshot(at)
    snapshot = {"connections": [
        {"metadata": {"sourceIP": A1, "host": "wrong-device.example", "network": "tcp", "destinationPort": "443"},
         "rule": "Domain", "chains": ["DIRECT"]},
        {"metadata": {"sourceIP": IP, "host": "v4.example", "network": "tcp", "destinationPort": "443"},
         "rule": "Domain", "chains": ["DIRECT"]}]}
    for change in ({"ROUTER_SSH": "different-router"}, {"DEVICE_MACS": OTHER_MAC}, {"ROUTER_LAN_IF": "br-guest"}):
        cfg = dict(CFG, ROUTER_API="http://r", **change)
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / "v6map.json").write_text(json.dumps(base))
            with mock.patch.multiple(netdiag, STATE=state_dir, load_config=lambda: cfg,
                                     service_state=lambda: "active", api_get=lambda *_a, **_k: snapshot,
                                     probe=lambda _c, s: "reachable" if s == "router" else "not configured"):
                line = netdiag.v6_status_line(cfg)
                assert "different IPv6 config" in line and "current" not in line and "grace" not in line, line
                out = io.StringIO()
                with redirect_stdout(out):
                    assert netdiag.cmd_collect(mock.Mock(since=timedelta(minutes=5))) == 0
                report = out.getvalue()
                assert "wrong-device.example" not in report and "v4.example" in report, (change, report)
                assert "made with a different IPv6 config" in report, report


def check_record_survives_tracker_failure() -> None:
    """If the IPv6 tracker cannot start, the router recorder still starts and the failure is reported."""
    names = []

    class FakeThread:
        def __init__(self, name, target, args=(), daemon=None):
            self.name = name
            names.append(name)

        def start(self):
            pass

        def join(self, timeout=None):
            pass

        def is_alive(self):
            return False

    def boom(*_a, **_k):
        raise OSError("disk full")

    with tempfile.TemporaryDirectory() as tmp:
        cfg = dict(CFG, ROUTER_API="http://r")
        with mock.patch.multiple(netdiag, STATE=Path(tmp), load_config=lambda: cfg, V6Tracker=boom, prune=lambda: None), \
                mock.patch.object(netdiag.threading, "Thread", FakeThread):
            try:
                netdiag.cmd_record(None)
            except RuntimeError as exc:
                assert "router" in str(exc), exc
            rows = netdiag.read_records("recorder", netdiag.now() - timedelta(minutes=1), netdiag.now())
    assert names == ["router"], names
    assert any(r.get("source") == "ipv6" and "start failed: disk full" in r.get("error", "") for r in rows), rows


if __name__ == "__main__":
    raise SystemExit(main())
