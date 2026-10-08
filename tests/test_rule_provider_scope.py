#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_rules import HOME_AUTO_SUFFIX, home_auto  # noqa: E402
CONFIG_PATHS = (
    ROOT / "mihomo" / "verge" / "config.yaml",
    ROOT / "mihomo" / "verge" / "config-single.yaml",
    ROOT / "mihomo" / "shellcrash" / "config-router.template.yaml",
    ROOT / "mihomo" / "shellcrash" / "config-router-single.template.yaml",
)

DOMAIN_ONLY_PROVIDERS = {
    "DAZN": (
        "MetaCubeX/meta-rules-dat@meta/geo/geosite/dazn.yaml",
        "./cache/rulesets/DAZN_Domain.yaml",
    ),
    "Cloudflare": (
        "MetaCubeX/meta-rules-dat@meta/geo/geosite/cloudflare.yaml",
        "./cache/rulesets/Cloudflare_Domain.yaml",
    ),
    "Amazon": (
        "MetaCubeX/meta-rules-dat@meta/geo/geosite/amazon.yaml",
        "./cache/rulesets/Amazon_Domain.yaml",
    ),
}

REQUIRED_LOCAL_DOMAINS = {
    ROOT / "rules" / "ai_extra.yaml": {
        "immersivetranslate.com",
        "jetbrains.ai",
        "grazie.ai",
        "grazie.aws.intellij.net",
        "openrouter.ai",
        "cohere.com",
        "ollama.com",
        "cursor.sh",
        "civitai.com",
        "lmarena.ai",
        "meta.ai",
        "metaaivm.com",
        "muse.ai",
    },
    ROOT / "rules" / "social_media.yaml": {
        "redditspace.com",
        "imgur.com",
        "imgur.io",
        "imgurinc.com",
    },
    ROOT / "rules" / "dev_extra.yaml": {
        "ldstatic.com",
        "v2ex.co",
        "v2ex.pro",
        "gradle.org",
        "eclipse.org",
        "helm.sh",
        "bun.sh",
        "cmake.org",
        "llvm.org",
        "supabase.com",
        "render.com",
        "railway.app",
    },
    ROOT / "rules" / "collaboration_extra.yaml": {
        "miro.com",
        "asana.com",
        "monday.com",
        "airtable.com",
        "clickup.com",
        "loom.com",
        "calendly.com",
        "box.com",
    },
    ROOT / "rules" / "ecommerce_extra.yaml": {"revolut.com"},
    ROOT / "rules" / "speedtest.yaml": {"librespeed.org"},
    ROOT / "rules" / "microsoft_extra.yaml": {
        "img-s-msn-com.akamaized.net",
        "msftstatic.com",
    },
    # Apple Intelligence / Private Cloud Compute, including the suffixes folded in from
    # ddgksf2013's AppleIntelligence.list, which no client loads any more.
    ROOT / "rules" / "apple_extra.yaml": {
        "apple-relay.cloudflare.com",
        "apple-relay.fastly-edge.com",
        "apple-relay.akamaized.net",
        "cp4.cloudflare.com",
        "gateway.icloud.com",
        "apple-relay.apple.com",
        "guzzoni.apple.com",
        "gspe1-ssl.ls.apple.com",
        "smoot.apple.com",
        "apple-relay.mask.apple-dns.net",
        "api-siri-prod.apple.com",
    },
    ROOT / "rules" / "game_extra.yaml": {
        "just-dance.com",
        "justdancenow.com",
    },
}


QX_CONFIG = ROOT / "quantumultx" / "bootstrap.example.conf"

# blackmatrix7 lists that exist on both clients under the same name; QX must
# route them to the same policy as the router template.
SHARED_SERVICES = (
    "YouTube",
    "Netflix",
    "Disney",
    "TikTok",
    "Discord",
    "Bahamut",
    "AppleTV",
    "Pinterest",
    "Developer",
)

# (earlier, later) pairs whose relative order decides overlapping domains.
# Each side is (QX list name, Clash provider name).
SHARED_ORDER = (
    (("YouTube", "YouTube"), ("Bahamut", "Bahamut")),
    (("Bahamut", "Bahamut"), ("Apple", "Apple")),
    (("Netflix", "Netflix"), ("Developer", "Developer")),
    (("Apple", "Apple"), ("Developer", "Developer")),
    (("Google", "Google"), ("Developer", "Developer")),
    (("Microsoft", "Microsoft"), ("Developer", "Developer")),
    (("Developer", "Developer"), ("Amazon", "Amazon")),
    (("Developer", "Developer"), ("China", "ChinaDirect")),
)


