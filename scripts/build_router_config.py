#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
SOURCE_PATH = ROOT / "clash" / "config.yaml"
OUTPUT_PATH = ROOT / "clash" / "config-router.template.yaml"

# ShellCrash keeps these sections when its default configuration override is enabled.
POLICY_SECTIONS = (
    "proxies",
    "proxy-providers",
    "proxy-groups",
    "rule-providers",
    "sub-rules",
    "rules",
    "listeners",
)

# These valid example URLs keep the public template acceptable to `mihomo -t`.
# The router deployment script replaces the full values with device-local secrets.
PROVIDER_PLACEHOLDERS = {
    "Sub": "https://example.com/__SUB_URL_1__",
    "Sub2": "https://example.com/__SUB_URL_2__",
}


class QuotedString(str):
    """String that must remain quoted in generated YAML."""


class NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data: object) -> bool:
        return True


def represent_quoted_string(dumper: yaml.SafeDumper, value: QuotedString):
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style='"')


NoAliasDumper.add_representer(QuotedString, represent_quoted_string)


def load_source() -> dict[str, object]:
    data = yaml.safe_load(SOURCE_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{SOURCE_PATH} must contain a mapping at top level")
    return data


def validate_references(config: dict[str, object]) -> None:
    proxies = config.get("proxies", [])
    providers = config.get("proxy-providers")
    groups = config.get("proxy-groups")
    rule_providers = config.get("rule-providers")
    rules = config.get("rules")

    if not isinstance(providers, dict) or not providers:
        raise ValueError("router template requires a non-empty 'proxy-providers' mapping")
    if not isinstance(groups, list) or not groups:
        raise ValueError("router template requires a non-empty 'proxy-groups' list")
    if not isinstance(rule_providers, dict) or not rule_providers:
        raise ValueError("router template requires a non-empty 'rule-providers' mapping")
    if not isinstance(rules, list) or not rules:
        raise ValueError("router template requires a non-empty 'rules' list")

    missing_placeholders = sorted(set(PROVIDER_PLACEHOLDERS) - set(providers))
    if missing_placeholders:
        raise ValueError(
            "missing proxy providers required for local injection: "
            + ", ".join(missing_placeholders)
        )

    group_names: set[str] = set()
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("name"), str):
            raise ValueError("every proxy group must be a mapping with a string 'name'")
        name = group["name"]
        if name in group_names:
            raise ValueError(f"duplicate proxy group name: {name}")
        group_names.add(name)

    if not isinstance(proxies, list):
        raise ValueError("router template field 'proxies' must be a list when present")
    proxy_names: set[str] = set()
    for proxy in proxies:
        if not isinstance(proxy, dict) or not isinstance(proxy.get("name"), str):
            raise ValueError("every static proxy must be a mapping with a string 'name'")
        proxy_names.add(proxy["name"])

    provider_names = set(providers)
    builtin_targets = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE"}
    known_targets = group_names | proxy_names | builtin_targets
    for group in groups:
        name = str(group["name"])
        use = group.get("use", [])
        if not isinstance(use, list) or not all(isinstance(item, str) for item in use):
            raise ValueError(f"proxy group {name!r} field 'use' must be a list of strings")
        missing = sorted(set(use) - provider_names)
        if missing:
            raise ValueError(
                f"proxy group {name!r} references missing proxy providers: {', '.join(missing)}"
            )
        targets = group.get("proxies", [])
        if not isinstance(targets, list) or not all(isinstance(item, str) for item in targets):
            raise ValueError(f"proxy group {name!r} field 'proxies' must be a list of strings")
        missing_targets = sorted(set(targets) - known_targets)
        if missing_targets:
            raise ValueError(
                f"proxy group {name!r} references missing proxies or groups: "
                + ", ".join(missing_targets)
            )

    rule_provider_names = set(rule_providers)
    known_policies = group_names | builtin_targets
    for index, rule in enumerate(rules, start=1):
        if not isinstance(rule, str):
            raise ValueError(f"rule {index} must be a string")
        fields = [field.strip() for field in rule.split(",")]
        if fields[0] == "RULE-SET":
            if len(fields) < 3:
                raise ValueError(f"rule {index} is an incomplete RULE-SET rule: {rule}")
            if fields[1] not in rule_provider_names:
                raise ValueError(
                    f"rule {index} references missing rule provider {fields[1]!r}"
                )
            policy = fields[2]
        elif fields[0] == "MATCH":
            if len(fields) < 2:
                raise ValueError(f"rule {index} is an incomplete MATCH rule: {rule}")
            policy = fields[1]
        elif len(fields) >= 3:
            policy = fields[2]
        else:
            continue
        if policy not in known_policies:
            raise ValueError(f"rule {index} references missing policy {policy!r}")


def build_router_config() -> str:
    source = load_source()
    output: dict[str, object] = {}
    for section in POLICY_SECTIONS:
        if section in source:
            output[section] = copy.deepcopy(source[section])

    providers = output.get("proxy-providers")
    if not isinstance(providers, dict):
        raise ValueError("source config must contain a 'proxy-providers' mapping")
    for name, placeholder in PROVIDER_PLACEHOLDERS.items():
        provider = providers.get(name)
        if not isinstance(provider, dict):
            raise ValueError(f"proxy provider {name!r} must be a mapping")
        provider["url"] = QuotedString(placeholder)

    validate_references(output)

    rendered = yaml.dump(
        output,
        Dumper=NoAliasDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=120,
    )
    if re.search(r"(?m)^\s*(?:<<:|[*&][A-Za-z0-9_-]+)", rendered):
        raise ValueError("generated router template still contains YAML anchors or aliases")

    header = (
        "# Generated by scripts/build_router_config.py. Do not edit by hand.\n"
        "# Source of truth: clash/config.yaml\n"
        "# ShellCrash owns ports, DNS, TUN, sniffer, controller and other runtime settings.\n"
        "# Keep ShellCrash configuration override enabled when using this policy template.\n\n"
    )
    return header + rendered


def check_output(expected: str) -> int:
    actual = OUTPUT_PATH.read_text(encoding="utf-8") if OUTPUT_PATH.exists() else None
    if actual == expected:
        return 0
    print(f"Generated file is out of date: {OUTPUT_PATH.relative_to(ROOT)}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the public ShellCrash router policy template."
    )
    parser.add_argument("--check", action="store_true", help="verify the generated file")
    args = parser.parse_args()

    try:
        rendered = build_router_config()
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"Router config build failed: {exc}", file=sys.stderr)
        return 1

    if args.check:
        return check_output(rendered)

    current = OUTPUT_PATH.read_text(encoding="utf-8") if OUTPUT_PATH.exists() else None
    if current == rendered:
        print(f"Generated file is already up to date: {OUTPUT_PATH.relative_to(ROOT)}")
        return 0
    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    print(f"Updated generated file: {OUTPUT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
