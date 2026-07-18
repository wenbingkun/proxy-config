#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
STEAM_POLICY = "🎮 Steam"
QX_GAME_POLICY = "🎮 游戏平台"

EXPECTED_NON_STEAM_DOWNLOADS = {
    "download.microsoft.com",
    "download.visualstudio.microsoft.com",
    "officecdn.microsoft.com",
    "winget.microsoft.com",
    "registry.npmjs.org",
}

EXPECTED_PROCESS_RULES = [
    "PROCESS-NAME,steam.exe,🎮 Steam",
    "PROCESS-NAME,steamwebhelper.exe,🎮 Steam",
    "PROCESS-NAME,steamservice.exe,🎮 Steam",
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
    if steam_item.get("clash_policy") != STEAM_POLICY:
        raise AssertionError("steam_download has the wrong Clash policy")
    if steam_item.get("qx_policy") != QX_GAME_POLICY:
        raise AssertionError("steam_download has the wrong QX policy")

    config = yaml.safe_load((ROOT / "clash" / "config.yaml").read_text(encoding="utf-8")) or {}
    steam_group = next(
        (group for group in config.get("proxy-groups", []) if group.get("name") == STEAM_POLICY),
        None,
    )
    if not steam_group:
        raise AssertionError("Steam proxy group is missing")
    if steam_group.get("proxies", [None])[0] != "DIRECT":
        raise AssertionError("Steam proxy group must default to DIRECT")
    for required in ("🚀 手动切换", "🌐 故障转移"):
        if required not in steam_group.get("proxies", []):
            raise AssertionError(f"Steam proxy group is missing {required}")

    providers = config.get("rule-providers", {})
    if "SteamDownload" not in providers or "Steam" not in providers:
        raise AssertionError("Steam platform/download provider is missing")

    rules = config.get("rules", [])
    if rules[:3] != EXPECTED_PROCESS_RULES:
        raise AssertionError("Steam process rules must be the first three rules")
    expected_targets = {
        "RULE-SET,SteamDownload,🎮 Steam",
        "RULE-SET,Steam,🎮 Steam",
        "RULE-SET,DownloadExtra,DIRECT",
        "RULE-SET,Download,DIRECT",
    }
    missing = sorted(expected_targets - set(rules))
    if missing:
        raise AssertionError(f"Steam/download rule targets are incomplete: {missing}")

    qx_lines = (ROOT / "quantumultx" / "filter_remote.snippet").read_text(
        encoding="utf-8"
    ).splitlines()
    for domain in steam_downloads:
        if not any(domain in line and line.endswith(f", {QX_GAME_POLICY}") for line in qx_lines):
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
