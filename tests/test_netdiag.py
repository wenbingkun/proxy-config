#!/usr/bin/env python3
"""Offline checks for scripts/netdiag.py: router log parsing, Surge problem detection and the report."""
from __future__ import annotations

import io
import re
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import netdiag  # noqa: E402

DEVICE = "192.0.2.60"


def main() -> int:
    ok = netdiag.parse_router(f"[TCP] {DEVICE}:60641 --> gspe79-cn-ssl.ls.apple.com:443 "
                              "match RuleSet(Apple) using 🍎 苹果服务[DIRECT]")
    assert ok and ok["host"] == "gspe79-cn-ssl.ls.apple.com" and ok["port"] == "443", ok
    assert ok["rule"] == "RuleSet(Apple)" and ok["policy"] == "🍎 苹果服务[DIRECT]", ok
    final = netdiag.parse_router(f"[UDP] {DEVICE}:5353 --> 1.2.3.4:443 doesn't match any rule using DIRECT")
    assert final and final["policy"] == "DIRECT" and final["rule"] is None, final
    dial_error = (f"[TCP] dial 🤖 人工智能 (match RuleSet/Gemini) {DEVICE}:51234 --> "
                  "gemini.google.com:443 error: i/o timeout")
    assert netdiag.parse_router(dial_error) is None

    t0 = datetime(2026, 10, 4, 15, 0, tzinfo=datetime.now().astimezone().tzinfo)
    good = {"id": 1, "startDate": t0.timestamp() + 10, "remoteHost": "www.apple.com:443",
            "policyName": "DIRECT", "rule": "SUBNET", "status": "Completed", "completed": True,
            "notes": ["[Rule] Policy decision path: DIRECT"]}
    bad = {"id": 2, "startDate": t0.timestamp() + 20, "remoteHost": "api.example.com:443",
           "policyName": "🚀 节点选择", "rule": "FINAL", "status": "Completed", "failed": True,
           "notes": ["[Connect] Connection to proxy failed: timed out"],
           "timingRecords": [{"name": "Rule Evaluating", "durationInMillisecond": 4200}]}
    assert netdiag.surge_problems(good) == []
    issues = netdiag.surge_problems(bad)
    assert "failed" in issues and any("timed out" in i for i in issues) and any("slow" in i for i in issues), issues

    old = dict(good, id=3, startDate=t0.timestamp() - 3600)
    merged, earlier = netdiag.merge_surge([{"t": t0.isoformat(), "request": dict(bad, failed=False)}],
                                          [good, bad, old], t0, t0 + timedelta(minutes=5))
    assert [r["id"] for r in merged] == [1, 2] and merged[1]["failed"] is True and earlier == 1, merged

    ctx = {"start": t0, "end": t0 + timedelta(minutes=5), "devices": [DEVICE], "sources": ["test"],
           "router": [{"t": t0.isoformat(), "level": "info",
                       "payload": f"[TCP] {DEVICE}:1 --> www.apple.com:443 match RuleSet(Apple) using DIRECT"},
                      {"t": t0.isoformat(), "level": "warning", "payload": dial_error}],
           "surge": merged, "session": "/tmp/x"}
    report = netdiag.build_report(ctx)
    for needle in ("## Problems", "api.example.com:443", "gemini.google.com", "## Surge requests (2)",
                   "## Router connections", "www.apple.com"):
        assert needle in report, (needle, report)

    blocked = {"id": 4, "startDate": t0.timestamp() + 30, "remoteHost": "ad.example.com:443", "policyName": "REJECT",
               "rejected": True, "failed": True, "rule": "SUBNET SSID:MyHome", "notes": ["pre-matching reject"]}
    assert netdiag.surge_problems(blocked) == []
    report = netdiag.build_report(dict(ctx, surge=merged + [blocked]))
    assert "## Surge rejected (1" in report and "MyHome" not in report and "SSID:<home>" in report, report

    check_window(t0, good)
    check_ssid()
    check_router_source()
    check_config_example()
    check_collect_degrades(t0, good)
    assert netdiag.parse_since("15m") == timedelta(minutes=15)
    print("netdiag tests passed")
    return 0


