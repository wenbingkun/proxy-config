#!/usr/bin/env python3
"""Static checks for the home Wi-Fi switching in quantumultx/bootstrap.example.conf.

QX mirrors Loon: rules point at "<group> · 自动" ssid policies that are DIRECT on the home Wi-Fi
(the router proxies) and <group> on other Wi-Fi and cellular. The wrapped groups come from
build_rules.HOME_AUTO_GROUPS, which also generates the QX snippet policies.
"""
from __future__ import annotations

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
    global_doh = [l for l in dns if l.startswith("doh-server") and "= /" not in l.replace(" ", "")]
    if len(global_doh) != 1:
        failures.append(f"[dns] must have exactly one global doh-server line, got {len(global_doh)}")
    else:
        parts = [p.strip() for p in global_doh[0].split("=", 1)[1].split(",")]
        if parts[:-1] != DOH_URLS or parts[-1] != f"excluded_ssids={HOME_SSID}":
            failures.append(f"global doh-server must be {DOH_URLS} on one line ending in excluded_ssids={HOME_SSID}")
    for line in dns:
        if "_ssids=" in line and line not in global_doh:
            failures.append(f"[dns] only the global doh-server may skip the home Wi-Fi: {line}")


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
