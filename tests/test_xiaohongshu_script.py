#!/usr/bin/env python3
"""Run the hosted quantumultx/scripts/xiaohongshu-channels.js in a Node VM, as QX, Loon and Surge would.

The hosted copy of fmz200's script adds two filters (home feed video notes, the 视频/直播/短剧 home channels)
and a guard so that empty or non-JSON bodies pass through. Each case checks the result and that
$done runs exactly once. Fixtures mirror the field names seen on the device (2026-10-05 sample:
home feed items are model_type "note" with type "video" or "normal"; channels carry an oid).
Needs node on PATH (GitHub runners have it).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "quantumultx" / "scripts" / "xiaohongshu-channels.js"
API = "https://edith.xiaohongshu.com/api/sns/"

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
  out.push({name: c.name, calls: calls.length,
            body: v && v.body !== undefined ? JSON.parse(v.body) : null, pass: !!v && v.body === undefined});
}
console.log(JSON.stringify(out));
"""


def note(i: str, kind: str, **extra) -> dict:
    return {"model_type": "note", "type": kind, "id": i, **extra}


FEED = [
    note("n1", "normal"),
    note("v1", "video"),
    {"model_type": "live_v2", "id": "live"},
    note("ad", "normal", ads_info={}),
    note("goods", "normal", note_attributes=["goods"]),
    note("card", "normal", card_icon={}),
    note("n2", "normal"),
]
CHANNELS = {
    "categories": [{"name": "视频", "oid": "homefeed.video_v3"}, {"name": "直播", "oid": "homefeed.live"},
                   {"name": "短剧", "oid": "homefeed.sketch"}, {"name": "直播技巧", "oid": "homefeed.other_live"}, {"name": "音乐", "oid": "homefeed.music_v3"}],
    "rec_categories": [{"name": "美食", "oid": "homefeed.food_v3"}, {"name": "视频2", "oid": "homefeed.video_v9"}, {"name": "直播", "oid": "homefeed.live"}, {"name": "短剧", "oid": "homefeed.sketch"}],
    "default_show": 1,
}
CASES = [
    {"name": "home feed", "url": API + "v6/homefeed?oid=homefeed_recommend", "body": json.dumps({"data": FEED})},
    {"name": "home feed all video", "url": API + "v6/homefeed?x=1",
     "body": json.dumps({"data": [note("v1", "video"), note("v2", "video")]})},
    {"name": "home feed data not a list", "url": API + "v6/homefeed?x=1", "body": json.dumps({"data": {"k": 1}})},
    {"name": "channels", "url": API + "v6/homefeed/categories?x=1", "body": json.dumps({"data": CHANNELS})},
    {"name": "follow feed keeps videos", "url": API + "v6/followfeed?x=1",
     "body": json.dumps({"data": {"items": [note("v1", "video"), note("n1", "normal")]}})},
    {"name": "search keeps videos", "url": API + "v10/search/notes?keyword=x",
     "body": json.dumps({"data": {"items": [note("v1", "video"), note("n1", "normal"), {"model_type": "ads"}]}})},
    {"name": "empty body", "url": API + "v6/homefeed?x=1", "body": ""},
    {"name": "no body", "url": API + "v6/homefeed?x=1"},
    {"name": "not json", "url": API + "v6/homefeed?x=1", "body": "<html>busy</html>"},
    {"name": "json scalar", "url": API + "v6/homefeed?x=1", "body": "123"},
]


def ids(items: list[dict]) -> list[str]:
    return [i.get("id") for i in items]


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

    expect("home feed", ids(got["home feed"]["body"]["data"]) == ["n1", "n2"],
           f"only image notes stay, got {ids(got['home feed']['body']['data'])}")
    expect("home feed all video", got["home feed all video"]["body"]["data"] == [], "an all-video page becomes empty")
    expect("home feed data not a list", got["home feed data not a list"]["body"] == {"data": {"k": 1}}, "unchanged")
    ch = got["channels"]["body"]["data"]
    expect("channels", [c["oid"] for c in ch["categories"]] == ["homefeed.other_live", "homefeed.music_v3"]
           and [c["oid"] for c in ch["rec_categories"]] == ["homefeed.food_v3"] and ch["default_show"] == 1,
           f"视频/直播/短剧 channels removed, the rest kept, got {ch}")
    expect("follow feed keeps videos", ids(got["follow feed keeps videos"]["body"]["data"]["items"]) == ["v1", "n1"],
           "follow feed is not filtered")
    expect("search keeps videos", ids(got["search keeps videos"]["body"]["data"]["items"]) == ["v1", "n1"],
           "search drops ads only")
    for name in ("empty body", "no body", "not json", "json scalar"):
        expect(name, got[name]["pass"], "must pass the response through unchanged")
    if failures:
        print("xiaohongshu-channels.js checks failed:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print(f"xiaohongshu-channels.js checks passed: {len(CASES)} cases, $done once each.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
