#!/usr/bin/env python3
"""Run quantumultx/scripts/12306-ads.js in a Node VM, as QX and other clients would.

Checks the three ad-slot answers (copied from ddgksf2013's 12306.js output for string placement
numbers), the no-ad answer for unreadable requests and non-string placement numbers, the reply
shape per client, and exactly one $done. Needs node on PATH.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "quantumultx" / "scripts" / "12306-ads.js"

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const out = [];
for (const c of JSON.parse(process.argv[2])) {
  const calls = [];
  const request = {url: 'https://ad.12306.cn/ad/ser/getAdList'};
  if (c.body !== null) request.body = c.body;
  const ctx = {$request: request, $done: v => calls.push(v), console: {log() {}}};
  if (c.client === 'qx') ctx.$task = {};
  else ctx.$httpClient = {};
  vm.runInNewContext(source, ctx);
  out.push(calls);
}
console.log(JSON.stringify(out));
"""

SPLASH = ('{"materialsList":[{"billMaterialsId":"6491","filePath":"ddgksf2013","creativeType":1}],'
          '"advertParam":{"skipTime":1},"code":"00"}')
G0054 = '{"code":"00","materialsList":[{}]}'
NO_AD = '{"code":"00","message":"无广告返回"}'

CASES = [
    ('{"placementNo":"0007"}', SPLASH),
    ('{"placementNo":"G0054","other":1}', G0054),
    ('{"placementNo":"0008"}', NO_AD),
    # Intentional difference: the original compared loosely, so the number 7 got the splash answer.
    ('{"placementNo":7}', NO_AD),
    ('{}', NO_AD),
    ('null', NO_AD),
    ('[]', NO_AD),
    ('{bad', NO_AD),
    ('', NO_AD),
    (None, NO_AD),
]


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("node is required", file=sys.stderr)
        return 1
    cases = [{"client": client, "body": body} for client in ("qx", "other") for body, _ in CASES]
    result = subprocess.run([node, "-e", HARNESS, str(SCRIPT), json.dumps(cases)],
                            capture_output=True, text=True, check=True)
    calls = json.loads(result.stdout)
    failures = []
    for case, got, (_, want) in zip(cases, calls, CASES * 2):
        expected = {"body": want} if case["client"] == "qx" else {"response": {"body": want}}
        if got != [expected]:
            failures.append(f"{case}: want exactly one {expected}, got {got}")
    if failures:
        print("12306-ads.js checks failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print(f"12306-ads.js checks passed: {len(cases)} cases, one $done each.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
