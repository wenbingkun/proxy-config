#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATHS = (
    ROOT / "clash" / "config.yaml",
    ROOT / "clash" / "config-single.yaml",
    ROOT / "clash" / "config-router.template.yaml",
    ROOT / "clash" / "config-router-single.template.yaml",
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
}


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


def main() -> int:
    for path in CONFIG_PATHS:
        assert_domain_only_providers(path)
    assert_local_domain_coverage()
    print("Rule provider scope tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
