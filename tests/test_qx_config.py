#!/usr/bin/env python3
"""Static checks for the home Wi-Fi switching in quantumultx/bootstrap.example.conf.

QX mirrors Loon: rules point at "<group> · 自动" ssid policies that are DIRECT on the home Wi-Fi
(the router proxies) and <group> on other Wi-Fi and cellular. The wrapped groups come from
build_rules.HOME_AUTO_GROUPS, which also generates the QX snippet policies.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import build_rules  # noqa: E402
import test_loon_config  # noqa: E402

QX_CONFIG = ROOT / "quantumultx" / "bootstrap.example.conf"
QX_SNIPPET = build_rules.QX_FILTER_PATH
HOME_SSID = "HOME_SSID"
SUFFIX = build_rules.HOME_AUTO_SUFFIX
BUILTIN = {"DIRECT", "REJECT"}
AD_GROUP = "🛡️ 安全防护"


def sections(text: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            result.setdefault(current, [])
        elif current is not None:
            result[current].append(line)
    return result


def load_policies(sec: dict[str, list[str]]) -> list[tuple[str, str, list[str]]]:
    """Return (kind, name, members) for each [policy] line, in order."""
    policies = []
    for line in sec["policy"]:
        kind, _, rest = line.partition("=")
        parts = [p.strip() for p in rest.split(", ")]
        members = [p for p in parts[1:] if "=" not in p]
        policies.append((kind.strip(), parts[0], members))
    return policies


def rule_policies(sec: dict[str, list[str]]) -> list[tuple[str, str]]:
    """Return (where, policy) for every QX rule the template or the repo snippet routes."""
    found = []
    for line in sec["filter_local"]:
        found.append((f"[filter_local] {line}", line.rsplit(",", 1)[1].strip()))
    for line in sec["filter_remote"]:
        opts = dict(p.strip().split("=", 1) for p in line.split(",")[1:] if "=" in p)
        if "force-policy" in opts:
            found.append((f"[filter_remote] {opts.get('tag')}", opts["force-policy"]))
    for line in QX_SNIPPET.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            found.append((f"repo.snippet {line}", line.rsplit(",", 1)[1].strip()))
    return found


def check(failures: list[str]) -> dict[str, list[str]]:
    sec = sections(QX_CONFIG.read_text(encoding="utf-8"))
    policies = load_policies(sec)
    groups = {name: members for kind, name, members in policies if kind != "ssid"}
    ssid = {name: members for kind, name, members in policies if kind == "ssid"}

    # 1. The wrappers: generator order, listed last, X on Wi-Fi and cellular, DIRECT at home.
    expected = [name + SUFFIX for name in build_rules.HOME_AUTO_GROUPS]
    if list(ssid) != expected:
        failures.append(f"QX ssid policies must be {expected} (build_rules.HOME_AUTO_GROUPS), got {list(ssid)}")
    kinds = [kind for kind, _, _ in policies]
    if kinds[len(kinds) - len(ssid) :] != ["ssid"] * len(ssid):
        failures.append("QX ssid policies must be listed after every other policy")
    for name, members in ssid.items():
        away = name.removesuffix(SUFFIX)
        if members != [away, away, f"{HOME_SSID}:DIRECT"]:
            failures.append(f"QX {name!r} must be: {away}, {away}, {HOME_SSID}:DIRECT; got {members}")
        if away not in groups:
            failures.append(f"QX {name!r}: away group {away!r} is missing")

    # 2. Ad blocking is not wrapped and still rejects at home.
    if AD_GROUP + SUFFIX in ssid or AD_GROUP in build_rules.HOME_AUTO_GROUPS:
        failures.append(f"{AD_GROUP} must not get a home-Wi-Fi DIRECT wrapper")
    if groups.get(AD_GROUP, [])[:1] != ["REJECT"] or not set(groups.get(AD_GROUP, [])) <= BUILTIN:
        failures.append(f"QX {AD_GROUP} must default to REJECT and hold only REJECT/DIRECT")

    # 3. Every rule ends in DIRECT or a reject at home: builtin, a wrapper, or a group holding only
    # builtins (🛡️ 安全防护). A bare wrapped group such as 🤖 人工智能 would still proxy at home.
    for where, policy in rule_policies(sec):
        if policy in BUILTIN or policy in ssid:
            continue
        if policy not in groups:
            failures.append(f"{where}: policy {policy!r} does not exist")
        elif not set(groups[policy]) <= BUILTIN:
            failures.append(f"{where}: {policy!r} proxies at home; use {build_rules.home_auto(policy)!r}")

    # 4. Same wrappers as Loon.
    loon_ssid = test_loon_config.load_loon()["ssid"]
    qx_as_loon = {
        name: {"default": members[0], "cellular": members[1], HOME_SSID: "DIRECT"}
        for name, members in ssid.items()
    }
    if list(loon_ssid) != list(ssid) or loon_ssid != qx_as_loon:
        failures.append("QX ssid policies differ from the Loon ssid groups (names, order or branches)")

    # 5. Rule mode on every network; rewrites and MitM stay on.
    general = {line.partition("=")[0].strip() for line in sec["general"]}
    for key in ("running_mode_trigger", "ssid_suspended_list"):
        if key in general:
            failures.append(f"QX [general] must not set {key}: home routing uses ssid policies")
    if not any(line.rstrip().endswith("enabled=true") for line in sec["rewrite_remote"]):
        failures.append("QX [rewrite_remote] has no enabled rewrite")
    if "mitm" not in sec:
        failures.append("QX [mitm] section is missing")
    return sec


DOH_URLS = [
    "https://223.5.5.5/dns-query",
    "https://1.12.12.12/dns-query",
    "https://1.1.1.1/dns-query",
    "https://dns.google/dns-query",
]


def check_dns(failures: list[str]) -> None:
    """On the home Wi-Fi only the global DoH line is skipped; plain servers stay for the router."""
    dns = sections(QX_CONFIG.read_text(encoding="utf-8"))["dns"]
    if "no-system" not in dns:
        failures.append("[dns] must keep no-system")
    global_doh = [
        line for line in dns
        if line.partition("=")[0].strip() == "doh-server"
        and not line.partition("=")[2].strip().startswith("/")
    ]
    if len(global_doh) != 1:
        failures.append(f"[dns] must have exactly one global doh-server line, got {len(global_doh)}")
    else:
        parts = [p.strip() for p in global_doh[0].split("=", 1)[1].split(",")]
        if parts[:-1] != DOH_URLS or parts[-1] != f"excluded_ssids={HOME_SSID}":
            failures.append(f"global doh-server must be {DOH_URLS} on one line ending in excluded_ssids={HOME_SSID}")
    for line in dns:
        if "_ssids=" in line and line not in global_doh:
            failures.append(f"[dns] only the global doh-server may skip the home Wi-Fi: {line}")


# fmz200 app snippets (2026-10-05), written out so editing the template cannot move the expectation:
# each is loaded as a rewrite and, where it carries domain rules, again as a filter. Both need the
# resource parser (opt-parser=true): on the device it kept the two kinds apart (Hupu: 8 rewrites, 5 filters).
FMZ200_SNIPPETS = "https://raw.githubusercontent.com/fmz200/wool_scripts/5d5f63fcf98bc69d5f8f1b1bae6f86a01ee4bb97/QuantumultX/rewrite/split/"
FMZ200_REWRITES = {"WeChatOfficialAccount", "Meituan-MeituanWaimai", "Hupu", "Mijia", "MaoYan", "LeKe", "Douban", "ChinaMobile",
                   "AutoNavi"}
FMZ200_FILTERS = {"Hupu", "Mijia", "Douban", "ChinaMobile", "AutoNavi"}


def check_fmz200(failures: list[str]) -> None:
    sec = sections(QX_CONFIG.read_text(encoding="utf-8"))
    for section, expected in (("rewrite_remote", FMZ200_REWRITES), ("filter_remote", FMZ200_FILTERS)):
        lines = [l for l in sec.get(section, []) if "fmz200/wool_scripts" in l]
        names = {l.split(",")[0].rsplit("/", 1)[-1].removesuffix(".snippet") for l in lines}
        if names != expected or len(lines) != len(expected):
            failures.append(f"QX [{section}]: fmz200 snippets must be exactly {sorted(expected)}, got {sorted(names)}")
        for line in lines:
            url, opts = line.split(",", 1)[0], line.replace(" ", "")
            if not url.startswith(FMZ200_SNIPPETS):
                failures.append(f"QX [{section}]: fmz200 snippet must be pinned: {url}")
            if "opt-parser=true" not in opts or "enabled=false" not in opts:
                failures.append(f"QX [{section}]: {url.rsplit('/', 1)[-1]} needs opt-parser=true and enabled=false")
            if section == "filter_remote" and "force-policy=🛡️安全防护" not in opts:
                failures.append(f"QX [filter_remote]: {url.rsplit('/', 1)[-1]} must use force-policy=🛡️ 安全防护")


# Hosted rewrites that replaced ddgksf2013's (2026-10-05): the scripts each may load, pinned to the
# reviewed commits (or this repo's hosted script), whether QX also loads the file as a filter (mixed
# snippet), and the template default: off until checked on the device, on once accepted (2026-10-05).
REPO_REWRITE = "https://raw.githubusercontent.com/wenbingkun/proxy-config/main/quantumultx/rewrite/"
REPO_XHS_SCRIPT = "https://raw.githubusercontent.com/wenbingkun/proxy-config/main/quantumultx/scripts/xiaohongshu.js"
HOSTED_REWRITES = {
    "Umetrip.conf": ({"https://raw.githubusercontent.com/wenbingkun/proxy-config/442b4ef2a10564bbbecbdcdd392abc806e7d222e/quantumultx/scripts/umetrip.js"}, False),
    "fmz200-Xiaohongshu.snippet": ({REPO_XHS_SCRIPT}, True),
    "AlibabaAmdc.conf": ({"https://raw.githubusercontent.com/wenbingkun/proxy-config/main/quantumultx/scripts/amdc.js"}, False),
    "fmz200-XiaoYuZhou.snippet": (set(), False),
    "fmz200-Zhihu.snippet": ({"https://raw.githubusercontent.com/fmz200/wool_scripts/"
                              "5d5f63fcf98bc69d5f8f1b1bae6f86a01ee4bb97/Scripts/zhihu/zhihu.js"}, True),
    "WeChatUnblock.conf": ({"https://raw.githubusercontent.com/zZPiglet/Task/"
                            "0a70fbe27dfb072dac29423d661ed3c47cf66aab/asset/UnblockURLinWeChat.js"}, False),
    "YouTube.conf": ({"https://raw.githubusercontent.com/Maasea/sgmodule/"
                      "65075cdb388fc5e3094afd7e7314c67b243f3525/Script/Youtube/youtube.response.js"}, False),
}
HOSTED_ACCEPTED = {"fmz200-XiaoYuZhou.snippet", "fmz200-Zhihu.snippet", "WeChatUnblock.conf", "YouTube.conf"}
REPLACED_DDGKSF2013 = ("scripts/zhihu.ads.js", "AdBlock/YoutubeAds.conf", "Function/UnblockURLinWeChat.conf",
                       "AdBlock/XiaoYuZhouAds.conf", "rewrite/XiaoHongShuAds.conf",
                       "AdBlock/AmapAds.conf")


def check_hosted_rewrites(failures: list[str]) -> None:
    text = QX_CONFIG.read_text(encoding="utf-8")
    sec = sections(text)
    for old in REPLACED_DDGKSF2013:
        if old in text:
            failures.append(f"QX: {old} was replaced by a pinned source and must not come back")
    for name, (scripts, as_filter) in HOSTED_REWRITES.items():
        body = (ROOT / "quantumultx" / "rewrite" / name).read_text(encoding="utf-8")
        found = set(re.findall(r"\burl script-[a-z-]+ (\S+)", body))
        if found != scripts:
            failures.append(f"quantumultx/rewrite/{name}: scripts must be exactly {sorted(scripts)}, got {sorted(found)}")
        for section, wanted in (("rewrite_remote", True), ("filter_remote", as_filter)):
            lines = [l for l in sec.get(section, []) if l.split(",")[0].strip().split("#", 1)[0] == REPO_REWRITE + name]
            if len(lines) != (1 if wanted else 0):
                failures.append(f"QX [{section}]: {name} must be loaded {'once' if wanted else 'not at all'}")
            for line in lines:
                opts = line.replace(" ", "")
                state = "enabled=true" if name in HOSTED_ACCEPTED else "enabled=false"
                if state not in opts:
                    failures.append(f"QX [{section}]: {name} must have {state} "
                                    "(off until checked on the device, on once accepted)")
                if name.endswith(".snippet") and "opt-parser=true" not in opts:
                    failures.append(f"QX [{section}]: snippet {name} needs opt-parser=true")
                if section == "filter_remote" and "force-policy=🛡️安全防护" not in opts:
                    failures.append(f"QX [filter_remote]: {name} must use force-policy=🛡️ 安全防护")


def check_local_filter_content(failures: list[str]) -> None:
    """A rewrite-only source cannot be imported as a filter after its last policy is removed."""
    prefix = "https://raw.githubusercontent.com/wenbingkun/proxy-config/main/"
    kinds = {"host", "host-suffix", "host-keyword", "host-wildcard", "host-regex",
             "ip-cidr", "ip6-cidr", "ip-asn", "geoip", "user-agent"}
    for line in sections(QX_CONFIG.read_text(encoding="utf-8")).get("filter_remote", []):
        url = line.split(",", 1)[0].strip().split("#", 1)[0]
        if not url.startswith(prefix):
            continue
        path = ROOT / url.removeprefix(prefix)
        if not path.is_file():
            failures.append(f"QX filter source is missing: {path.relative_to(ROOT)}")
            continue
        policies = [l for l in path.read_text(encoding="utf-8").splitlines()
                    if l.split(",", 1)[0].strip().lower() in kinds]
        if not policies:
            failures.append(f"QX [filter_remote]: {path.name} has no filter policies; load it only as a rewrite")
    # XianYu's only policy was amdc, which now belongs to AlibabaAmdc.
    references = sections(QX_CONFIG.read_text(encoding="utf-8"))
    for section, count in (("rewrite_remote", 1), ("filter_remote", 0)):
        got = sum("quantumultx/rewrite/fmz200-XianYu.snippet," in l for l in references.get(section, []))
        if got != count:
            failures.append(f"QX [{section}]: XianYu must be referenced {count} time(s), got {got}")


def check_xiaoyuzhou_features(failures: list[str]) -> None:
    # Exercise the actual native URL patterns on all three clients. A source can stay pinned
    # while still blocking normal features, which the pin-only checks cannot catch.
    paths = (
        ROOT / "quantumultx/rewrite/fmz200-XiaoYuZhou.snippet",
        ROOT / "loon/plugins/fmz200-XiaoYuZhou.plugin",
        ROOT / "surge/modules/rewrite/xiaoyuzhou.sgmodule",
    )
    normal = (
        "ai", "ai/summary", "search/get", "search/get-results", "search/query",
        "search/get-preset-result", "category/list", "category/list-podcasts",
        "category/list-daily-suggestion-extra", "related-episode/list", "operation-resource/list",
        "flashcards/list",
    )
    cleanup = ("flash-screen/list", "search/get-express", "search/get-preset", "category/list-daily-suggestion",
               "discovery-feed/list")
    for path in paths:
        text = path.read_text(encoding="utf-8")
        rules = [line for line in text.splitlines() if line.startswith(("^https", "http-response-jq "))]
        patterns = [re.compile(next(part for part in line.split() if part.startswith("^https"))) for line in rules]
        if len(rules) != 4:
            failures.append(f"{path.relative_to(ROOT)}: expected four scoped XiaoYuZhou rewrites")
        for version in (1, 2):
            for query in ("", "?q=example"):
                for endpoint in normal:
                    url = f"https://api.xiaoyuzhoufm.com/v{version}/{endpoint}{query}"
                    if any(p.search(url) for p in patterns):
                        failures.append(f"{path.relative_to(ROOT)}: normal feature must pass through: {url}")
                for endpoint in cleanup:
                    url = f"https://api.xiaoyuzhoufm.com/v{version}/{endpoint}{query}"
                    if sum(bool(p.search(url)) for p in patterns) != 1:
                        failures.append(f"{path.relative_to(ROOT)}: cleanup endpoint must match exactly once: {url}")
        discovery = [line for line in rules if "discovery-feed" in line]
        expected_jq = "'" + '.data |= map(select(.type != "DISCOVERY_BANNER"))' + "'"
        if len(discovery) != 1 or not discovery[0].endswith(expected_jq):
            failures.append(f"{path.relative_to(ROOT)}: discovery must keep all items except DISCOVERY_BANNER")


PINNED_URL = re.compile(r"^https://raw\.githubusercontent\.com/[^/]+/[^/]+/[0-9a-f]{40}/")
OWN_SCRIPTS = "https://raw.githubusercontent.com/wenbingkun/proxy-config/main/quantumultx/scripts/"


def check_no_ddgksf2013(failures: list[str]) -> None:
    """2026-10-06: every ddgksf2013 resource is replaced; hosted rewrites only load pinned or own scripts."""
    for line in QX_CONFIG.read_text(encoding="utf-8").splitlines():
        if not line.lstrip().startswith(("#", ";")) and "ddgksf2013" in line.split(",")[0]:
            failures.append(f"QX: ddgksf2013 resources were replaced and must not come back: {line.split(',')[0]}")
    for path in sorted((ROOT / "quantumultx" / "rewrite").glob("*")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith(("#", ";")):
                continue
            for url in re.findall(r"\b(?:script-[a-z-]+|echo-response \S+ echo-response) (https?://\S+)", line):
                if not PINNED_URL.match(url) and not url.startswith(OWN_SCRIPTS):
                    failures.append(f"quantumultx/rewrite/{path.name}: loads an unpinned resource {url}")


def check_generator(failures: list[str]) -> None:
    for policy in ("DIRECT", "REJECT", AD_GROUP, "🌏 全球加速"):
        if build_rules.home_auto(policy) != policy:
            failures.append(f"home_auto({policy!r}) must not wrap it")
    for policy in build_rules.HOME_AUTO_GROUPS:
        if build_rules.home_auto(policy) != policy + SUFFIX:
            failures.append(f"home_auto({policy!r}) must return its wrapper")
    if QX_SNIPPET.read_text(encoding="utf-8") != build_rules.render_qx_filter():
        failures.append("quantumultx/filter/repo.snippet is out of date; run scripts/build_rules.py")


def main() -> int:
    failures: list[str] = []
    check(failures)
    check_dns(failures)
    check_fmz200(failures)
    check_hosted_rewrites(failures)
    check_no_ddgksf2013(failures)
    check_local_filter_content(failures)
    check_xiaoyuzhou_features(failures)
    check_generator(failures)
    if failures:
        print("QX config checks failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print(f"QX config checks passed: {len(build_rules.HOME_AUTO_GROUPS)} ssid policies match Loon.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
