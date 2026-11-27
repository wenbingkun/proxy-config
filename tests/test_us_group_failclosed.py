#!/usr/bin/env python3
"""Offline regression contracts for US group fail-closed REJECT (A01)
and preservation of unchanged AI group options / rule order.
"""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

MIHOMO_CONFIGS = (
    ("mihomo/verge/config.yaml", ["Sub", "Sub2"]),
    ("mihomo/verge/config-single.yaml", ["Sub"]),
    ("mihomo/shellcrash/config-router.template.yaml", ["Sub", "Sub2"]),
    ("mihomo/shellcrash/config-router-single.template.yaml", ["Sub"]),
)

US_GROUP = "🇺🇸 美国节点"
AI_GROUP = "🤖 人工智能"
EXPECTED_US_FILTER = r"(?i)美国|美國|(^|[^a-z])(us|usa|united ?states)([^a-z]|$)"
EXPECTED_AI_PROXIES = [
    "🇺🇸 美国节点",
    "🇯🇵 日本节点",
    "🇸🇬 狮城节点",
    "🇪🇺 欧洲节点",
    "🌐 故障转移",
]
EXPECTED_US_DIRECT_CONSUMERS = [
    "🌏 全球加速",
    "🎬 流媒体",
    "🌐 社交平台",
    "🤖 人工智能",
    "🎮 游戏平台",
    "👨‍💻 开发服务",
    "🎙️ 商务协作",
    "🛒 电商支付",
    "🍎 苹果服务",
    "🌌 谷歌服务",
    "Ⓜ️ 微软服务",
    "🐟 兜底分流",
]
EXPECTED_GROUP_ORDER = [
    "🚀 手动切换",
    "🌏 全球加速",
    "🎬 流媒体",
    "📺 哔哩哔哩",
    "🌐 社交平台",
    "🤖 人工智能",
    "🎮 游戏平台",
    "👨‍💻 开发服务",
    "💰 加密货币",
    "🎙️ 商务协作",
    "🛒 电商支付",
    "🍎 苹果服务",
    "🌌 谷歌服务",
    "Ⓜ️ 微软服务",
    "🇨🇳 国内服务",
    "📡 网络测速",
    "🛡️ 安全防护",
    "🐟 兜底分流",
    "♻️ 自动选择",
    "🌐 故障转移",
    "🇭🇰 香港节点",
    "🇨🇳 台湾节点",
    "🇯🇵 日本节点",
    "🇰🇷 韩国节点",
    "🇺🇸 美国节点",
    "🇸🇬 狮城节点",
    "🌏 东南亚节点",
    "🌏 亚洲其他节点",
    "🇪🇺 欧洲节点",
    "🌎 美洲节点",
    "🏝️ 大洋洲节点",
    "🌍 非洲节点",
]