def load_yaml(path: Path) -> dict[str, object]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    assert isinstance(data, dict), f"{path.relative_to(ROOT)} must contain a mapping"
    return data


def assert_domain_only_providers(path: Path) -> None:
    config = load_yaml(path)
    providers = config.get("rule-providers")
    assert isinstance(providers, dict), f"{path.relative_to(ROOT)} has no rule-providers"

    for name, (expected_url_suffix, expected_path) in DOMAIN_ONLY_PROVIDERS.items():
        provider = providers.get(name)
        assert isinstance(provider, dict), f"{path.relative_to(ROOT)} is missing {name}"
        assert provider.get("behavior") == "domain", (
            f"{path.relative_to(ROOT)}:{name} must not match shared CDN IP ranges"
        )
        url = provider.get("url")
        assert isinstance(url, str) and url.endswith(expected_url_suffix), (
            f"{path.relative_to(ROOT)}:{name} uses an unexpected source"
        )
        assert provider.get("path") == expected_path, (
            f"{path.relative_to(ROOT)}:{name} must not reuse its former classical cache"
        )

    assert "GlobalMedia" not in providers, (
        f"{path.relative_to(ROOT)} must not reintroduce the GlobalMedia provider"
    )

    rules = config.get("rules")
    assert isinstance(rules, list), f"{path.relative_to(ROOT)} has no rules"
    rules = [strip_no_resolve(rule) for rule in rules]
    assert not any("GlobalMedia" in rule for rule in rules), (
        f"{path.relative_to(ROOT)} must not reference GlobalMedia rules"
    )
    assert "RULE-SET,AIExtra,🤖 人工智能" in rules, f"{path.relative_to(ROOT)} is missing AIExtra"
    assert "RULE-SET,MicrosoftExtra,Ⓜ️ 微软服务" in rules, (
        f"{path.relative_to(ROOT)} is missing MicrosoftExtra"
    )
    assert "RULE-SET,AppleExtra,🍎 苹果服务" in rules, (
        f"{path.relative_to(ROOT)} is missing AppleExtra"
    )
    rule_sets = [rule.split(",")[1] for rule in rules if rule.startswith("RULE-SET,")]
    for later in ("Cloudflare", "ProxyLite"):
        assert rule_sets.index("AppleExtra") < rule_sets.index(later), (
            f"{path.relative_to(ROOT)}: AppleExtra must precede {later}"
        )
    assert "EcommerceExtra" in providers, (
        f"{path.relative_to(ROOT)} is missing EcommerceExtra"
    )
    assert "RULE-SET,EcommerceExtra,🛒 电商支付" in rules, (
        f"{path.relative_to(ROOT)} is missing EcommerceExtra routing"
    )


def assert_local_domain_coverage() -> None:
    for path, expected in REQUIRED_LOCAL_DOMAINS.items():
        source = load_yaml(path)
        suffixes = source.get("domain_suffix")
        assert isinstance(suffixes, list), f"{path.relative_to(ROOT)} has no domain_suffix list"
        missing = expected - set(suffixes)
        assert not missing, f"{path.relative_to(ROOT)} is missing {sorted(missing)}"

    stack_overflow = load_yaml(ROOT / "rules" / "stack_overflow.yaml")
    assert "stack.imgur.com" not in stack_overflow.get("domain", []), (
        "stack.imgur.com must be covered by the shared Imgur social-media suffix rule"
    )


def load_qx_remote_filters() -> list[tuple[str, str]]:
    """Return (list name, group) for enabled blackmatrix7 QX lists, in file order.

    The group is the force-policy without its "· 自动" home-Wi-Fi wrapper, as Clash names it."""
    filters: list[tuple[str, str]] = []
    in_section = False
    for raw_line in QX_CONFIG.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("["):
            in_section = line == "[filter_remote]"
            continue
        if not in_section or not line or line.startswith(("#", ";")):
            continue
        params = dict(
            part.strip().split("=", 1) for part in line.split(",")[1:] if "=" in part
        )
        # QX loads a remote resource unless it is explicitly disabled.
        if params.get("enabled", "true").strip().lower() == "false":
            continue
        # A "#..." fragment carries resource-parser options (Cloudflare drops its IP rules that way).
        match = re.search(r"/rule/QuantumultX/([^/]+)/\1\.list(?:#.*)?$", line.split(",")[0])
        if match and "force-policy" in params:
            filters.append(
                (match.group(1), params["force-policy"].strip().removesuffix(HOME_AUTO_SUFFIX))
            )
    return filters


