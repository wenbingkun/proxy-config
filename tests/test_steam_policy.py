#!/usr/bin/env python3
from __future__ import annotations

import copy
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_rules import home_auto  # noqa: E402

GAME_POLICY = "🎮 游戏平台"
STEAM_DOWNLOAD_RULE = "RULE-SET,SteamDownload,DIRECT,no-resolve"

EXPECTED_STEAM_SUFFIXES = {
    "cdn-ali.content.steamchina.com",
    "cdn-qc.content.steamchina.com",
    "cdn-ws.content.steamchina.com",
    "dl.steam.clngaa.com",
    "edge.steam-dns.top.comcast.net",
    "st.dl.bscstorage.net",
    "st.dl.eccdnx.com",
    "st.dl.pinyuncloud.com",
    "steamcontent.com",
    "steampipe-kr.akamaized.net",
    "steampipe-partner.akamaized.net",
    "steampipe.akamaized.net",
}
EXPECTED_STEAM_DOMAINS = {"xz.pphimalayanrt.com"}
EXCLUDED_STEAM_DOMAINS = {
    "steamcdn-a.akamaihd.net",
    "steamcontent.cc",
    "dl.steam.ksyna.com",
    "f3b7q2p3.ssl.hwcdn.net",
    "steam.apac.qtlglb.com",
    "steam.eca.qtlglb.com",
    "steam.naeu.qtlglb.com",
    "steam.ru.qtlglb.com",
    "steamcontent.tnkjmec.com",
}

EXPECTED_NON_STEAM_DOWNLOADS = {
    "download.microsoft.com",
    "download.visualstudio.microsoft.com",
    "officecdn.microsoft.com",
    "winget.microsoft.com",
}

EXPECTED_PROCESS_RULES = [
    "PROCESS-NAME,steam.exe,🎮 游戏平台",
    "PROCESS-NAME,steamwebhelper.exe,🎮 游戏平台",
    "PROCESS-NAME,steamservice.exe,🎮 游戏平台",
]

MIHOMO_CONFIGS = (
    ROOT / "mihomo" / "verge" / "config.yaml",
    ROOT / "mihomo" / "verge" / "config-single.yaml",
    ROOT / "mihomo" / "shellcrash" / "config-router.template.yaml",
    ROOT / "mihomo" / "shellcrash" / "config-router-single.template.yaml",
)

# Minimal upstream Game overlap fixture to test priority and exclusions offline.
GAME_OVERLAP_ENTRIES = [
    "DOMAIN-SUFFIX,steampowered.com",
    "DOMAIN-SUFFIX,steamcommunity.com",
    "DOMAIN-SUFFIX,steamcdn-a.akamaihd.net",
    "DOMAIN-SUFFIX,steamcontent.com",
    "DOMAIN-SUFFIX,dl.steam.clngaa.com",
    "DOMAIN-SUFFIX,st.dl.eccdnx.com",
    "DOMAIN-SUFFIX,steampipe.akamaized.net",
    "DOMAIN,xz.pphimalayanrt.com",
]