def assert_us_failclosed_contract(data: dict, expected_use: list[str], label: str) -> None:
    groups = data.get("proxy-groups")
    if not isinstance(groups, list):
        raise AssertionError(f"{label}: missing proxy-groups list")

    names = [g.get("name") for g in groups if isinstance(g, dict)]
    if names != EXPECTED_GROUP_ORDER:
        raise AssertionError(f"{label}: proxy-group names/order changed: {names}")

    by_name = {g["name"]: g for g in groups}

    # 1. US group fail-closed contract
    us = by_name[US_GROUP]
    if us.get("type") != "select":
        raise AssertionError(f"{label}: {US_GROUP} type must stay 'select', got {us.get('type')!r}")
    if us.get("proxies") != ["REJECT"]:
        raise AssertionError(
            f"{label}: {US_GROUP} must set static proxies=['REJECT'] only, got {us.get('proxies')!r}"
        )
    if us.get("use") != expected_use:
        raise AssertionError(f"{label}: {US_GROUP} use mismatch: {us.get('use')!r} != {expected_use!r}")
    if us.get("filter") != EXPECTED_US_FILTER:
        raise AssertionError(f"{label}: {US_GROUP} filter regex changed")

    # 2. Consumer boundary check (exact 12 direct, 0 indirect, 0 rules referencing US directly)
    direct = [g["name"] for g in groups if US_GROUP in (g.get("proxies") or [])]
    if direct != EXPECTED_US_DIRECT_CONSUMERS:
        raise AssertionError(f"{label}: {US_GROUP} direct consumers changed: {direct}")
    indirect = [
        g["name"]
        for g in groups
        if g["name"] not in direct and any(p in direct for p in (g.get("proxies") or []))
    ]
    if indirect:
        raise AssertionError(f"{label}: unexpected indirect {US_GROUP} consumers: {indirect}")
    rules = data.get("rules") or []
    us_rules = [r for r in rules if isinstance(r, str) and US_GROUP in r]
    if us_rules:
        raise AssertionError(f"{label}: unexpected rule directly referencing {US_GROUP}: {us_rules}")

    # 3. Third-item-unchanged guard: AI group options and AI rule order remain intact
    ai = by_name[AI_GROUP]
    if ai.get("type") != "select" or ai.get("proxies") != EXPECTED_AI_PROXIES:
        raise AssertionError(f"{label}: {AI_GROUP} options/order must stay unchanged: {ai}")
    if "use" in ai:
        raise AssertionError(f"{label}: {AI_GROUP} must not add 'use'")

    expected_ai_rules = [
        "RULE-SET,OpenAI,🤖 人工智能,no-resolve",
        "RULE-SET,Anthropic,🤖 人工智能,no-resolve",
        "RULE-SET,GitHubCopilot,🤖 人工智能,no-resolve",
        "RULE-SET,Gemini,🤖 人工智能,no-resolve",
        "RULE-SET,AIExtra,🤖 人工智能,no-resolve",
    ]
    actual_ai_rules = [r for r in rules if isinstance(r, str) and AI_GROUP in r]
    if actual_ai_rules != expected_ai_rules:
        raise AssertionError(f"{label}: AI rules changed: {actual_ai_rules}")

    adguard_idx = rules.index("RULE-SET,AdGuard,🛡️ 安全防护,no-resolve")
    anthropic_idx = rules.index("RULE-SET,Anthropic,🤖 人工智能,no-resolve")
    if anthropic_idx < adguard_idx:
        raise AssertionError(f"{label}: Anthropic rule must not be moved ahead of AdGuard")


def assert_mutation_rejected(data: dict, expected_use: list[str], name: str, mutate) -> None:
    mutated = copy.deepcopy(data)
    mutate(mutated)
    try:
        assert_us_failclosed_contract(mutated, expected_use, f"mutation:{name}")
    except AssertionError:
        return
    raise AssertionError(f"negative mutation {name!r} was not caught")


def main() -> int:
    for rel_path, expected_use in MIHOMO_CONFIGS:
        data = yaml.safe_load((ROOT / rel_path).read_text(encoding="utf-8"))
        assert_us_failclosed_contract(data, expected_use, rel_path)

    # Negative mutation tests against the source config (T03)
    source = yaml.safe_load((ROOT / "mihomo/verge/config.yaml").read_text(encoding="utf-8"))

    def remove_reject(cfg: dict) -> None:
        next(g for g in cfg["proxy-groups"] if g["name"] == US_GROUP).pop("proxies")

    def add_direct(cfg: dict) -> None:
        next(g for g in cfg["proxy-groups"] if g["name"] == US_GROUP)["proxies"] = ["REJECT", "DIRECT"]

    def replace_with_direct(cfg: dict) -> None:
        next(g for g in cfg["proxy-groups"] if g["name"] == US_GROUP)["proxies"] = ["DIRECT"]

    def narrow_ai_group(cfg: dict) -> None:
        next(g for g in cfg["proxy-groups"] if g["name"] == AI_GROUP)["proxies"] = [US_GROUP]

    def move_anthropic_front(cfg: dict) -> None:
        r = cfg["rules"]
        r.remove("RULE-SET,Anthropic,🤖 人工智能,no-resolve")
        r.insert(0, "RULE-SET,Anthropic,🤖 人工智能,no-resolve")

    for mut_name, fn in (
        ("remove-us-reject", remove_reject),
        ("add-direct-to-us", add_direct),
        ("replace-reject-with-direct", replace_with_direct),
        ("narrow-ai-group", narrow_ai_group),
        ("move-anthropic-front", move_anthropic_front),
    ):
        assert_mutation_rejected(source, ["Sub", "Sub2"], mut_name, fn)

    print(
        "US group fail-closed contract passed: 4 Mihomo configs, 12 direct consumers, "
        "unchanged AI group/rules and 5 negative mutations."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
