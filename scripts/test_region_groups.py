#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
CLASH_CONFIG = ROOT / "clash" / "config.yaml"
QX_CONFIG = ROOT / "quantumultx" / "bootstrap.example.conf"

REGION_GROUPS = (
    "🇭🇰 香港节点",
    "🇨🇳 台湾节点",
    "🇯🇵 日本节点",
    "🇰🇷 韩国节点",
    "🇸🇬 狮城节点",
    "🇺🇸 美国节点",
    "🌏 东南亚节点",
    "🕌 中东中亚节点",
    "🌏 亚洲其他节点",
    "🇪🇺 欧洲节点",
    "🌎 北美节点",
    "🌎 南美节点",
    "🏝️ 大洋洲节点",
    "🌍 非洲节点",
)

REMOVED_GROUPS = (
    "🔄 备用线路",
    "🇲🇴 澳门节点",
    "🇬🇧 英国节点",
    "🇨🇦 加拿大节点",
    "🇦🇺 澳洲节点",
    "🇳🇿 新西兰节点",
    "🕌 中东节点",
    "🌏 中亚节点",
    "🇦🇶 南极节点",
)

EXPECTED_SAMPLES = {
    "HK Hong Kong": "🇭🇰 香港节点",
    "TW Taiwan": "🇨🇳 台湾节点",
    "JP Tokyo": "🇯🇵 日本节点",
    "KR Seoul": "🇰🇷 韩国节点",
    "SG Singapore": "🇸🇬 狮城节点",
    "US Los Angeles": "🇺🇸 美国节点",
    "Malaysia Kuala Lumpur": "🌏 东南亚节点",
    "India Mumbai": "🌏 亚洲其他节点",
    "Mongolia Ulaanbaatar": "🌏 亚洲其他节点",
    "Kazakhstan Almaty": "🕌 中东中亚节点",
    "Turkey Istanbul": "🕌 中东中亚节点",
    "UK London": "🇪🇺 欧洲节点",
    "Canada Toronto": "🌎 北美节点",
    "Mexico City": "🌎 北美节点",
    "Puerto Rico": "🌎 北美节点",
    "Brazil Sao Paulo": "🌎 南美节点",
    "Australia Sydney": "🏝️ 大洋洲节点",
    "Fiji Suva": "🏝️ 大洋洲节点",
    "South Africa Johannesburg": "🌍 非洲节点",
}

NO_REGION_SAMPLES = (
    "Made in premium line",
    "My backup server",
    "Premium at edge",
    "Antarctica Research",
)


def load_clash_filters() -> dict[str, str]:
    data = yaml.safe_load(CLASH_CONFIG.read_text(encoding="utf-8")) or {}
    groups = data.get("proxy-groups", [])
    filters = {
        group["name"]: group["filter"]
        for group in groups
        if isinstance(group, dict)
        and group.get("name") in REGION_GROUPS
        and isinstance(group.get("filter"), str)
    }
    missing = sorted(set(REGION_GROUPS) - set(filters))
    if missing:
        raise AssertionError(f"Clash missing region filters: {missing}")
    return filters


def load_qx_filters() -> dict[str, str]:
    filters: dict[str, str] = {}
    for line in QX_CONFIG.read_text(encoding="utf-8").splitlines():
        if not line.startswith("static=") or ", server-tag-regex=" not in line:
            continue
        name, rest = line.removeprefix("static=").split(", server-tag-regex=", 1)
        if name not in REGION_GROUPS:
            continue
        regex, separator, _ = rest.rpartition(", img-url=")
        if not separator:
            raise AssertionError(f"QX region group {name!r} has no img-url")
        filters[name] = regex
    missing = sorted(set(REGION_GROUPS) - set(filters))
    if missing:
        raise AssertionError(f"QX missing region filters: {missing}")
    return filters


def matching_groups(name: str, patterns: dict[str, re.Pattern[str]]) -> list[str]:
    return [group for group, pattern in patterns.items() if pattern.search(name)]


def main() -> int:
    clash_filters = load_clash_filters()
    qx_filters = load_qx_filters()
    if clash_filters != qx_filters:
        mismatched = sorted(
            name for name in REGION_GROUPS if clash_filters[name] != qx_filters[name]
        )
        raise AssertionError(f"Clash/QX region regex mismatch: {mismatched}")

    patterns = {name: re.compile(regex) for name, regex in clash_filters.items()}
    for sample, expected in EXPECTED_SAMPLES.items():
        matched = matching_groups(sample, patterns)
        if matched != [expected]:
            raise AssertionError(f"{sample!r}: expected {[expected]}, got {matched}")

    for sample in NO_REGION_SAMPLES:
        matched = matching_groups(sample, patterns)
        if matched:
            raise AssertionError(f"{sample!r}: expected no region, got {matched}")

    current_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            CLASH_CONFIG,
            ROOT / "clash" / "config-single.yaml",
            ROOT / "clash" / "config-router.template.yaml",
            ROOT / "clash" / "config-router-single.template.yaml",
            QX_CONFIG,
            ROOT / "quantumultx" / "filter_remote.snippet",
            ROOT / "rules" / "local_rules.yaml",
        )
    )
    residual = [name for name in REMOVED_GROUPS if name in current_text]
    if residual:
        raise AssertionError(f"removed region groups still referenced: {residual}")

    print(
        f"Region group tests passed: {len(REGION_GROUPS)} groups, "
        f"{len(EXPECTED_SAMPLES)} positive and {len(NO_REGION_SAMPLES)} negative samples."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
