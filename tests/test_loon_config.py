#!/usr/bin/env python3
"""Static checks for loon/bootstrap.example.conf and the Loon rule lists."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_rules  # noqa: E402


LOON_CONFIG = ROOT / "loon" / "bootstrap.example.conf"
QX_CONFIG = ROOT / "quantumultx" / "bootstrap.example.conf"
MANIFEST = ROOT / "rules" / "local_rules.yaml"

HOME_SSID = "HOME_SSID"
# Rule-facing ssid groups are "<QX group> · 自动"; the QX-named group is where nodes are picked.
AUTO_SUFFIX = build_rules.HOME_AUTO_SUFFIX
BUILTIN = {"DIRECT", "REJECT", "REJECT-IMG", "REJECT-DICT", "REJECT-ARRAY", "REJECT-DROP"}
REPO_RAW = "https://raw.githubusercontent.com/wenbingkun/proxy-config/main/"
QX_KINDS = {"static": "select", "url-latency-benchmark": "url-test", "available": "fallback"}
# Plugins whose own rules use PROXY (kokoryh Sparkle: bsbsb.top for the sponsor-block lookup;
# BoxJS: every jsdelivr.net request).
PLUGINS_WITH_PROXY_RULES = ("kokoryh/Sparkle/", "chavyleung/scripts/master/box/")


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


def split_opts(rest: str) -> tuple[list[str], dict[str, str]]:
    members: list[str] = []
    opts: dict[str, str] = {}
    for part in (p.strip() for p in rest.split(",")):
        key, sep, value = part.partition("=")
        if sep and key.strip() in {"interval", "tolerance", "img-url", "url", "max-timeout"}:
            opts[key.strip()] = value.strip()
        else:
            members.append(part)
    return members, opts


def load_loon() -> dict:
    sec = sections(LOON_CONFIG.read_text(encoding="utf-8"))
    filters = {}
    for line in sec["Remote Filter"]:
        m = re.fullmatch(r'(\S+) = NameRegex, FilterKey = "(.*)"', line)
        assert m, f"unexpected Remote Filter line: {line}"
        filters[m.group(1)] = m.group(2)

    groups: dict[str, tuple[str, list[str], dict[str, str]]] = {}
    ssid: dict[str, dict[str, str]] = {}
    for line in sec["Proxy Group"]:
        name, _, rest = line.partition(" = ")
        kind, _, rest = rest.partition(", ")
        assert name not in groups and name not in ssid, f"duplicate group {name!r}"
        if kind == "ssid":
            branches = {}
            for part in rest.split(", "):
                key, _, value = part.partition(" = ")
                branches[key.strip('"')] = value
            ssid[name] = branches
        else:
            members, opts = split_opts(rest)
            groups[name] = (kind, members, opts)

    remote_rules = []
    for line in sec["Remote Rule"]:
        url, _, rest = line.partition(", ")
        opts = dict(p.split("=", 1) for p in rest.split(", "))
        remote_rules.append((url, opts))

    local_rules = [line.split(",") for line in sec["Rule"]]
    return {
        "sections": sec,
        "filters": filters,
        "groups": groups,
        "ssid": ssid,
        "remote_rules": remote_rules,
        "local_rules": local_rules,
    }


def load_qx_groups() -> dict[str, tuple[str, list[str], str | None, dict[str, str]]]:
    groups = {}
    for line in sections(QX_CONFIG.read_text(encoding="utf-8"))["policy"]:
        kind, _, rest = line.partition("=")
        if kind == "ssid":
            # The QX ssid wrappers are checked against Loon's in tests/test_qx_config.py.
            continue
        parts = [p.strip() for p in rest.split(", ")]
        members, regex, opts = [], None, {}
        for part in parts[1:]:
            if part.startswith("server-tag-regex="):
                regex = part.removeprefix("server-tag-regex=")
            elif part.startswith(("check-interval=", "tolerance=")):
                key, value = part.split("=", 1)
                opts["interval" if key == "check-interval" else key] = value
            elif not part.startswith("img-url="):
                members.append(part)
        groups[parts[0]] = (kind, members, regex, opts)
    return groups


def check_home_and_away(loon: dict, failures: list[str]) -> None:
    groups, ssid = loon["groups"], loon["ssid"]
    known = set(groups) | set(ssid) | set(loon["filters"]) | BUILTIN

    # The wrapped groups are shared with the QX template and the QX snippet generator.
    expected_ssid = [name + AUTO_SUFFIX for name in build_rules.HOME_AUTO_GROUPS]
    if list(ssid) != expected_ssid:
        failures.append(f"ssid groups must be {expected_ssid} (build_rules.HOME_AUTO_GROUPS), got {list(ssid)}")
    group_lines = [line.partition(" = ")[2].startswith("ssid, ") for line in loon["sections"]["Proxy Group"]]
    if group_lines[len(group_lines) - len(ssid) :] != [True] * len(ssid):
        failures.append("ssid groups must be listed after every other group")

    for name, branches in ssid.items():
        if not name.endswith(AUTO_SUFFIX):
            failures.append(f"ssid group {name!r}: name must end with {AUTO_SUFFIX!r}")
            continue
        away = name.removesuffix(AUTO_SUFFIX)
        if branches.get("default") != away or branches.get("cellular") != away:
            failures.append(f"ssid group {name!r}: default and cellular must be {away!r}")
        if branches.get(HOME_SSID) != "DIRECT":
            failures.append(f"ssid group {name!r}: {HOME_SSID} must be DIRECT")
        if away not in groups:
            failures.append(f"ssid group {name!r}: away group {away!r} is missing")

    for name, (_, members, _) in groups.items():
        for member in members:
            if member not in known:
                failures.append(f"group {name!r}: unknown member {member!r}")

    # Every policy a rule points at must end in DIRECT or a reject on the home Wi-Fi, so the
    # router does all proxying there.
    policies = {opts["policy"] for _, opts in loon["remote_rules"]}
    policies |= {rule[-1] for rule in loon["local_rules"]}
    # A plugin's own PROXY rules go to the policy= set on its [Plugin] line. Plugins known to carry
    # PROXY rules must set exactly one policy when enabled, or they would bypass the home check.
    for line in loon["sections"].get("Plugin", []):
        parts = [re.sub(r"\s*=\s*", "=", p.strip(), count=1) for p in line.split(",")]
        # Anything but an explicit enabled=false counts as enabled.
        if "enabled=false" in (p.lower() for p in parts):
            continue
        if not any(key in parts[0] for key in PLUGINS_WITH_PROXY_RULES):
            continue
        plugin_policies = [p.split("=", 1)[1] for p in parts if p.startswith("policy=")]
        if len(plugin_policies) != 1 or not plugin_policies[0]:
            failures.append(f"plugin {parts[0]} must set exactly one policy=")
    policies |= {
        part.split("=", 1)[1]
        for line in loon["sections"].get("Plugin", [])
        for part in (re.sub(r"\s*=\s*", "=", p.strip(), count=1) for p in line.split(","))
        if part.startswith("policy=")
    }
    for policy in sorted(policies):
        if policy in BUILTIN or policy in ssid:
            continue
        if policy not in groups:
            failures.append(f"rule policy {policy!r} does not exist")
        elif not set(groups[policy][1]) <= BUILTIN:
            failures.append(f"rule policy {policy!r} is not DIRECT/REJECT at home; wrap it in an ssid group")

    graph = {name: members for name, (_, members, _) in groups.items()}
    graph.update({name: list(branches.values()) for name, branches in ssid.items()})

    def visit(node: str, stack: tuple[str, ...]) -> None:
        if node in stack:
            failures.append(f"group cycle: {' -> '.join(stack + (node,))}")
            return
        for child in graph.get(node, []):
            visit(child, stack + (node,))

    for name in graph:
        visit(name, ())


def check_qx_parity(loon: dict, failures: list[str]) -> None:
    qx = load_qx_groups()
    loon_groups, filters = loon["groups"], loon["filters"]
    expected_names = set(qx)
    if set(loon_groups) != expected_names:
        failures.append(
            f"Loon groups differ from QX: missing {sorted(expected_names - set(loon_groups))}, "
            f"extra {sorted(set(loon_groups) - expected_names)}"
        )
    for name, (qx_kind, qx_members, regex, qx_opts) in qx.items():
        loon_name = name
        if loon_name not in loon_groups:
            continue
        kind, members, opts = loon_groups[loon_name]
        if kind != QX_KINDS[qx_kind]:
            failures.append(f"{loon_name!r}: kind {kind} != QX {qx_kind}")
        if regex is not None:
            if len(members) != 1 or filters.get(members[0]) != regex:
                failures.append(f"{loon_name!r}: node filter does not match the QX regex")
        elif members != qx_members:
            failures.append(f"{loon_name!r}: members {members} != QX {qx_members}")
        for key, value in qx_opts.items():
            if opts.get(key) != value:
                failures.append(f"{loon_name!r}: {key}={opts.get(key)} != QX {value}")


def wrapped(loon: dict, policy: str) -> str:
    """The policy a Loon rule should use for a QX group: its "· 自动" group if it has one.

    QX rules already point at the "· 自动" wrappers, which pass through unchanged."""
    return policy + AUTO_SUFFIX if policy + AUTO_SUFFIX in loon["ssid"] else policy


def check_rules(loon: dict, failures: list[str]) -> None:
    remote = loon["remote_rules"]
    urls = [url for url, _ in remote]
    duplicates = sorted({url for url in urls if urls.count(url) > 1})
    if duplicates:
        failures.append(f"loon: duplicate [Remote Rule] URLs: {duplicates}")
    for url, opts in remote:
        if url.startswith(REPO_RAW) and not (ROOT / url.removeprefix(REPO_RAW)).is_file():
            failures.append(f"loon: {url} has no file in the repo")

    # Expected order: repo rules in manifest order, then third-party lists in QX order, then GEOIP.
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["rule_sets"]
    expected_repo = [
        (f"{REPO_RAW}loon/rules/{item['id']}.list", wrapped(loon, item["qx_policy"]), "true")
        for item in manifest
    ]
    actual_repo = [
        (url, opts.get("policy"), opts.get("enabled")) for url, opts in remote[: len(expected_repo)]
    ]
    if actual_repo != expected_repo:
        failures.append(
            "loon: [Remote Rule] must start with every rules/local_rules.yaml set in manifest order, "
            "policy = qx_policy (or its · 自动 group), enabled=true"
        )

    # QX third-party lists and Loon should route the same tags to the same policies, in order.
    qx_remote = []
    for line in sections(QX_CONFIG.read_text(encoding="utf-8"))["filter_remote"]:
        opts = dict(p.strip().split("=", 1) for p in line.split(",")[1:] if "=" in p)
        if "force-policy" in opts:
            qx_remote.append((opts["tag"], wrapped(loon, opts["force-policy"]), opts.get("enabled")))
    loon_remote = [
        (opts.get("tag"), opts.get("policy"), opts.get("enabled"))
        for _, opts in remote[len(expected_repo) : -1]
    ]
    if loon_remote != qx_remote:
        failures.append(
            "loon: third-party [Remote Rule] tags/policies/enabled/order differ from QX [filter_remote]"
        )

    last_url, last_opts = remote[-1]
    if (last_url, last_opts.get("policy"), last_opts.get("enabled")) != (
        f"{REPO_RAW}loon/rules/geoip_cn.list",
        wrapped(loon, "🇨🇳 国内服务"),
        "true",
    ):
        failures.append("loon: geoip_cn.list must be the last [Remote Rule], policy 🇨🇳 国内服务 · 自动, enabled=true")
    geoip_lines = [
        line.strip()
        for line in (ROOT / "loon" / "rules" / "geoip_cn.list").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if geoip_lines != ["GEOIP,CN"]:
        failures.append(f"loon/rules/geoip_cn.list must contain exactly GEOIP,CN, got {geoip_lines}")
    if any(rule[0].upper() == "GEOIP" for rule in loon["local_rules"]):
        failures.append("loon: GEOIP in [Rule] would match before remote IP rules")
    if loon["local_rules"][-1] != ["FINAL", wrapped(loon, "🐟 兜底分流")]:
        failures.append("loon: [Rule] must end with FINAL,🐟 兜底分流 · 自动")


def check_runtime(loon: dict, failures: list[str]) -> None:
    general = dict(
        (key.strip(), value.strip())
        for key, _, value in (line.partition("=") for line in loon["sections"]["General"])
    )
    if general.get("ip-mode") != "dual":
        failures.append("loon: [General] ip-mode must be dual")
    if "443" not in [p.strip() for p in general.get("disable-udp-ports", "").split(",")]:
        failures.append("loon: [General] disable-udp-ports must include 443")
    if "ssid-trigger" in general:
        failures.append("loon: ssid-trigger would switch modes; home routing uses ssid groups")
    host = loon["sections"]["Host"]
    if f"ssid:{HOME_SSID} = server:system" not in host:
        failures.append(f"loon: [Host] must map ssid:{HOME_SSID} to server:system")
    mitm = dict(
        (key.strip(), value.strip())
        for key, _, value in (line.partition("=") for line in loon["sections"]["MitM"])
    )
    for key in ("ca-p12", "ca-passphrase"):
        if mitm.get(key):
            failures.append(f"loon: [MitM] {key} must stay empty in the template")
    if loon["sections"].get("Remote Proxy"):
        failures.append("loon: [Remote Proxy] must only hold a commented placeholder")


def check_hosted_plugins(failures: list[str]) -> None:
    # Loon runs every matching rewrite and the last redirect wins (QX stops at the first match),
    # so the Q-Search catch-all must come first or it overrides every keyword redirect.
    lines = (ROOT / "loon" / "plugins" / "Q-Search.plugin").read_text(encoding="utf-8").splitlines()
    rewrites = lines[lines.index("[Rewrite]") + 1 :]
    rules = [l for l in rewrites[: next((i for i, l in enumerate(rewrites) if l.startswith("[")), None)]
             if l.strip() and not l.startswith("#")]
    if not rules or not rules[0].startswith(r"^https:\/\/duckduckgo.com\/\?q=([^&]+).+ "):
        failures.append("loon/plugins/Q-Search.plugin: the catch-all Google rule must be the first rewrite")


# fmz200 split plugins reviewed on 2026-10-05: script-free, pinned to this commit, off until each passes
# a device check. Written out here so that editing the template cannot move the expectation with it.
FMZ200_COMMIT = "5d5f63fcf98bc69d5f8f1b1bae6f86a01ee4bb97"
FMZ200_PLUGINS = {"WeChatOfficialAccount", "Meituan-MeituanWaimai", "Hupu", "Mijia", "ChinaTelecom", "MaoYan",
                  "LeKe", "Douban", "ChinaMobile"}


def check_fmz200_plugins(failures: list[str]) -> None:
    lines = [l for l in sections(LOON_CONFIG.read_text(encoding="utf-8")).get("Plugin", []) if "fmz200/wool_scripts" in l]
    names = {re.sub(r"\.lpx$", "", l.split(",")[0].rsplit("/", 1)[-1]) for l in lines}
    if names != FMZ200_PLUGINS or len(lines) != len(FMZ200_PLUGINS):
        failures.append(f"loon: fmz200 plugins must be exactly {sorted(FMZ200_PLUGINS)}, got {sorted(names)}")
    for line in lines:
        url, _, rest = line.partition(",")
        if f"/fmz200/wool_scripts/{FMZ200_COMMIT}/Loon/plugin/split/" not in url:
            failures.append(f"loon: fmz200 plugin must be pinned to {FMZ200_COMMIT}: {url}")
        if "enabled=false" not in rest.replace(" ", ""):
            failures.append(f"loon: fmz200 plugin must stay off until checked on the device: {url}")


def check_generator(failures: list[str]) -> None:
    sample = {
        "domain_suffix": ["example.com"],
        "domain": ["a.example.com"],
        "domain_keyword": ["example"],
        "ip_cidr": ["192.0.2.0/24"],
        "ip_cidr6": ["2001:db8::/32"],
    }
    body = [
        line
        for line in build_rules.render_loon_list("sample.yaml", sample).splitlines()
        if not line.startswith("#")
    ]
    expected = [
        "DOMAIN-SUFFIX,example.com",
        "DOMAIN,a.example.com",
        "DOMAIN-KEYWORD,example",
        "IP-CIDR,192.0.2.0/24",
        "IP-CIDR6,2001:db8::/32",
    ]
    if body != expected:
        failures.append(f"render_loon_list: {body} != {expected}")
    try:
        build_rules.render_loon_list("sample.yaml", {"domain_regex": [r"^ad\."]})
    except ValueError:
        pass
    else:
        failures.append("render_loon_list: domain_regex must be rejected")

    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["rule_sets"]
    expected_paths = {build_rules.LOON_RULES_DIR / f"{item['id']}.list" for item in manifest}
    built = {path for path in build_rules.build_outputs() if path.parent == build_rules.LOON_RULES_DIR}
    if built != expected_paths:
        failures.append("build_rules: Loon outputs do not cover every manifest rule set")


def main() -> int:
    failures: list[str] = []
    loon = load_loon()
    check_home_and_away(loon, failures)
    check_qx_parity(loon, failures)
    check_rules(loon, failures)
    check_runtime(loon, failures)
    check_hosted_plugins(failures)
    check_fmz200_plugins(failures)
    check_generator(failures)
    if failures:
        print("Loon config checks failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print(
        f"Loon config checks passed: {len(loon['ssid'])} ssid groups, {len(loon['groups'])} groups, "
        f"{len(loon['remote_rules'])} remote rules."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
