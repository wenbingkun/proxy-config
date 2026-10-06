#!/usr/bin/env python3
"""Run the readable quantumultx/scripts/umetrip.js in a Node VM, as QX, Loon and Surge would.

Checks JSON and Protobuf promotion removal, untouched business fields, malformed-input pass-through,
legacy fixture equivalence, and exactly one completion. Needs node on PATH.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "quantumultx" / "scripts" / "umetrip.js"
LEGACY = ROOT / "quantumultx" / "scripts" / "umetrip.ads.js"

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
  if (c.client && c.client !== 'qx' && response.bodyBytes !== undefined) {
    const bytes = new Uint8Array(response.bodyBytes);
    const padded = new Uint8Array(bytes.length + 4); padded.set(bytes, 2);
    response.body = padded.subarray(2, 2 + bytes.length); delete response.bodyBytes;
  }
  const ctx = {$request: {url: 'https://home.umetrip.com/gateway/api/umetrip/native', headers: c.headers || {}},
    $response: response, $done: v => calls.push(v), console: {log() {}}, Uint8Array, ArrayBuffer};
  if (c.client === 'qx') ctx.$task = {};
  if (c.client === 'surge') ctx.$httpClient = {};
  if (c.client === 'loon') {ctx.$httpClient = {}; ctx.$loon = {};}
  if (!c.noDecoder) ctx.TextDecoder = TextDecoder;
  vm.createContext(ctx);
  vm.runInContext(source, ctx);
  const r = {name: c.name, calls: calls.length, kind: 'pass'};
  const v = calls[0];
  if (v && v.bodyBytes !== undefined) {
    if (!(v.bodyBytes instanceof ArrayBuffer)) throw Error('QX requires ArrayBuffer output');
    const b = new Uint8Array(v.bodyBytes);
    r.kind = 'bytes'; r.bytes = Array.from(b); r.text = Buffer.from(b).toString('utf8');
  } else if (v && v.body !== undefined) {
    if (c.client && c.client !== 'qx' && typeof v.body !== 'string') {
      if (!(v.body instanceof Uint8Array)) throw Error('wrong binary output type');
      r.kind = 'bytes'; r.bytes = Array.from(v.body); r.text = Buffer.from(v.body).toString('utf8');
    } else {r.kind = 'body'; r.text = v.body;}
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
    *[{"name": "json bytes prefix " + name, "jsonBytes": prefix + AD_JSON}
      for name, prefix in (("space", " "), ("CR", "\r"), ("LF", "\n"), ("TAB", "\t"), ("mixed", " \r\n\t"))],
    {"name": "plain json text", "body": PLAIN_JSON},
    {"name": "protobuf rpid 1000019", "headers": {"rpid": "1000019"}, "bytes": [58, 2, 8, 1]},
    {"name": "protobuf unknown rpid", "headers": {"rpid": "42"}, "bytes": [58, 2, 8, 1]},
    {"name": "broken bytes", "bytes": [255, 255, 1]},
    {"name": "broken json text", "body": "{\"advertDataList\": ["},
    {"name": "no body", },
]


def varint(value: int) -> list[int]:
    result = []
    while value >= 128:
        result.append(value % 128 | 128)
        value //= 128
    return result + [value]


def field(number: int, payload: str | list[int]) -> list[int]:
    body = list(payload.encode("utf-8")) if isinstance(payload, str) else payload
    return varint(number * 8 + 2) + varint(len(body)) + body


def check_extended(node: str, failures: list[str]) -> None:
    cases, expected = [], {}

    def add(name, payload, result, headers=None, no_decoder=False):
        cases.append({"name": name, "bytes": payload, "headers": headers or {}, "noDecoder": no_decoder})
        expected[name] = result

    business = field(20, "航班查询保留") + [24, 7]
    for rpid in ("1000019", "1420002", "1120000"):
        packet = field(7, [8, 1]) + business
        add("empty " + rpid, packet, field(7, [10, 0, 16, 0, 32, 0]) + business, {"RPID": rpid})
    home_ad = field(5, field(37, "ADVERT"))
    add("home domain promotion", field(7, home_ad + business), field(7, business), {"rpid": "1000002"})
    duplicate = field(5, field(37, "NORMAL") + field(37, "ADVERT"))
    add("duplicate promotion field", field(7, duplicate + business), field(7, business), {"rpid": "1000002"})
    card = field(5, field(8, field(3, "机上闭门购虚拟卡片")))
    add("home named card", field(7, card + business), field(7, business), {"rpid": "1000002"}, True)
    for rpid, number, text in (
        ("1000029", 8, "瀑布流_酒店"), ("1370126", 8, "更早历史行程待解锁"),
        ("1011058", 11, "历史行程容量剩余 付费会员"), ("1060060", 12, "付费会员"),
    ):
        add("protobuf " + rpid, field(7, field(number, text) + business), field(7, business), {"rpid": rpid})
    family = field(1, "可免费试用30天 payMember") + field(2, "添加家人并开启守护 payMember")
    add("family promotion pair", field(7, family + business), field(7, business), {"rpid": "1370279"})
    add("family ordinary single", field(7, field(2, "添加家人并开启守护 payMember") + business), None, {"rpid": "1370279"})
    mine = field(5, field(8, field(7, '{"groupId":111402}')))
    add("mine promotion", field(7, mine + business), field(7, business), {"rpid": "1100001"})
    embedded = json.dumps({"cardList": [{"serviceTrack": "advert_test"}, {"serviceName": "值机"}], "keep": 1}, ensure_ascii=False, separators=(",", ":"))
    cleaned = json.dumps({"cardList": [{"serviceName": "值机"}], "keep": 1}, ensure_ascii=False, separators=(",", ":"))
    add("embedded JSON", field(7, field(9, embedded) + business), field(7, field(9, cleaned) + business), {"rpid": "1000002"})
    add("rpid from envelope", field(5, "1000019") + field(7, [8, 1]), field(5, "1000019") + field(7, [10, 0, 16, 0, 32, 0]))
    add("empty header envelope fallback", field(5, "1000019") + field(7, [8, 1]), field(5, "1000019") + field(7, [10, 0, 16, 0, 32, 0]), {"rpid": ""})
    uint64 = varint(3 * 8) + varint(2**64 - 1)
    add("uint64 business bytes", field(7, [8, 1]) + uint64, field(7, [10, 0, 16, 0, 32, 0]) + uint64, {"rpid": "1000019"})
    for name, packet in (("truncated fixed64", [9, 1]), ("truncated length", [58, 5, 1]),
                         ("invalid field zero", [0, 1]), ("overflow uint64", [24] + [255] * 10 + [1])):
        add(name, packet, None, {"rpid": "1000019"})
    for rpid in ("1000002", "1000029", "1370126", "1370279", "1011058", "1100001", "1060060", "unknown"):
        add("normal business " + rpid, field(7, business), None, {"rpid": rpid})
    document = {
        "responseBody": {"templateService": {"groupList": [
            {"groupStyle": 1009, "cardList": [{"cardResult": "ad"}]},
            {"groupStyle": 1, "cardList": [{"cardResult": {"serviceTrack": "advert_banner"}}, {"cardResult": {"serviceName": "航班查询"}}]},
        ]}, "advertPageInfo": [1], "advertTotalTimeout": 5, "pageIdBlackList": [1], "warmStartInterval": 10,
        "advertDataList": [1], "historyTripCapacity": {"showTripCapacityBar": 1, "capacity": 10},
        "cardCollections": [1], "isLastPage": False, "lastParam": "ad", "vipShowInfoList": [1],
        "isShowPop": 1, "page": {"groupList": [1]}, "keep": {"flight": "正常"}},
        "keepEnvelope": [1, 2],
    }
    for decoder in (False, True):
        name = "JSON complete" + (" no decoder" if decoder else "")
        cases.append({"name": name, "jsonBytes": json.dumps(document, ensure_ascii=False), "noDecoder": decoder})
        expected[name] = "json"
    cases = [{**c, "client": client, "name": client + ": " + c["name"]}
             for client in ("qx", "surge", "loon") for c in cases]
    expected = {client + ": " + name: value for client in ("qx", "surge", "loon")
                for name, value in expected.items()}
    run = subprocess.run([node, "-e", HARNESS, str(SCRIPT), json.dumps(cases)], capture_output=True, text=True, timeout=60)
    if run.returncode:
        failures.append(run.stderr)
        return
    for result in json.loads(run.stdout):
        name = result["name"]
        if result["calls"] != 1:
            failures.append(f"{name}: completion count {result['calls']}")
        wanted = expected[name]
        if wanted is not None and result["kind"] != "bytes":
            failures.append(f"{name}: binary response must return the client binary field/type")
        if wanted is None:
            if result["kind"] != "pass": failures.append(f"{name}: business/malformed input must pass unchanged")
        elif wanted == "json":
            doc = json.loads(result["text"])
            body = doc["responseBody"]
            if body["keep"] != {"flight": "正常"} or doc["keepEnvelope"] != [1, 2]: failures.append(f"{name}: business fields lost")
            groups = body["templateService"]["groupList"]
            if len(groups) != 1 or groups[0]["cardList"] != [{"cardResult": {"serviceName": "航班查询"}}]: failures.append(f"{name}: cards/groups cleanup incorrect")
            if body["advertPageInfo"] or body["advertDataList"] or body["vipShowInfoList"] or body["page"]["groupList"]: failures.append(f"{name}: ad arrays remain")
            if body["advertTotalTimeout"] != 0 or body["historyTripCapacity"] != {"showTripCapacityBar": 0, "capacity": 10}: failures.append(f"{name}: capacity/timer cleanup incorrect")
            if body["cardCollections"] or not body["isLastPage"] or body["lastParam"] or body["isShowPop"]: failures.append(f"{name}: waterfall/popup cleanup incorrect")
        elif result.get("bytes") != wanted:
            failures.append(f"{name}: unexpected protobuf bytes {result}")
    legacy = subprocess.run([node, "-e", HARNESS, str(LEGACY), json.dumps(CASES)], capture_output=True, text=True, timeout=60)
    current = subprocess.run([node, "-e", HARNESS, str(SCRIPT), json.dumps(CASES)], capture_output=True, text=True, timeout=60)
    if legacy.returncode or current.returncode or json.loads(legacy.stdout) != json.loads(current.stdout):
        failures.append("the original 13 cases must remain equivalent to the frozen script")
    print(f"Umetrip extended checks: {len(cases)} protocol/business cases.")


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

    for name in (c["name"] for c in CASES if "with ads" in c["name"] or c["name"].startswith("json bytes prefix ")):
        r = got[name]
        if name.startswith("json bytes") and r["kind"] != "bytes":
            failures.append(f"{name}: must return bodyBytes")
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

    pattern = r"^https:\/\/(?:sns|appmsg|home|umehome|opactivity|activity|umerp|flightstatus|umeflightstatus|startup|umestartup|user|umeuser|event)\.umetrip\.com\/gateway\/api\/umetrip\/native(?:\?.*)?$"
    script_url = "https://raw.githubusercontent.com/wenbingkun/proxy-config/442b4ef2a10564bbbecbdcdd392abc806e7d222e/quantumultx/scripts/umetrip.js"
    for path, mode in (
        ("quantumultx/rewrite/Umetrip.conf", None),
        ("loon/plugins/Umetrip.plugin", "binary-body-mode=true"),
        ("surge/modules/converted/Umetrip.sgmodule", "binary-body-mode=1"),
    ):
        text = (ROOT / path).read_text(encoding="utf-8")
        if text.count(pattern) != 1 or text.count(script_url) != 1:
            failures.append(f"{path}: must use the reviewed native endpoint once with the shared script")
        if mode and mode not in text:
            failures.append(f"{path}: binary response mode is required")

    check_extended(node, failures)
    if failures:
        print("Umetrip script checks failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"Umetrip script checks passed: {len(CASES)} cases, $done once each.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