def check_window(t0, good) -> None:
    """A request belongs to the window by its startDate, never by when the recorder sampled it."""
    end = t0 + timedelta(minutes=15)
    sampled_now = (t0 + timedelta(minutes=10)).isoformat()
    restart_old = dict(good, id=7, startDate=t0.timestamp() - 3600)    # recorder restart re-reads it
    finished_old = dict(good, id=8, startDate=t0.timestamp() - 60, completed=True)
    at_start = dict(good, id=9, startDate=t0.timestamp())
    at_end = dict(good, id=10, startDate=end.timestamp())
    after = dict(good, id=11, startDate=end.timestamp() + 1)
    reused = dict(good, id=9, startDate=t0.timestamp() + 30)            # same id after a VPN restart
    rows = [{"t": sampled_now, "session": "s1", "request": r} for r in (restart_old, finished_old, at_start, at_end, reused)]
    merged, earlier = netdiag.merge_surge(rows, [after], t0, end)
    assert [(r["id"], r["startDate"]) for r in merged] == [
        (9, t0.timestamp()), (9, t0.timestamp() + 30), (10, end.timestamp())], merged
    assert earlier == 2, earlier
    s = lambda sid, *ids: [{"id": i, "_session": sid} for i in ids]  # noqa: E731
    assert netdiag.id_gaps(s("a", 1, 4, 5)) == (2, 0)
    assert netdiag.id_gaps(s("a", 98, 99) + s("b", 1, 2)) == (0, 0)        # restart, both runs complete
    assert netdiag.id_gaps(s("a", 5, 6, 7) + s("b", 1, 2, 6)) == (3, 0)    # overlapping numbers, gap in b only
    assert netdiag.id_gaps([{"id": 98}, {"id": 99}, {"id": 1}, {"id": 2}]) == (None, 4)  # cannot tell runs apart
    assert netdiag.id_gaps(s("a", 1, 2) + [{"id": 9}]) == (0, 1)            # a live-only request is left out
    assert netdiag.burst_check(0, [1, 2, 3]) == (3, False, False)           # first poll
    assert netdiag.burst_check(10, list(range(5, 16))) == (15, False, False)  # steady
    assert netdiag.burst_check(10, list(range(20, 70))) == (69, True, False)  # gap: some were missed
    assert netdiag.burst_check(10, list(range(11, 41))) == (40, True, False)  # 30 new at once
    assert netdiag.burst_check(4653, [1, 2]) == (2, False, True)            # tunnel restart
    assert netdiag.burst_check(99, [1, 2]) == (2, False, True)              # restart with close numbers


def check_ssid() -> None:
    for raw in ("SUBNET,SSID:My Home Network,DIRECT", "SUBNET SSID:My Home Network (在家直连)",
                'SUBNET,SSID:"My Home, 5G",DIRECT', "SUBNET SSID:" + "Long Name " * 20):
        out = netdiag.mask(raw)
        assert "Home" not in out and "Long" not in out and "SSID:<home>" in out, (raw, out)
    assert netdiag.mask("SUBNET SSID:My Home Network (在家直连)").endswith("(在家直连)")
    cell = netdiag.table(["rule"], [["SUBNET SSID:" + "Long Name " * 20]])
    assert "Long" not in cell and "SSID:<home>" in cell, cell


def check_router_source() -> None:
    assert netdiag.source_ip(f"[TCP] {DEVICE}:1 --> a.com:443 match X using DIRECT") == DEVICE
    assert netdiag.source_ip(f"[TCP] 192.0.2.5:1 --> {DEVICE}:62078 match X using DIRECT") == "192.0.2.5"
    assert netdiag.source_ip(f"[TCP] 192.0.2.160:1 --> a.com:443 match X using DIRECT") != DEVICE
    assert netdiag.source_ip("[UDP] [fe80::1]:5353 --> b.com:443 match X using DIRECT") == "fe80::1"


def check_config_example() -> None:
    """The env example in docs/netdiag.md must parse into usable URLs."""
    doc = (ROOT / "docs" / "netdiag.md").read_text(encoding="utf-8")
    block = re.search(r"```sh\n(.*?ROUTER_API.*?)```", doc, re.S)[1]
    block = re.sub(r"<[^>]+>", "192.0.2.60", block.replace("    ", ""))
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / "env"
        env.write_text("\n".join(line.strip() for line in block.splitlines()))
        with mock.patch.object(netdiag, "CONFIG", env):
            cfg = netdiag.load_config()
    for key in ("ROUTER_API", "SURGE_API"):
        assert re.fullmatch(r"http://[0-9.]+:\d+", cfg[key]), (key, cfg[key])


def check_collect_degrades(t0, good) -> None:
    """A live fetch that fails after probe() said reachable must not lose the recorded data."""
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        rec = state / "records" / netdiag.now().strftime("%Y-%m-%d")
        rec.mkdir(parents=True)
        recent = dict(good, id=21, startDate=netdiag.now().timestamp() - 30)
        (rec / "surge.jsonl").write_text(
            '{"t": "%s", "request": %s}\n' % (netdiag.now().isoformat(), __import__("json").dumps(recent)))
        cfg = {"ROUTER_API": "http://r", "SURGE_API": "http://s", "DEVICE_IPS": DEVICE}

        def boom(*_a, **_k):
            raise TimeoutError("timed out")

        args = mock.Mock(since=timedelta(minutes=5))
        out = io.StringIO()
        with mock.patch.multiple(netdiag, STATE=state, load_config=lambda: cfg, api_get=boom,
                                 probe=lambda *_a: "reachable", service_state=lambda: "active"), \
                redirect_stdout(out):
            assert netdiag.cmd_collect(args) == 0
    report = out.getvalue()
    assert "INCOMPLETE: router /connections failed: TimeoutError" in report, report
    assert "INCOMPLETE: Surge /v1/requests/recent failed" in report, report
    assert "Surge requests started in window: 1" in report and "www.apple.com" in report, report


if __name__ == "__main__":
    raise SystemExit(main())
