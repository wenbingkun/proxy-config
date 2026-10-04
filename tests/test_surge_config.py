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
REWRITE = build_surge_modules.OUTPUT
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
# overview). Only this tail may do that: GEOIP,CN needs an address, and the three domestic sets keep
# their IP fallback for domains no earlier rule matched. Every rule ahead of it must carry no-resolve
# (#57, #60), so an upstream list that drops the flag cannot bring early resolution back.
DOMESTIC = "🇨🇳 国内服务"
RESOLVING_TAIL = [f"RULE-SET,{BM7_SURGE}{n}/{n}_All.list,{DOMESTIC}" for n in ("Alibaba", "Tencent", "China")] + [
    f"GEOIP,CN,{DOMESTIC}", "FINAL,🐟 兜底分流,dns-failed"]
IP_RULES = {"IP-CIDR", "IP-CIDR6", "IP-ASN", "GEOIP"}
KNOWN_RULES = IP_RULES | {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "DOMAIN-WILDCARD", "RULE-SET",
                          "AND", "OR", "NOT", "SUBNET", "DEST-PORT", "USER-AGENT", "PROTOCOL", "FINAL"}
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


def early_resolve(rule: str) -> list[str]:
    """Why a rule ahead of RESOLVING_TAIL could resolve a domain locally (empty if it cannot)."""
    kind, *opts = [p.strip() for p in rule.split(",")]
    if kind not in KNOWN_RULES:
        return [f"unknown rule type {kind!r}, review how it resolves: {rule}"]
    if kind == "FINAL":
        return [f"FINAL must be the last rule: {rule}"]
    if kind == "NOT":
        return [f"NOT is not expected ahead of the domestic tail: {rule}"]
    if kind in ("AND", "OR"):
        return [p for sub in sub_rules(rule) for p in early_resolve(sub)]
    if (kind in IP_RULES or kind == "RULE-SET") and "no-resolve" not in opts:
        return [f"needs no-resolve (only the domestic tail may resolve): {rule}"]
    return []


def check_resolve_order(rules: list[str], failures: list[str]) -> None:
    if rules[-len(RESOLVING_TAIL):] != RESOLVING_TAIL:
        failures.append("rules must end with the Alibaba/Tencent/China _All sets, GEOIP,CN and FINAL dns-failed,"
                        f" in that order; got {rules[-len(RESOLVING_TAIL):]}")
    for line in rules[:-len(RESOLVING_TAIL)]:
        failures.extend(early_resolve(line))


def check_rules(failures: list[str]) -> None:
    rules = rule_lines()
    names = {g[0] for g in surge_groups()} | {"DIRECT"}
    for line in rules:
        parts = line.split(",")
        policy = parts[1] if parts[0] == "FINAL" else parts[2]
        if policy not in names:
            failures.append(f"rule policy {policy!r} is not a Surge group: {line}")
        if build_rules.HOME_AUTO_SUFFIX in line or AD_GROUP in line:
            failures.append(f"Surge rules must not use ssid wrappers or {AD_GROUP}: {line}")
    if rules[0] != "DOMAIN-SUFFIX,base.org,💰 加密货币":
        failures.append(f"first rule must be Loon's local base.org rule, got {rules[0]}")
    check_resolve_order(rules, failures)

    rule_sets = [line for line in rules if line.startswith("RULE-SET,")]
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
        f"AND,((RULE-SET,{src},extended-matching),(NOT,((RULE-SET,{allow},extended-matching)))),REJECT,pre-matching"
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


def check_rewrite_module(failures: list[str]) -> None:
    """The merged module stays generated, pinned and with the sponsor-block lookup switched off."""
    text = REWRITE.read_text(encoding="utf-8")
    if "# Generated by scripts/build_surge_modules.py." not in text:
        failures.append(f"{REWRITE.name} must be generated by scripts/build_surge_modules.py")
    wheres = re.findall(r"^# Source: .* (\S+)$", text, re.M)
    expected = [s.get("url") or s["path"] for s in build_surge_modules.SOURCES]
    if wheres != expected:
        failures.append(f"{REWRITE.name} sources must match build_surge_modules.SOURCES, got {wheres}")
    for source in build_surge_modules.SOURCES:
        url = source.get("url")
        if url and not re.search(r"/[0-9a-f]{40}/|/releases/download/v[0-9.]+/", url):
            failures.append(f"{REWRITE.name}: source must be pinned to a commit or release tag: {url}")
        path = source.get("path")
        if path:
            converted = (ROOT / path).read_text(encoding="utf-8")
            if not re.search(r"^# Frozen Surge conversion of \S+'s Quantumult X rewrite; do not edit by hand\.$",
                             converted, re.M):
                failures.append(f"{path}: missing the frozen-conversion header")
            if re.search(r"refs/heads/master|script\.hub", converted):
                failures.append(f"{path}: script paths must be pinned and must not go through Script-Hub")
    if "{{{" in text:
        failures.append(f"{REWRITE.name}: unreplaced module argument")
    if '"sponsorBlock":"#"' not in text or not re.search(r"^# = type=http-request", text, re.M):
        failures.append(f"{REWRITE.name}: the sponsor-block lookup (空降助手) must be off")
    sec = sections(text)
    allowed = set(build_surge_modules.KEYED_SECTIONS + build_surge_modules.LIST_SECTIONS)
    if set(sec) - allowed:
        failures.append(f"{REWRITE.name}: unexpected sections {sorted(set(sec) - allowed)}")
    hostnames = sec.get("MITM", [])
    if len(hostnames) != 1 or "grpc.biliapi.net" not in hostnames[0] or "%APPEND%" not in hostnames[0]:
        failures.append(f"{REWRITE.name}: [MITM] must be one merged 'hostname = %APPEND% ...' line")
    for line in sec.get("Rule", []):
        # Module rules sit at the top of the rule list; an IP rule there without no-resolve would
        # resolve every request locally before the profile's own rules run.
        if line.startswith(("IP-CIDR,", "IP-CIDR6,", "GEOIP,", "IP-ASN,")) and not line.endswith(",no-resolve"):
            failures.append(f"{REWRITE.name}: IP rule without no-resolve: {line}")
        tokens = check_reject_conflicts.split_top(line, REWRITE.name)
        policy = tokens[2] if len(tokens) > 2 else ""
        if policy not in ("DIRECT", "REJECT", "REJECT-NO-DROP", "REJECT-DROP", "REJECT-TINYGIF"):
            failures.append(f"{REWRITE.name}: module rules may only use built-in policies: {line}")


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
    for check in (check_groups, check_rules, check_modules, check_allow_list, check_private, check_airport_dns,
                  check_rewrite_module):
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