# Folded into rules/apple_extra.yaml; the shared rule set replaces the remote list on every client.
APPLE_INTELLIGENCE_LIST = "ddgksf2013/Filter/refs/heads/master/AppleIntelligence.list"
CLIENT_TEMPLATES = (
    ROOT / "quantumultx" / "bootstrap.example.conf",
    ROOT / "loon" / "bootstrap.example.conf",
    ROOT / "surge" / "proxy-config.conf",
)


def load_qx_enabled_filters() -> list[tuple[str, dict[str, str]]]:
    """Return (url, params) for every enabled QX [filter_remote] resource, in file order."""
    entries: list[tuple[str, dict[str, str]]] = []
    in_section = False
    for raw_line in QX_CONFIG.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("["):
            in_section = line == "[filter_remote]"
            continue
        if not in_section or not line or line.startswith(("#", ";")):
            continue
        parts = [part.strip() for part in line.split(",")]
        params = dict(part.split("=", 1) for part in parts[1:] if "=" in part)
        params = {key.strip(): value.strip() for key, value in params.items()}
        if params.get("enabled", "true").lower() == "false":
            continue
        entries.append((parts[0], params))
    return entries


def clash_rule_sets(path: Path) -> list[tuple[str, str]]:
    rules = load_yaml(path).get("rules")
    assert isinstance(rules, list), f"{path.relative_to(ROOT)} has no rules"
    return [
        (parts[1], parts[2])
        for parts in (strip_no_resolve(rule).split(",") for rule in rules)
        if parts[0] == "RULE-SET"
    ]


def assert_shared_service_policies() -> None:
    qx = load_qx_remote_filters()
    qx_policy = dict(qx)
    qx_order = [name for name, _ in qx]
    for name in SHARED_SERVICES:
        assert qx_order.count(name) == 1, f"QX must load {name} exactly once"

    for path in CONFIG_PATHS:
        clash = clash_rule_sets(path)
        clash_policy = dict(clash)
        clash_order = [name for name, _ in clash]
        where = path.relative_to(ROOT)
        for name in SHARED_SERVICES:
            assert clash_policy.get(name) == qx_policy[name], (
                f"{name}: {where} routes to {clash_policy.get(name)!r}, "
                f"QX routes to {qx_policy[name]!r}"
            )
        for (qx_first, clash_first), (qx_second, clash_second) in SHARED_ORDER:
            assert clash_order.index(clash_first) < clash_order.index(clash_second), (
                f"{where}: {clash_first} must precede {clash_second}"
            )
            assert qx_order.index(qx_first) < qx_order.index(qx_second), (
                f"QX: {qx_first} must precede {qx_second} to match {where}"
            )

    assert qx_policy["AppleTV"] == "🍎 苹果服务", "Apple TV must follow the Apple services group"

    assert qx_policy["Gemini"] == "🤖 人工智能", "Gemini must stay in the AI group"

    for path in CLIENT_TEMPLATES:
        assert APPLE_INTELLIGENCE_LIST not in path.read_text(encoding="utf-8"), (
            f"{path.relative_to(ROOT)} must not load {APPLE_INTELLIGENCE_LIST}: "
            "its suffixes live in rules/apple_extra.yaml"
        )


def assert_qx_cloudflare_domain_only() -> None:
    """QX has no no-resolve and matches IP rules after resolving any host no host rule matched, so the
    bm7 Cloudflare list's IP ranges would pull every unlisted Cloudflare-hosted site into the dev
    group. Mihomo uses a domain-only Cloudflare set; QX drops the IP rules through the resource parser."""
    entries = [(url, params) for url, params in load_qx_enabled_filters()
               if "/rule/QuantumultX/Cloudflare/Cloudflare.list" in url]
    assert len(entries) == 1, "QX must load the Cloudflare list exactly once"
    url, params = entries[0]
    assert url.endswith("Cloudflare.list#out=IP-CIDR+IP6-CIDR+IP-ASN&ntf=0"), f"QX Cloudflare must drop IP rules: {url}"
    assert params.get("opt-parser") == "true", "QX Cloudflare needs opt-parser=true for #out to apply"


def strip_no_resolve(rule: str) -> str:
    return rule.removesuffix(",no-resolve")


