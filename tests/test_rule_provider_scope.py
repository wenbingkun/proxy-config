#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
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
    ROOT / "rules" / "apple_extra.yaml": {
        "apple-relay.cloudflare.com",
        "apple-relay.fastly-edge.com",
        "apple-relay.akamaized.net",
        "cp4.cloudflare.com",
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
    """Return (list name, force-policy) for enabled blackmatrix7 QX lists, in file order."""
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
        match = re.search(r"/rule/QuantumultX/([^/]+)/\1\.list$", line.split(",")[0])
        if match and "force-policy" in params:
            filters.append((match.group(1), params["force-policy"].strip()))
    return filters


APPLE_INTELLIGENCE_URL = (
    "https://raw.githubusercontent.com/ddgksf2013/Filter/refs/heads/master/AppleIntelligence.list"
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
        for parts in (rule.split(",") for rule in rules)
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

    enabled = load_qx_enabled_filters()
    urls = [url for url, _ in enabled]
    matches = [i for i, url in enumerate(urls) if url == APPLE_INTELLIGENCE_URL]
    assert len(matches) == 1, "QX must load the Apple Intelligence list exactly once"
    params = enabled[matches[0]][1]
    assert params.get("force-policy") == "🍎 苹果服务", (
        "Apple Intelligence must follow the Apple services group"
    )
    apple = [i for i, url in enumerate(urls) if url.endswith("/rule/QuantumultX/Apple/Apple.list")]
    assert len(apple) == 1 and matches[0] < apple[0], (
        "Apple Intelligence must load before the generic Apple list"
    )


def main() -> int:
    for path in CONFIG_PATHS:
        assert_domain_only_providers(path)
    assert_local_domain_coverage()
    assert_shared_service_policies()
    print("Rule provider scope tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
