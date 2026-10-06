#!/usr/bin/env python3
"""Static checks for the Surge files: surge/proxy-config.conf, the home modules and rule lists.

Surge mirrors loon/bootstrap.example.conf on purpose, with three deliberate differences that
these checks pin down (see docs/design.md):
- no "· 自动" ssid wrappers: surge/modules/home-direct.sgmodule sends home Wi-Fi DIRECT with one
  SUBNET rule, so build_rules.HOME_AUTO_GROUPS does not apply to Surge;
- no 🛡️ 安全防护 group: the AdRules and Privacy lists are pre-matching REJECT rules in that
  module (pre-matching only takes REJECT itself), ahead of the SUBNET rule;
- region url-test groups are smart groups; the regexes are Loon's [Remote Filter] verbatim.
"""
from __future__ import annotations

import fnmatch
import hashlib
import itertools
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_rules  # noqa: E402
import build_surge_modules  # noqa: E402
import check_reject_conflicts  # noqa: E402

LOON = ROOT / "loon" / "bootstrap.example.conf"
SURGE = ROOT / "surge" / "proxy-config.conf"
BOOTSTRAP = ROOT / "surge" / "bootstrap.example.conf"
HOME = ROOT / "surge" / "modules" / "home-direct.sgmodule"
NOBLOCK = ROOT / "surge" / "modules" / "home-direct-noblock.sgmodule"
AIRPORT_DNS = ROOT / "surge" / "airport-dns.example.sgmodule"
SPLIT = [build_surge_modules.MODULE_DIR / f"{s['file']}.sgmodule" for s in build_surge_modules.SOURCES]
# MitM hosts the per-app modules share. Surge does not document the order between modules, and only
# the first matching script, Map Local or header-mode URL Rewrite runs, so two modules may share a
# host only when they cannot both answer one request in the same section. Reviewed 2026-10-05: on
# these hosts blackmatrix7 Advertising carries only URL Rewrite lines; where its host-specific
# patterns and another module's URL Rewrite match the same URL, both reject (amap.com, uve.weibo.com)
# or redirect to the same target (google.cn, General and SafeRedirect). The others use different
# sections there. Reviewed 2026-10-05 for the app modules added that day (damai, neteasemail,
# umetrip): Advertising still has only URL Rewrite there and they use Script, Map Local and Body Rewrite;
# Advertising's rejects run first in the request stage either way (umetrip startup, mail.163 /mmad/,
# damai popup.get). Reviewed 2026-10-05 for the fmz200 domestic modules (chinamobile,
# douban-app, hupu, leke, maoyan, meituan, mijia, wechat-mp): Advertising has only URL Rewrite there; where
# it and a new URL Rewrite match the same request both reject, and its rejects run before their Map Local
# (Codex's worked examples: hupu search/hotkey, goblin getOther, hoopchina blogfile, meituan linglong and
# wmapi startpicture, maoyan adAdmin jpg, douban common_ads). Review again before changing this. This only catches overlaps by host: Advertising's
# host-agnostic reject patterns (advertising, /ad/, ...) can still meet another module's redirect;
# surge/README.md records that accepted difference.
# fmz200 split modules reviewed on 2026-10-05: pinned to this commit and without scripts. Written out here
# rather than read from build_surge_modules, so changing SOURCES cannot move the expectation with it.
FMZ200_COMMIT = "5d5f63fcf98bc69d5f8f1b1bae6f86a01ee4bb97"
FMZ200_FILES = {"wechat-mp", "meituan", "hupu", "mijia", "maoyan", "leke", "douban-app", "chinamobile", "xiaoyuzhou"}
# Modules whose scripts point at this repo's hosted copy (2026-10-05): exactly these scripts.
REPO_SCRIPTED = {
    "amap": {"https://raw.githubusercontent.com/wenbingkun/proxy-config/a0e3343ea4f86da12b9864caf0df1c1f7479c4a6/quantumultx/scripts/amap.js"},
    "amap-page-cleanup": {"https://raw.githubusercontent.com/wenbingkun/proxy-config/ced3ace1d4dfa6e6b1301dfb465cb6c5c4056bd8/quantumultx/scripts/amap-page-cleanup.js"},
    "umetrip": {"https://raw.githubusercontent.com/wenbingkun/proxy-config/9f2bd622348198b7eaa84ccbfe2c57bc010e7bfd/quantumultx/scripts/umetrip.js"},
    "alibaba-amdc": {"https://raw.githubusercontent.com/wenbingkun/proxy-config/main/quantumultx/scripts/amdc.js"},
    "xiaohongshu": {
        "https://raw.githubusercontent.com/wenbingkun/proxy-config/main/quantumultx/scripts/xiaohongshu.js",
        "https://raw.githubusercontent.com/Script-Hub-Org/Script-Hub/6b4fb62240629d2fc66b08bc271f8c1f83a5dcd1/scripts/replace-body.js",
    },
}
# fmz200 modules that run a script: only these scripts, pinned through SOURCES "pins" (2026-10-05).
FMZ200_SCRIPTED = {
    "wechat": {"https://raw.githubusercontent.com/zZPiglet/Task/0a70fbe27dfb072dac29423d661ed3c47cf66aab/asset/UnblockURLinWeChat.js"},
}
# Startup subset uses only request-stage empty replies; its acs host has no
# Damai endpoint overlap. Umetrip discardrp is also request-stage, before response scripts.
SHARED_MITM = {
    ("damai", "startup-supplement"): {"acs.m.taobao.com"},
    ("advertising", "chinamobile"): {"client.app.coc.10086.cn"},
    ("advertising", "douban-app"): {"api.douban.com"},
    ("advertising", "hupu"): {"games.mobileapi.hupu.com", "goblin.hupu.com", "i*.hoopchina.com.cn"},
    ("advertising", "leke"): {"lens.leoao.com"},
    ("advertising", "maoyan"): {"p0.pipi.cn"},
    ("advertising", "meituan"): {"img.meituan.net", "s3plus.meituan.net", "flowplus.meituan.net"},
    ("advertising", "mijia"): {"home.mi.com"},
    ("advertising", "wechat-mp"): {"mp.weixin.qq.com"},
    ("advertising", "startup-supplement"): {"apiproxy.zuche.com", "acs.m.taobao.com", "api.pinduoduo.com", "api.yangkeduo.com", "app.dewu.com", "res.xiaojukeji.com"},
    ("advertising", "amap"): {"amap-aos-info-nogw.amap.com", "m*.amap.com", "optimus-ads.amap.com"},
    ("advertising", "damai"): {"acs.m.taobao.com"},
    ("advertising", "neteasemail"): {"appconf.mail.163.com", "client.mail.163.com"},
    ("advertising", "safe-redirect"): {"*.google.cn", "app.biliintl.com", "ditu.google.cn", "map.google.cn",
                                       "passport.biliintl.com", "www.firefox.com.cn", "www.google.cn"},
    ("advertising", "spotify"): {"spclient.wg.spotify.com"},
    ("advertising", "umetrip"): {"discardrp.umetrip.com", "*.umetrip.com", "activity.umetrip.com", "appmsg.umetrip.com", "event.umetrip.com",
                                 "flightstatus.umetrip.com", "home.umetrip.com", "opactivity.umetrip.com",
                                 "oss.umetrip.com", "sns.umetrip.com", "startup.umetrip.com",
                                 "umeflightstatus.umetrip.com", "umehome.umetrip.com", "umerp.umetrip.com",
                                 "umestartup.umetrip.com", "umeuser.umetrip.com", "user.umetrip.com"},
    ("advertising", "wechat"): {"security.wechat.com", "weixin110.qq.com"},
    # fmz200 Weibo (2026-10-06) decrypts *.weibo.cn and *.weibo.com: wider than ddgksf2013's list.
    ("advertising", "weibo"): {"*.uve.weibo.com", "*.weibo.cn", "*.weibo.com", "api.weibo.cn", "mapi.weibo.com",
                               "new.vip.weibo.cn", "tqt.weibo.cn", "weibointl.api.weibo.cn"},
    ("advertising", "xiaohongshu"): {"edith.xiaohongshu.com", "www.xiaohongshu.com"},
}
README = ROOT / "surge" / "README.md"
REPO_RULES = "https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/rules/"
MANAGED_URL = "https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/proxy-config.conf"
BM7_LOON = "https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Loon/"
BM7_SURGE = "https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Surge/"
# bm7 splits these Surge lists into X.list (non-domain) and X_Domain.list; X_All.list has both.
BM7_ALL = {"China", "Apple", "Alibaba", "Tencent"}
AD_GROUP = "🛡️ 安全防护"
ADRULES = "https://raw.githubusercontent.com/Cats-Team/AdRules/main/adrules-surge.conf"
PRIVACY = BM7_SURGE + "Privacy/Privacy_All_No_Resolve.list"
HOME_RULE = "SUBNET,SSID:{{{HOME_SSID}}},DIRECT"
# Surge resolves a domain locally at the first IP-based rule without no-resolve (manual: rules
# overview). Only this tail may do that: local_network again without no-resolve (an unlisted domain whose
# DNS answer is a private address stays DIRECT outside home instead of reaching FINAL), the three domestic
# sets as the IP fallback for domains no earlier rule matched, and GEOIP,CN.
# Every rule ahead of it must carry no-resolve (#57, #60), so an upstream list that drops the flag cannot
# bring early resolution back.
DOMESTIC = "🇨🇳 国内服务"
LAN_IP_STAGE = f"RULE-SET,{REPO_RULES}local_network.list,DIRECT"
RESOLVING_TAIL = [LAN_IP_STAGE] + [
    f"RULE-SET,{BM7_SURGE}{n}/{n}_All.list,{DOMESTIC}" for n in ("Alibaba", "Tencent", "China")] + [
    f"GEOIP,CN,{DOMESTIC}", "FINAL,🐟 兜底分流,dns-failed"]
