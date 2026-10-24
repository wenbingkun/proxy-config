#!/usr/bin/env python3
"""Run the hosted quantumultx/scripts/wechat-mp.js in a Node VM, as Loon would.

The script stands in for fmz200's `getappmsgad response-body-replace-regex advertisement fmz200`, so every
"advertisement" in the body must be renamed (not only the first) and nothing else may change. Empty or missing
bodies pass through. Each case also checks that $done runs exactly once. Fixtures are synthetic.
Needs node on PATH (GitHub runners have it).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "quantumultx" / "scripts" / "wechat-mp.js"
URL = "https://mp.weixin.qq.com/mp/getappmsgad?f=json"

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const cases = JSON.parse(process.argv[2]);
const out = [];
for (const c of cases) {
  const calls = [];
  const response = {};
  if (c.body !== undefined) response.body = c.body;
  const ctx = {$request: {url: c.url, headers: {}}, $response: response, $done: v => calls.push(v),
    console: {log() {}}};
  vm.createContext(ctx);
  vm.runInContext(source, ctx);
  const v = calls[0];
  out.push({name: c.name, calls: calls.length, body: v && v.body !== undefined ? v.body : null,
            pass: !!v && v.body === undefined});
}
console.log(JSON.stringify(out));
"""

AD = {
    "advertisement_num": 2,
    "advertisement_info": [{"aid": 1, "advertisement_type": "card"}, {"aid": 2}],
    "base_resp": {"ret": 0},
    "title": "x",
}
CASES = [
    {"name": "ads", "url": URL, "body": json.dumps(AD)},
    {"name": "no ads", "url": URL, "body": json.dumps({"base_resp": {"ret": 0}, "title": "x"})},
    {"name": "empty body", "url": URL, "body": ""},
    {"name": "no body", "url": URL},
]


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("node not found", file=sys.stderr)
        return 1
    res = subprocess.run([node, "-e", HARNESS, str(SCRIPT), json.dumps(CASES)], capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stderr, file=sys.stderr)
        return 1
    got = {r["name"]: r for r in json.loads(res.stdout)}
    failures = [f"{n}: $done called {r['calls']} times" for n, r in got.items() if r["calls"] != 1]

    def expect(name: str, ok: bool, detail: str) -> None:
        if not ok:
            failures.append(f"{name}: {detail}")

    want = json.loads(json.dumps(AD).replace("advertisement", "fmz200"))
    body = got["ads"]["body"]
    expect("ads", body is not None and "advertisement" not in body and json.loads(body) == want,
           f"every advertisement renamed, other fields unchanged, got {body}")
    expect("no ads", got["no ads"]["body"] == CASES[1]["body"], "body without ads is returned unchanged")
    for name in ("empty body", "no body"):
        expect(name, got[name]["pass"], "must pass the response through unchanged")
    if failures:
        print("wechat-mp.js checks failed:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print(f"wechat-mp.js checks passed: {len(CASES)} cases, $done once each.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
