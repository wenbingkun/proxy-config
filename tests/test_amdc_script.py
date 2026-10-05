#!/usr/bin/env python3
"""Run the hosted quantumultx/scripts/amdc.js in a Node VM, as QX, Loon and Surge would.

The script keeps ddgksf2013's User-Agent list exactly as the apps send it: Chinese app names are
percent-encoded, so a decoded name must not match (same as the original). Each case checks the
result and that $done runs exactly once. Needs node on PATH (GitHub runners have it).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "quantumultx" / "scripts" / "amdc.js"
URL = "http://amdc.m.taobao.com/amdc/mobileDispatch?appkey=1"
BLOCKED = "proxy-config-amdc"

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const out = [];
for (const c of JSON.parse(process.argv[2])) {
  const calls = [];
  const request = {url: c.url};
  if (c.headers !== null) request.headers = c.headers;
  const ctx = {$request: request, $response: {body: '{"dns":[]}'}, $done: v => calls.push(v), console: {log() {}}};
  vm.createContext(ctx);
  vm.runInContext(source, ctx);
  out.push({name: c.name, calls: calls.length, value: calls[0] === undefined ? null : calls[0]});
}
console.log(JSON.stringify(out));
"""

CASES = [
    # name, headers (None = no headers object), blocked?
    ("AMap, User-Agent", {"User-Agent": "AMapiPhone/16.10 (iPhone; iOS 18.0)"}, True),
    ("DMPortal, user-agent", {"user-agent": "DMPortal/9.4 CFNetwork"}, True),
    ("Alibaba (1688)", {"User-Agent": "Alibaba/11.0 CFNetwork"}, True),
    ("Xianyu percent-encoded", {"User-Agent": "%E9%97%B2%E9%B1%BC/7.18 CFNetwork/1.0"}, True),
    ("Tmall percent-encoded", {"User-Agent": "%E5%A4%A9%E7%8C%AB/15.0"}, True),
    ("Xianyu decoded is not in the list", {"User-Agent": "闲鱼/7.18 CFNetwork/1.0"}, False),
    ("Taobao main app", {"User-Agent": "Taobao4iPhone/10.40 CFNetwork"}, False),
    ("other app", {"User-Agent": "SmartDrive/6.0 CFNetwork"}, False),
    ("mixed-case UA header", {"uSeR-aGeNt": "Cainiao/9.0"}, True),
    ("Fliggy", {"User-Agent": "%E9%A3%9E%E7%8C%AA%E6%97%85%E8%A1%8C/1.0"}, True),
    ("Miaojie", {"User-Agent": "%E5%96%B5%E8%A1%97/1.0"}, True),
    ("MovieApp", {"User-Agent": "MovieApp/1.0"}, True),
    ("Hema", {"User-Agent": "Hema4iPhone/1.0"}, True),
    ("Moon", {"User-Agent": "Moon/1.0"}, True),
    ("CloudConsoleApp", {"User-Agent": "CloudConsoleApp/1.0"}, False),
    ("empty UA", {"User-Agent": ""}, False),
    ("no UA header", {"Accept": "*/*"}, False),
    ("no headers", None, False),
]


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("node not found", file=sys.stderr)
        return 1
    payload = [{"name": n, "url": URL, "headers": h} for n, h, _ in CASES]
    res = subprocess.run([node, "-e", HARNESS, str(SCRIPT), json.dumps(payload)], capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stderr, file=sys.stderr)
        return 1
    got = {r["name"]: r for r in json.loads(res.stdout)}
    failures = []
    for name, _, blocked in CASES:
        r = got[name]
        if r["calls"] != 1:
            failures.append(f"{name}: $done called {r['calls']} times")
        want = {"body": BLOCKED} if blocked else {}
        if r["value"] != want:
            failures.append(f"{name}: expected {want}, got {r['value']}")
    containers = [
        ("quantumultx/rewrite/AlibabaAmdc.conf", r"^(\S+) url script-response-body "),
        ("loon/plugins/AlibabaAmdc.plugin", r"^http-response (\S+) script-path="),
        ("surge/modules/rewrite/alibaba-amdc.sgmodule", r'pattern="([^"]+)", script-path='),
    ]
    patterns = []
    for path, expression in containers:
        found = re.findall(expression, (ROOT / path).read_text(), re.M)
        if len(found) != 1:
            failures.append(f"{path}: must contain exactly one AMDC response script")
            continue
        patterns.append(found[0])
        for url, wanted in [(URL, True), ("http://amdc.m.taobao.com:80/amdc/mobileDispatch", True),
                            ("http://a-b.example.com/amdc/mobileDispatch?x=1", True),
                            (URL.replace("http:", "https:"), False),
                            ("http://amdc.m.taobao.com/ordinary", False)]:
            if bool(re.search(found[0], url)) != wanted:
                failures.append(f"{path}: unexpected matching for {url}")
    if len(set(patterns)) != 1:
        failures.append("three clients must share the same AMDC URL pattern")
    if failures:
        print("amdc.js checks failed:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print(f"amdc.js checks passed: {len(CASES)} cases, $done once each.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