IP_RULES = {"IP-CIDR", "IP-CIDR6", "IP-ASN", "GEOIP"}
KNOWN_RULES = IP_RULES | {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "DOMAIN-WILDCARD", "RULE-SET",
                          "AND", "OR", "NOT", "SUBNET", "DEST-PORT", "USER-AGENT", "PROTOCOL", "FINAL",
                          # Matches the URL of decrypted / plain-HTTP requests; never resolves (fmz200 Weibo, 2026-10-06).
                          "URL-REGEX"}
GROUP_KIND = {"url-test": "smart", "select": "select", "fallback": "fallback"}


def sections(text: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            result.setdefault(current, [])
        elif current is not None:
            result[current].append(line)
    return result


def loon_parts() -> tuple[dict[str, str], list[tuple[str, str, list[str]]], list[str]]:
    sec = sections(LOON.read_text(encoding="utf-8"))
    filters = {}
    for line in sec["Remote Filter"]:
        name, rest = line.split(" = ", 1)
        filters[name] = re.search(r'FilterKey = "(.*)"$', rest).group(1)
    groups = []
    for line in sec["Proxy Group"]:
        name, rest = line.split(" = ", 1)
        if name.endswith(build_rules.HOME_AUTO_SUFFIX):
            continue
        parts = [p.strip() for p in rest.split(",")]
        members = [p for p in parts[1:] if not p.startswith(("img-url=", "interval=", "tolerance="))]
        groups.append((name, parts[0], members))
    return filters, groups, sec["Remote Rule"]


def surge_groups() -> list[tuple[str, str, str]]:
    out = []
    for line in sections(SURGE.read_text(encoding="utf-8"))["Proxy Group"]:
        name, rest = line.split(" = ", 1)
        kind, _, opts = rest.partition(", ")
        out.append((name, kind, opts))
    return out


def check_groups(failures: list[str]) -> None:
    filters, loon, _ = loon_parts()
    expected = [g for g in loon if g[0] != AD_GROUP]
    surge = surge_groups()
    if [g[0] for g in surge] != [g[0] for g in expected]:
        failures.append(f"group names/order must match Loon without {AD_GROUP}: {[g[0] for g in surge]}")
        return
    for (name, kind, opts), (_, loon_kind, members) in zip(surge, expected):
        if len(members) == 1 and members[0] in filters:
            want_kind = GROUP_KIND[loon_kind]
            want_opts = "include-all-proxies=true"
            if members[0] != "ALL_Filter":
                want_opts += f', policy-regex-filter="{filters[members[0]]}"'
        else:
            want_kind, want_opts = loon_kind, ", ".join(members)
        if (kind, opts) != (want_kind, want_opts):
            failures.append(f"{name}: got {kind}, {opts[:80]}; want {want_kind}, {want_opts[:80]}")


def rule_lines() -> list[str]:
    return sections(SURGE.read_text(encoding="utf-8"))["Rule"]


def sub_rules(rule: str) -> list[str]:
    """The sub-rules of AND,((a),(b)),… / NOT,((a)),… (nesting allowed)."""
    body = rule[rule.index(",") + 1:]
    depth, start, subs = 0, 0, []
    for i, ch in enumerate(body):
        if ch == "(":
            depth += 1
            if depth == 2:
                start = i + 1
        elif ch == ")":
            if depth == 2:
                subs.append(body[start:i].strip())
            depth -= 1
            if depth == 0:
                return subs
    raise ValueError(f"unbalanced logical rule: {rule}")


# The only NOT allowed anywhere (used by the home module): the repo's domain-only allow list. A no-resolve
# there would turn a skipped IP entry into "not allowed", so the list stays domain-only (check_allow_list).
ALLOWED_NOT = f"NOT,((RULE-SET,{REPO_RULES}reject_allow.list,extended-matching))"


def early_resolve(rule: str) -> list[str]:
    """Why a rule ahead of RESOLVING_TAIL could resolve a domain locally (empty if it cannot)."""
    kind, *opts = [p.strip() for p in rule.split(",")]
    if kind not in KNOWN_RULES:
        return [f"unknown rule type {kind!r}, review how it resolves: {rule}"]
    if kind == "FINAL":
        return [f"FINAL must be the last rule: {rule}"]
    if kind == "NOT":
        return [] if rule.replace(" ", "") == ALLOWED_NOT else [f"only {ALLOWED_NOT} may use NOT: {rule}"]
    if kind in ("AND", "OR"):
        return [p for sub in sub_rules(rule) for p in early_resolve(sub)]
    if (kind in IP_RULES or kind == "RULE-SET") and "no-resolve" not in opts:
        return [f"needs no-resolve (only the domestic tail may resolve): {rule}"]
    return []


def check_resolve_order(rules: list[str], failures: list[str]) -> None:
    if rules[-len(RESOLVING_TAIL):] != RESOLVING_TAIL:
        failures.append("rules must end with local_network (no no-resolve), the Alibaba/Tencent/China _All sets,"
                        " GEOIP,CN and FINAL dns-failed,"
                        f" in that order; got {rules[-len(RESOLVING_TAIL):]}")
    for line in rules[:-len(RESOLVING_TAIL)]:
        failures.extend(early_resolve(line))


def check_module_resolve(failures: list[str]) -> None:
    """Module rules run ahead of the whole profile (and UDP has no pre-matching stage), so they follow
    the same no-resolve rule as the profile's front part. Device-only modules are not covered."""
    for path in (HOME, NOBLOCK, *SPLIT):
        for line in module_rules(path):
            failures.extend(f"{path.name}: {p}" for p in early_resolve(line))


def check_rules(failures: list[str]) -> None:
    rules = rule_lines()
    names = {g[0] for g in surge_groups()} | {"DIRECT"}
    for line in rules:
        # Split outside parentheses and quotes so a logical rule's sub-rules do not read as its policy.
        try:
            parts = check_reject_conflicts.split_top(line, "surge/proxy-config.conf [Rule]")
        except check_reject_conflicts.InputError as exc:
            failures.append(str(exc))
            continue
        policy = parts[1] if parts[0] == "FINAL" else parts[2]
        if policy not in names:
            failures.append(f"rule policy {policy!r} is not a Surge group: {line}")
        if build_rules.HOME_AUTO_SUFFIX in line or AD_GROUP in line:
            failures.append(f"Surge rules must not use ssid wrappers or {AD_GROUP}: {line}")
    if rules[0] != "DOMAIN-SUFFIX,base.org,💰 加密货币":
        failures.append(f"first rule must be Loon's local base.org rule, got {rules[0]}")
    check_resolve_order(rules, failures)

    rule_sets = [line for line in rules if line.startswith("RULE-SET,") and line != LAN_IP_STAGE]
    manifest = build_rules.load_manifest()
    repo = [f"RULE-SET,{REPO_RULES}{item['id']}.list,{item['qx_policy']},no-resolve" for item in manifest]
    if rule_sets[: len(repo)] != repo:
        failures.append("repo RULE-SET lines must follow rules/local_rules.yaml order and qx_policy, with no-resolve")

    _, _, loon_remote = loon_parts()
    third = []
    for line in loon_remote:
        url, *opts = [p.strip() for p in line.split(",")]
        policy = dict(o.split("=", 1) for o in opts)["policy"].removesuffix(build_rules.HOME_AUTO_SUFFIX)
        if "wenbingkun/proxy-config" in url or policy == AD_GROUP:
            continue
        if url.startswith(BM7_LOON):
            name = url[len(BM7_LOON):].split("/")[0]
            url = f"{BM7_SURGE}{name}/{name}{'_All' if name in BM7_ALL else ''}.list"
        line = f"RULE-SET,{url},{policy}"
        third.append(line if line in RESOLVING_TAIL else line + ",no-resolve")
    if rule_sets[len(repo):] != third:
        failures.append("third-party RULE-SET lines must mirror Loon [Remote Rule]"
                        " (bm7 Surge paths, no-resolve except the domestic tail)")
    if any(ADRULES in line or "/Privacy/" in line for line in rules):
        failures.append("AdRules/Privacy belong in surge/modules/home-direct.sgmodule only")


def module_rules(path: Path) -> list[str]:
    return sections(path.read_text(encoding="utf-8")).get("Rule", [])


def check_modules(failures: list[str]) -> None:
    home, noblock = module_rules(HOME), module_rules(NOBLOCK)
    if noblock != [HOME_RULE]:
        failures.append(f"noblock module must hold only {HOME_RULE}, got {noblock}")
    if not home or home[-1] != HOME_RULE:
        failures.append(f"home module must end with {HOME_RULE}")
        return
    allow = REPO_RULES + "reject_allow.list"
    want = [
        f"AND,((RULE-SET,{src},no-resolve,extended-matching),(NOT,((RULE-SET,{allow},extended-matching)))),REJECT,pre-matching"
        for src in (ADRULES, PRIVACY)
    ]
    if home[:-1] != want:
        failures.append(f"home module REJECT lines must be {want}, got {home[:-1]}")
    for path in (HOME, NOBLOCK):
        text = path.read_text(encoding="utf-8")
        if "#!arguments=HOME_SSID:HOME_SSID" not in text:
            failures.append(f"{path.name}: HOME_SSID argument must default to the placeholder")


def check_allow_list(failures: list[str]) -> None:
    expected: set[str] = set()
    for name in build_rules.SURGE_REJECT_ALLOW_SOURCES:
        data = build_rules.load_rule_source(name)
        expected |= {f"DOMAIN-SUFFIX,{v}" for v in data.get("domain_suffix", [])}
        expected |= {f"DOMAIN,{v}" for v in data.get("domain", [])}
    path = ROOT / "surge" / "rules" / "reject_allow.list"
    got = [l for l in path.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")]
    if set(got) != expected or len(got) != len(set(got)):
        failures.append("surge/rules/reject_allow.list must equal the hk_banks + intl_brokers domain rules")
    manifest_paths = {build_rules.SURGE_RULES_DIR / f"{i['id']}.list" for i in build_rules.load_manifest()}
    built = {p for p in build_rules.build_outputs() if p.parent == build_rules.SURGE_RULES_DIR}
    if built != manifest_paths | {path}:
        failures.append("build_rules must emit one Surge list per manifest item plus reject_allow.list")
    for list_path in sorted(build_rules.SURGE_RULES_DIR.glob("*.list")):
        try:
            check_reject_conflicts.validate_surge_ruleset(list_path.read_text(encoding="utf-8"), list_path.name)
        except check_reject_conflicts.InputError as exc:
            failures.append(str(exc))


def check_private(failures: list[str]) -> None:
    if SURGE.read_text(encoding="utf-8").splitlines()[0] != (
        f"#!MANAGED-CONFIG {MANAGED_URL} interval=86400 strict=false"
    ):
        failures.append("proxy-config.conf must start with its #!MANAGED-CONFIG line")
    for path in (SURGE, BOOTSTRAP, HOME, NOBLOCK, AIRPORT_DNS):
        text = path.read_text(encoding="utf-8")
        ssids = set(re.findall(r"SSID:([^\s,]+)", text)) - {"HOME_SSID", "{{{HOME_SSID}}}"}
        if ssids:
            failures.append(f"{path.name}: real SSIDs must stay on the device: {sorted(ssids)}")
        if re.search(r"(token|subscribe)=", text, re.I):
            failures.append(f"{path.name}: looks like a subscription URL")
    ssid_setting = sections(BOOTSTRAP.read_text(encoding="utf-8"))["SSID Setting"]
    if ssid_setting != ["SSID:HOME_SSID dns-server=system,encrypted-dns-server=off"]:
        failures.append(f"[SSID Setting] parameters take commas without spaces, got {ssid_setting}")
    mitm = sections(BOOTSTRAP.read_text(encoding="utf-8"))["MITM"]
    for key in ("ca-p12", "ca-passphrase"):
        if f"{key} =" not in mitm:
            failures.append(f"bootstrap [MITM] must keep an empty {key} placeholder")
    # Surge refuses a profile line like "hostname =" (device check 2026-10-02); only the two CA
    # placeholders the user must fill may be empty.
    for path in (BOOTSTRAP, SURGE, HOME, NOBLOCK, AIRPORT_DNS):
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not line.startswith("#") and not value.strip() and key.strip() not in ("ca-p12", "ca-passphrase"):
                failures.append(f"{path.name}: empty value is invalid in Surge: {line!r}")
    if "[Proxy]" in sections(SURGE.read_text(encoding="utf-8")):
        failures.append("proxy-config.conf must not define nodes; they come from Airport.conf")


def check_generated_module(path: Path, sources: list[dict], failures: list[str]) -> dict[str, list[str]]:
    """A generated rewrite module: pinned sources, arguments applied, built-in rule policies only."""
    name = path.relative_to(ROOT)
    text = path.read_text(encoding="utf-8")
    if "# Generated by scripts/build_surge_modules.py." not in text:
        failures.append(f"{name} must be generated by scripts/build_surge_modules.py")
    if not re.search(r"^#!category=proxy-config$", text, re.M):
        failures.append(f"{name}: must be in the proxy-config category")
    wheres = re.findall(r"^# Source: .* (\S+)$", text, re.M)
    expected = [s.get("url") or s["path"] for s in sources]
    if wheres != expected:
        failures.append(f"{name} sources must match build_surge_modules.SOURCES, got {wheres}")
    if "{{{" in text:
        failures.append(f"{name}: unreplaced module argument")
    sec = sections(text)
    allowed = set(build_surge_modules.KEYED_SECTIONS + build_surge_modules.LIST_SECTIONS)
    if set(sec) - allowed:
        failures.append(f"{name}: unexpected sections {sorted(set(sec) - allowed)}")
    hostnames = sec.get("MITM", [])
    # Only alibaba-amdc is HTTP-only; keep the MitM requirement for all existing modules.
    if path.stem == "alibaba-amdc":
        if "MITM" in sec:
            failures.append(f"{name}: HTTP-only amdc module must not add MitM")
    elif len(hostnames) != 1 or "%APPEND%" not in hostnames[0]:
        failures.append(f"{name}: [MITM] must be one merged 'hostname = %APPEND% ...' line")
    for line in sec.get("Rule", []):
        # Module rules sit at the top of the rule list; an IP rule there without no-resolve would
        # resolve every request locally before the profile's own rules run.
        if line.startswith(("IP-CIDR,", "IP-CIDR6,", "GEOIP,", "IP-ASN,")) and not line.endswith(",no-resolve"):
            failures.append(f"{name}: IP rule without no-resolve: {line}")
        tokens = check_reject_conflicts.split_top(line, str(name))
        policy = tokens[2] if len(tokens) > 2 else ""
        if policy not in ("DIRECT", "REJECT", "REJECT-NO-DROP", "REJECT-DROP", "REJECT-TINYGIF"):
            failures.append(f"{name}: module rules may only use built-in policies: {line}")
    return sec


def mitm_hosts(sec: dict[str, list[str]]) -> set[str]:
    return {h.strip() for line in sec.get("MITM", []) for h in line.split("%APPEND%", 1)[-1].split(",") if h.strip()}


def check_rewrite_module(failures: list[str]) -> None:
    """The per-app modules stay generated and pinned, with the sponsor-block lookup switched off."""
    sources = list(build_surge_modules.SOURCES)
    for source in sources:
        url = source.get("url")
        if url and not re.search(r"/[0-9a-f]{40}/|/releases/download/v[0-9.]+/", url):
            failures.append(f"{source['name']}: source must be pinned to a commit or release tag: {url}")
        path = source.get("path")
        if path:
            converted = (ROOT / path).read_text(encoding="utf-8")
            if path in {"surge/modules/converted/Umetrip.sgmodule",
                        "surge/modules/converted/AmapPageCleanup.sgmodule",
                        "surge/modules/converted/Amap.sgmodule",
                        "surge/modules/converted/StartupSupplement.sgmodule"}:
                if "# Native Surge source maintained by proxy-config; endpoint and image rules match" not in converted:
                    failures.append(f"{path}: missing the maintained native-source header")
            elif not re.search(r"^# Frozen (Surge conversion of \S+'s Quantumult X rewrite|copy of \S+'s Surge [^;]*); do not edit by hand\.$",
                             converted, re.M):
                failures.append(f"{path}: missing the frozen-conversion header")
            if re.search(r"refs/heads/master|script\.hub", converted):
                failures.append(f"{path}: script paths must be pinned and must not go through Script-Hub")

    on_disk = sorted(build_surge_modules.MODULE_DIR.glob("*.sgmodule"))
    if on_disk != sorted(SPLIT):
        failures.append("surge/modules/rewrite/ must hold exactly one module per source, "
                        f"got {[p.name for p in on_disk]}")
        return
    split = {path.stem: check_generated_module(path, [source], failures) for path, source in zip(SPLIT, sources)}
    for name, expected in REPO_SCRIPTED.items():
        text = (build_surge_modules.MODULE_DIR / f"{name}.sgmodule").read_text(encoding="utf-8")
        scripts = set(re.findall(r"script-path=([^,\s]+)", text))
        if scripts != expected:
            failures.append(f"{name}.sgmodule: scripts must be exactly {sorted(expected)}, got {sorted(scripts)}")
    fmz200 = {s["file"]: s for s in sources if "fmz200/wool_scripts" in (s.get("url") or "")}
    if set(fmz200) != FMZ200_FILES | set(FMZ200_SCRIPTED):
        failures.append(f"fmz200 sources must be exactly {sorted(FMZ200_FILES | set(FMZ200_SCRIPTED))}, "
                        f"got {sorted(fmz200)}")
    for name, source in fmz200.items():
        if f"/fmz200/wool_scripts/{FMZ200_COMMIT}/" not in source["url"]:
            failures.append(f"{name}: fmz200 source must be pinned to {FMZ200_COMMIT}: {source['url']}")
        path = build_surge_modules.MODULE_DIR / f"{name}.sgmodule"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if name in FMZ200_SCRIPTED:
            scripts = set(re.findall(r"script-path=([^,\s]+)", text))
            if scripts != FMZ200_SCRIPTED[name]:
                failures.append(f"{path.name}: scripts must be exactly {sorted(FMZ200_SCRIPTED[name])}, got {sorted(scripts)}")
        elif "Script" in sections(text):
            failures.append(f"{path.name}: the reviewed fmz200 modules must not run scripts")
    for path, source in zip(SPLIT, sources):
        text = path.read_text(encoding="utf-8")
        for old, new in source.get("pins", {}).items():
            if old in text or f"script-path={new}" not in text:
                failures.append(f"{path.name}: script must be pinned to {new}")

    bilibili = build_surge_modules.MODULE_DIR / "bilibili.sgmodule"
    text = "\n".join(line for lines in split.get("bilibili", {}).values() for line in lines)
    if ('"sponsorBlock":"#"' not in text or "grpc.biliapi.net" not in text
            or not re.search(r"^# = type=http-request", bilibili.read_text(encoding="utf-8"), re.M)):
        failures.append("rewrite/bilibili.sgmodule: the sponsor-block lookup (空降助手) must be off")

    shared = {}
    compat = {s["file"] for s in sources if s.get("compat_only")}
    if compat != {"amap-page-cleanup", "douban", "general"}:
        failures.append(f"unexpected compatibility-only sources: {compat}")
    legacy = ROOT / "surge/modules/rewrite/amap-page-cleanup.sgmodule"
    if hashlib.sha256(legacy.read_bytes()).hexdigest() != "c8f7a3dfe0b668d6d9c2661f4384ad9dc3e1e8af5ddeee049b91e4400bcdd523":
        failures.append("compatibility AMap page-cleanup module must retain its published bytes")
    if hashlib.sha256((ROOT / "surge/modules/rewrite/douban.sgmodule").read_bytes()).hexdigest() != "18da8e3eff861971a601dffa78212eb55c1f24ab5fbc5cad2522b605702822ee":
        failures.append("compatibility douban module must retain its published bytes")
    if hashlib.sha256((ROOT / "surge/modules/rewrite/general.sgmodule").read_bytes()).hexdigest() != "8162d8241d150da97ac683bf4b5573ae902c515078d20f140ccda133546f6542":
        failures.append("compatibility general module must retain its published bytes")
    for a, b in itertools.combinations(sorted(set(split) - compat), 2):
        ha, hb = mitm_hosts(split[a]), mitm_hosts(split[b])
        common = {x for x in ha for y in hb if fnmatch.fnmatchcase(x, y) or fnmatch.fnmatchcase(y, x)}
        common |= {y for x in ha for y in hb if fnmatch.fnmatchcase(x, y) or fnmatch.fnmatchcase(y, x)}
        if common:
            shared[(a, b)] = common
    if shared != SHARED_MITM:
        failures.append(f"per-app modules share unreviewed MitM hosts (see SHARED_MITM): {shared}")


def check_categories(failures: list[str]) -> None:
    """The home modules and device-only templates get their own category instead of 未分类. Surge lists
    categories and the modules inside one by name, so this group follows proxy-config and keeps
    these four in the order below."""
    paths = (HOME, NOBLOCK, AIRPORT_DNS, ROOT / "surge" / "netdiag-api.example.sgmodule")
    names = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if not re.search(r"^#!category=proxy-config · 家庭与本地$", text, re.M):
            failures.append(f"{path.name}: must be in the 'proxy-config · 家庭与本地' category")
        names.append(re.search(r"^#!name=(.*)$", text, re.M).group(1))
    if names != sorted(names):
        failures.append(f"home and device-only module names must sort in this order: {names}")


def check_airport_dns(failures: list[str]) -> None:
    """The airport DNS is a local module overriding the global encrypted DNS, never [Host]."""
    sec = sections(AIRPORT_DNS.read_text(encoding="utf-8"))
    if sec != {"General": ["encrypted-dns-server = https://doh.example.com/dns-query"]}:
        failures.append(f"{AIRPORT_DNS.name} must hold only a placeholder encrypted-dns-server, got {sec}")
    for path in (BOOTSTRAP, README, SURGE):
        if "Local.dconf" in path.read_text(encoding="utf-8"):
            failures.append(f"{path.name}: proxy hostnames never match [Host]; do not map node DNS there")


def main() -> int:
    failures: list[str] = []
    for check in (check_groups, check_rules, check_modules, check_module_resolve, check_allow_list, check_private,
                  check_airport_dns, check_rewrite_module, check_categories):
        check(failures)
    if failures:
        print("Surge config checks failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"Surge config checks passed: {len(surge_groups())} groups, {len(rule_lines())} rules.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