def load_rule_fields(path: Path) -> tuple[set[str], set[str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    result: dict[str, set[str]] = {}
    for field in ("domain_suffix", "domain"):
        entries = data.get(field, [])
        if not isinstance(entries, list):
            raise AssertionError(f"{path}: {field} must be a list")
        result[field] = {str(item) for item in entries}
    return result["domain_suffix"], result["domain"]


def match_rule(entry: str, host: str) -> bool:
    kind, value = [part.strip() for part in entry.split(",", 1)]
    if kind == "DOMAIN":
        return host == value
    if kind == "DOMAIN-SUFFIX":
        return host == value or host.endswith("." + value)
    if kind == "DOMAIN-KEYWORD":
        return value in host
    return False


def eval_mihomo(
    rules: list[str], steam_payload: list[str], host: str, process_name: str | None = None
) -> str | None:
    providers = {"SteamDownload": steam_payload, "Game": GAME_OVERLAP_ENTRIES}
    for rule in rules:
        parts = [part.strip() for part in rule.split(",")]
        kind = parts[0]
        if kind == "PROCESS-NAME":
            if process_name == parts[1]:
                return parts[2]
        elif kind == "RULE-SET" and parts[1] in providers:
            if any(match_rule(entry, host) for entry in providers[parts[1]]):
                return parts[2]
    return None


def check_mihomo_config(path: Path, steam_payload: list[str]) -> None:
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    groups = {group.get("name") for group in config.get("proxy-groups", [])}
    if GAME_POLICY not in groups:
        raise AssertionError(f"{path.name}: Game platform proxy group is missing")
    if "🎮 Steam" in groups:
        raise AssertionError(f"{path.name}: standalone Steam proxy group must be removed")

    providers = config.get("rule-providers", {})
    if "SteamDownload" not in providers or "Game" not in providers:
        raise AssertionError(f"{path.name}: Game platform or Steam download provider is missing")
    cache_path = providers["SteamDownload"].get("path")
    if cache_path != "./cache/rulesets/SteamDownloadDirect.yaml":
        raise AssertionError(f"{path.name}: SteamDownload must use the dedicated direct cache")
    if any(
        name != "SteamDownload" and provider.get("path") == cache_path
        for name, provider in providers.items()
    ):
        raise AssertionError(f"{path.name}: SteamDownload cache is shared by another provider")
    if "Steam" in providers:
        raise AssertionError(f"{path.name}: standalone Steam provider must be removed")

    raw_rules = config.get("rules", [])
    steam_rules = [rule for rule in raw_rules if rule.startswith("RULE-SET,SteamDownload,")]
    if steam_rules != [STEAM_DOWNLOAD_RULE]:
        raise AssertionError(
            f"{path.name}: expected exactly one {STEAM_DOWNLOAD_RULE!r}, got {steam_rules}"
        )
    if raw_rules[1:4] != EXPECTED_PROCESS_RULES:
        raise AssertionError(f"{path.name}: Steam process rules must follow SteamDownload directly")
    steam_idx = raw_rules.index(STEAM_DOWNLOAD_RULE)
    for proc_rule in EXPECTED_PROCESS_RULES:
        if steam_idx >= raw_rules.index(proc_rule):
            raise AssertionError(f"{path.name}: {STEAM_DOWNLOAD_RULE} must precede {proc_rule}")

    expected_other = {
        "RULE-SET,Game,🎮 游戏平台,no-resolve",
        "RULE-SET,DownloadExtra,DIRECT,no-resolve",
        "RULE-SET,Download,DIRECT,no-resolve",
    }
    missing = sorted(expected_other - set(raw_rules))
    if missing:
        raise AssertionError(f"{path.name}: Steam/download rule targets are incomplete: {missing}")
    if any(rule.startswith("RULE-SET,Steam,") for rule in raw_rules):
        raise AssertionError(f"{path.name}: standalone Steam rule must be removed")

    # Positive & control evaluations (with and without Windows steam.exe process name).
    for host in (
        "dl.steam.clngaa.com",
        "st.dl.eccdnx.com",
        "xz.pphimalayanrt.com",
        "cache1-hkg1.steamcontent.com",
        "fastly.cdn.steampipe.steamcontent.com",
        "steampipe.akamaized.net",
    ):
        for proc in (None, "steam.exe"):
            got = eval_mihomo(raw_rules, steam_payload, host, proc)
            if got != "DIRECT":
                raise AssertionError(f"{path.name}: {host} (proc={proc}) expected DIRECT, got {got!r}")

    for host in ("store.steampowered.com", "steamcommunity.com", "steamcdn-a.akamaihd.net"):
        for proc in (None, "steam.exe"):
            got = eval_mihomo(raw_rules, steam_payload, host, proc)
            if got != GAME_POLICY:
                raise AssertionError(
                    f"{path.name}: {host} (proc={proc}) expected {GAME_POLICY!r}, got {got!r}"
                )

    # Exact DOMAIN match on xz.pphimalayanrt.com must not catch subdomains.
    if eval_mihomo(raw_rules, steam_payload, "child.xz.pphimalayanrt.com", "steam.exe") != GAME_POLICY:
        raise AssertionError(f"{path.name}: subdomain of xz.pphimalayanrt.com should stay in Game group")
    if eval_mihomo(raw_rules, steam_payload, "child.xz.pphimalayanrt.com", None) == "DIRECT":
        raise AssertionError(f"{path.name}: subdomain of xz.pphimalayanrt.com must not hit SteamDownload")

    # Negative mutations: detect moved rule, removed rule, or re-added steamcdn-a.
    moved = copy.deepcopy(raw_rules)
    moved.remove(STEAM_DOWNLOAD_RULE)
    moved.insert(moved.index("PROCESS-NAME,steamservice.exe,🎮 游戏平台") + 1, STEAM_DOWNLOAD_RULE)
    if eval_mihomo(moved, steam_payload, "cache1-hkg1.steamcontent.com", "steam.exe") != GAME_POLICY:
        raise AssertionError(f"{path.name}: mutation(after-process) did not trigger expected regression")

    removed = [r for r in raw_rules if r != STEAM_DOWNLOAD_RULE]
    for host in ("cache1-hkg1.steamcontent.com", "dl.steam.clngaa.com", "xz.pphimalayanrt.com"):
        if eval_mihomo(removed, steam_payload, host, None) != GAME_POLICY:
            raise AssertionError(f"{path.name}: mutation(removed) on {host} did not regress to Game")

    polluted_payload = steam_payload + ["DOMAIN-SUFFIX,steamcdn-a.akamaihd.net"]
    if eval_mihomo(raw_rules, polluted_payload, "steamcdn-a.akamaihd.net", "steam.exe") != "DIRECT":
        raise AssertionError(f"{path.name}: mutation(readd-steamcdn-a) did not trigger regression")


def main() -> int:
    steam_suffixes, steam_domains = load_rule_fields(ROOT / "rules" / "steam_download.yaml")
    if steam_suffixes != EXPECTED_STEAM_SUFFIXES:
        raise AssertionError(
            f"unexpected Steam domain_suffix rules: {sorted(steam_suffixes)} != {sorted(EXPECTED_STEAM_SUFFIXES)}"
        )
    if steam_domains != EXPECTED_STEAM_DOMAINS:
        raise AssertionError(
            f"unexpected Steam domain rules: {sorted(steam_domains)} != {sorted(EXPECTED_STEAM_DOMAINS)}"
        )
    all_steam = steam_suffixes | steam_domains
    if all_steam & EXCLUDED_STEAM_DOMAINS:
        raise AssertionError(f"excluded Steam domains present: {sorted(all_steam & EXCLUDED_STEAM_DOMAINS)}")

    other_suffixes, other_domains = load_rule_fields(ROOT / "rules" / "download_extra.yaml")
    other_downloads = other_suffixes | other_domains
    if all_steam & other_downloads:
        raise AssertionError("Steam and non-Steam download rules overlap")
    if other_downloads != EXPECTED_NON_STEAM_DOWNLOADS:
        raise AssertionError(f"unexpected non-Steam download rules: {sorted(other_downloads)}")

    manifest = yaml.safe_load((ROOT / "rules" / "local_rules.yaml").read_text(encoding="utf-8")) or {}
    steam_item = next(
        (item for item in manifest.get("rule_sets", []) if item.get("id") == "steam_download"),
        None,
    )
    if not steam_item:
        raise AssertionError("steam_download is missing from rules/local_rules.yaml")
    if steam_item.get("clash_policy") != "DIRECT":
        raise AssertionError(f"steam_download clash_policy must be DIRECT, got {steam_item.get('clash_policy')!r}")
    if steam_item.get("qx_policy") != GAME_POLICY:
        raise AssertionError("steam_download has the wrong QX policy")

    mihomo_payload = (
        yaml.safe_load((ROOT / "mihomo" / "rules" / "steam_download.yaml").read_text(encoding="utf-8")) or {}
    ).get("payload", [])
    expected_payload = [f"DOMAIN-SUFFIX,{d}" for d in sorted(EXPECTED_STEAM_SUFFIXES)] + [
        f"DOMAIN,{d}" for d in sorted(EXPECTED_STEAM_DOMAINS)
    ]
    if sorted(mihomo_payload) != sorted(expected_payload) or len(mihomo_payload) != 13:
        raise AssertionError(f"mihomo/rules/steam_download.yaml payload mismatch: {mihomo_payload}")

    for path in MIHOMO_CONFIGS:
        check_mihomo_config(path, mihomo_payload)

    for list_path in (
        ROOT / "loon" / "rules" / "steam_download.list",
        ROOT / "surge" / "rules" / "steam_download.list",
    ):
        entries = [
            line.strip()
            for line in list_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if sorted(entries) != sorted(expected_payload) or len(entries) != 13:
            raise AssertionError(f"{list_path.relative_to(ROOT)} mismatch: {entries}")

    qx_lines = (ROOT / "quantumultx" / "filter" / "repo.snippet").read_text(encoding="utf-8").splitlines()
    for domain in EXPECTED_STEAM_SUFFIXES:
        expected_line = f"host-suffix, {domain}, {home_auto(GAME_POLICY)}"
        if expected_line not in qx_lines:
            raise AssertionError(f"QX Steam suffix rule missing or wrong policy: {expected_line}")
    for domain in EXPECTED_STEAM_DOMAINS:
        expected_line = f"host, {domain}, {home_auto(GAME_POLICY)}"
        if expected_line not in qx_lines:
            raise AssertionError(f"QX Steam exact rule missing or wrong policy: {expected_line}")
    for excluded in EXCLUDED_STEAM_DOMAINS:
        if any(excluded in line for line in qx_lines):
            raise AssertionError(f"QX repo.snippet still contains excluded Steam host: {excluded}")
    for domain in other_downloads:
        if f"host-suffix, {domain}, DIRECT" not in qx_lines:
            raise AssertionError(f"QX non-Steam download rule has wrong policy: {domain}")

    print(
        f"Steam policy tests passed: {len(all_steam)} Steam CDN rules "
        f"({len(EXPECTED_STEAM_SUFFIXES)} suffix + {len(EXPECTED_STEAM_DOMAINS)} exact), "
        f"4 Mihomo configs, 3 mutations, and {len(other_downloads)} non-Steam download rules."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
