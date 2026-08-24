#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
GAME_POLICY = "🎮 游戏平台"

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


def values(path: Path) -> set[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    result: set[str] = set()
    for field in ("domain_suffix", "domain"):
        entries = data.get(field, [])
        if not isinstance(entries, list):
            raise AssertionError(f"{path}: {field} must be a list")
        result.update(str(item) for item in entries)
    return result


def main() -> int:
    steam_downloads = values(ROOT / "rules" / "steam_download.yaml")
    other_downloads = values(ROOT / "rules" / "download_extra.yaml")
    if steam_downloads & other_downloads:
        raise AssertionError("Steam and non-Steam download rules overlap")
    if other_downloads != EXPECTED_NON_STEAM_DOWNLOADS:
        raise AssertionError(
            f"unexpected non-Steam download rules: {sorted(other_downloads)}"
        )
    if len(steam_downloads) != 21:
        raise AssertionError(f"expected 21 Steam CDN rules, got {len(steam_downloads)}")

    manifest = yaml.safe_load(
        (ROOT / "rules" / "local_rules.yaml").read_text(encoding="utf-8")
    ) or {}
    steam_item = next(
        (item for item in manifest.get("rule_sets", []) if item.get("id") == "steam_download"),
        None,
    )
    if not steam_item:
        raise AssertionError("steam_download is missing from rules/local_rules.yaml")
    if steam_item.get("clash_policy") != GAME_POLICY:
        raise AssertionError("steam_download has the wrong Clash policy")
    if steam_item.get("qx_policy") != GAME_POLICY:
        raise AssertionError("steam_download has the wrong QX policy")

    config = yaml.safe_load((ROOT / "clash" / "config.yaml").read_text(encoding="utf-8")) or {}
    groups = {group.get("name") for group in config.get("proxy-groups", [])}
    if GAME_POLICY not in groups:
        raise AssertionError("Game platform proxy group is missing")
    if "🎮 Steam" in groups:
        raise AssertionError("standalone Steam proxy group must be removed")

    providers = config.get("rule-providers", {})
    if "SteamDownload" not in providers or "Game" not in providers:
        raise AssertionError("Game platform or Steam download provider is missing")
    if "Steam" in providers:
        raise AssertionError("standalone Steam provider must be removed")

    rules = config.get("rules", [])
    if rules[:3] != EXPECTED_PROCESS_RULES:
        raise AssertionError("Steam process rules must be the first three rules")
    expected_targets = {
        "RULE-SET,SteamDownload,🎮 游戏平台",
        "RULE-SET,Game,🎮 游戏平台",
        "RULE-SET,DownloadExtra,DIRECT",
        "RULE-SET,Download,DIRECT",
    }
    missing = sorted(expected_targets - set(rules))
    if missing:
        raise AssertionError(f"Steam/download rule targets are incomplete: {missing}")
    if any(rule.startswith("RULE-SET,Steam,") for rule in rules):
        raise AssertionError("standalone Steam rule must be removed")

    qx_lines = (ROOT / "quantumultx" / "filter_remote.snippet").read_text(
        encoding="utf-8"
    ).splitlines()
    for domain in steam_downloads:
        if not any(domain in line and line.endswith(f", {GAME_POLICY}") for line in qx_lines):
            raise AssertionError(f"QX Steam download rule has wrong policy: {domain}")
    for domain in other_downloads:
        if not any(domain in line and line.endswith(", DIRECT") for line in qx_lines):
            raise AssertionError(f"QX non-Steam download rule has wrong policy: {domain}")

    print(
        f"Steam policy tests passed: {len(steam_downloads)} Steam CDN rules and "
        f"{len(other_downloads)} non-Steam download rules."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
