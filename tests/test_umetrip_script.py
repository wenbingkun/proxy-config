#!/usr/bin/env python3
"""Run the hosted quantumultx/scripts/umetrip.ads.js in a Node VM, as QX, Loon and Surge would.

Upstream joins a Protobuf cleaner and a JSON cleaner that was never called; the hosted copy has one
entry that picks the path. Each case checks the result and that $done runs exactly once:
JSON ads removed (text body and JSON bytes), ordinary JSON fields kept, a Protobuf rewrite by rpid,
and unknown or broken input passed through. Needs node on PATH (GitHub runners have it).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "quantumultx" / "scripts" / "umetrip.ads.js"

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
  if (c.bytes !== undefined) response.bodyBytes = Uint8Array.from(c.bytes).buffer;
  if (c.jsonBytes !== undefined) response.bodyBytes = Uint8Array.from(Buffer.from(c.jsonBytes)).buffer;
  const ctx = {$request: {url: 'https://home.umetrip.com/gateway/api/umetrip/native', headers: c.headers || {}},
    $response: response, $done: v => calls.push(v), console: {log() {}}, Uint8Array, TextDecoder};
  vm.createContext(ctx);
  vm.runInContext(source, ctx);
  const r = {name: c.name, calls: calls.length, kind: 'pass'};
  const v = calls[0];
  if (v && v.bodyBytes !== undefined) {
    const b = new Uint8Array(v.bodyBytes);
    r.kind = 'bytes'; r.bytes = Array.from(b); r.text = Buffer.from(b).toString('utf8');
  } else if (v && v.body !== undefined) {
    r.kind = 'body'; r.text = v.body;
  }
  out.push(r);
}
console.log(JSON.stringify(out));
"""

AD_JSON = json.dumps({"advertDataList": [{"id": "ad"}], "keep": "normal"})
PLAIN_JSON = json.dumps({"keep": "normal", "list": [1, 2]})
CASES = [
    {"name": "json text with ads", "body": AD_JSON},
    {"name": "json bytes with ads", "jsonBytes": AD_JSON},
    {"name": "plain json text", "body": PLAIN_JSON},
    {"name": "protobuf rpid 1000019", "headers": {"rpid": "1000019"}, "bytes": [58, 2, 8, 1]},
    {"name": "protobuf unknown rpid", "headers": {"rpid": "42"}, "bytes": [58, 2, 8, 1]},
    {"name": "broken bytes", "bytes": [255, 255, 1]},
    {"name": "broken json text", "body": "{\"advertDataList\": ["},
    {"name": "no body", },
]


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("node is required for tests/test_umetrip_script.py", file=sys.stderr)
        return 1
    run = subprocess.run([node, "-e", HARNESS, str(SCRIPT), json.dumps(CASES)],
                         capture_output=True, text=True, timeout=60)
    if run.returncode != 0:
        print(run.stderr, file=sys.stderr)
        return 1
    got = {r["name"]: r for r in json.loads(run.stdout)}
    failures = [f"{name}: $done ran {r['calls']} times" for name, r in got.items() if r["calls"] != 1]

    for name in ("json text with ads", "json bytes with ads"):
        r = got[name]
        doc = json.loads(r.get("text") or "null") if r["kind"] != "pass" else None
        if not doc or doc.get("advertDataList") != [] or doc.get("keep") != "normal":
            failures.append(f"{name}: ads must be removed and keep kept, got {r}")
    if got["json text with ads"]["kind"] != "body" or got["json bytes with ads"]["kind"] != "bytes":
        failures.append("a text body must come back as body and JSON bytes as bodyBytes")
    plain = got["plain json text"]
    if plain["kind"] != "pass" and json.loads(plain["text"]) != json.loads(PLAIN_JSON):
        failures.append(f"plain json text: business fields must be unchanged, got {plain}")
    if got["protobuf rpid 1000019"].get("bytes") != [58, 6, 10, 0, 16, 0, 32, 0]:
        failures.append(f"protobuf rpid 1000019: field 7 must be replaced, got {got['protobuf rpid 1000019']}")
    for name in ("protobuf unknown rpid", "broken bytes", "broken json text", "no body"):
        if got[name]["kind"] != "pass":
            failures.append(f"{name}: must pass through unchanged, got {got[name]}")

    if failures:
        print("Umetrip script checks failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"Umetrip script checks passed: {len(CASES)} cases, $done once each.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