# Every rule set that held IP rules on 2026-10-04 (provider snapshot), in rule order. Their IP rules are
# the fallback for domains nothing else matched: private addresses stay DIRECT, ad and hijack addresses
# stay blocked, Chinese cloud addresses stay domestic. Dropping one changes routing, so it needs review.
REQUIRED_IP_STAGE = ("LocalNetwork", "Lan", "AdGuard", "Hijacking", "WhatsApp", "Line", "YouTube", "BiliBili",
                     "Telegram", "Twitter", "Facebook", "Spotify", "Twitch", "Apple", "Google", "WeChat",
                     "Alibaba", "Tencent", "ChinaCompany", "Game")
REPO_RULES_URLS = (  # where the configs load the repo's own Mihomo rule files from
    "https://cdn.jsdelivr.net/gh/wenbingkun/proxy-config@main/mihomo/rules/",
    "https://raw.githubusercontent.com/wenbingkun/proxy-config/main/mihomo/rules/",
)


def assert_two_stage_rules(path: Path) -> None:
    """Mihomo resolves a domain at the first IP rule without no-resolve. Domain stage: every rule set
    carries no-resolve, so a domain rule always decides before any lookup. IP stage: rule sets that
    also hold IP rules are referenced again without no-resolve, in their domain-stage order, then
    GEOIP,CN and MATCH. Only the timing of the lookup changes, never which IP rules exist."""
    where = path.relative_to(ROOT)
    rules = load_yaml(path)["rules"]
    assert rules[-2:] == ["GEOIP,CN,🇨🇳 国内服务", "MATCH,🐟 兜底分流"], f"{where}: must end with GEOIP,CN and MATCH"
    split = next((i for i, r in enumerate(rules[:-2]) if r.startswith("RULE-SET,") and not r.endswith(",no-resolve")),
                 len(rules) - 2)
    front, tail = rules[:split], rules[split:-2]
    for rule in front:
        kind = rule.split(",")[0]
        assert kind in ("RULE-SET", "PROCESS-NAME", "DOMAIN-SUFFIX"), f"{where}: unexpected rule type in the domain stage: {rule}"
        if kind == "DOMAIN-SUFFIX":
            assert rule in {
                "DOMAIN-SUFFIX,pancakeswap.finance,💰 加密货币",
                "DOMAIN-SUFFIX,marketplace.visualstudio.com,👨‍💻 开发服务",
                "DOMAIN-SUFFIX,vscode.dev,👨‍💻 开发服务",
                "DOMAIN-SUFFIX,vsassets.io,👨‍💻 开发服务",
                "DOMAIN-SUFFIX,fast.com,📡 网络测速",
            }, f"{where}: unreviewed domain exception: {rule}"
        if kind == "RULE-SET":
            assert rule.endswith(",no-resolve"), f"{where}: domain-stage rule set needs no-resolve: {rule}"
    stage = [strip_no_resolve(r) for r in front if r.startswith("RULE-SET,")]
    for rule in tail:
        assert rule.startswith("RULE-SET,") and not rule.endswith(",no-resolve"), f"{where}: bad IP-stage rule: {rule}"
        assert rule in stage, f"{where}: IP-stage rule must repeat a domain-stage rule set and policy: {rule}"
    order = [stage.index(rule) for rule in tail]
    assert order == sorted(order) and len(set(tail)) == len(tail), f"{where}: IP stage must keep domain-stage order"
    names = {rule.split(",")[1] for rule in tail}
    providers = load_yaml(path)["rule-providers"]
    # Repo-maintained lists are checked offline: one that holds IP rules must be in the IP stage too.
    repo_lists = 0
    for name, provider in providers.items():
        url = str(provider.get("url", ""))
        prefix = next((p for p in REPO_RULES_URLS if url.startswith(p)), None)
        if prefix is None:
            continue
        repo_lists += 1
        payload = load_yaml(ROOT / "mihomo" / "rules" / url[len(prefix):]).get("payload") or []
        if any(str(x).split(",")[0] in ("IP-CIDR", "IP-CIDR6", "IP-ASN") for x in payload) and name not in names:
            raise AssertionError(f"{where}: {name} holds IP rules, so it must also be in the IP stage")
    assert repo_lists, f"{where}: no repo rule provider matched REPO_RULES_URLS; the check above would be dead"
    missing = [name for name in REQUIRED_IP_STAGE if name not in names]
    assert not missing, f"{where}: IP stage is missing {missing}"


def main() -> int:
    for path in CONFIG_PATHS:
        assert_domain_only_providers(path)
        assert_two_stage_rules(path)
    assert_local_domain_coverage()
    assert_shared_service_policies()
    assert_qx_cloudflare_domain_only()
    print("Rule provider scope tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
