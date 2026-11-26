#!/usr/bin/env python3
"""Offline finance/wallet domain-routing contracts using generated lists and production rule order.

External lists are independent overlap fixtures, not a client or DNS emulator.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
HK = "🇭🇰 香港节点"
MOBILE_HK = HK + " · 自动"
CRYPTO = "💰 加密货币"
MOBILE_CRYPTO = CRYPTO + " · 自动"

DIRECT_CASES = (
    "wise.com",
    "api.wise.com",
    "loki-test.wise.com",
    "transferwise.com",
    "octopus.com.hk",
    "www.octopus.com.hk",
    "octopuscards.com",
    "rbwm-api.hsbc.com.hk",
    "www.hsbc.com.hk",
    "eapi.preferences.prod.ap-east-1.vam-hk.cloud1.vv1865.com",
    "messaging.prod.ap-east-1.vam-hk.cloud1.vv1865.com",
    "hsbc.edge.sdk.awswaf.com",
    "cdnhsbcrhostorage.azureedge.net",
)
HK_CASES = (
    "redhotoffers.hsbc.com.hk",
    "www.redhotoffers.hsbc.com.hk",
    "interactivebrokers.com",
)
CRYPTO_CASES = (
    "safepal.com",
    "api.safepal.com",
    "safepal.io",
)
EXACT_CONTROLS = (
    "sub.hsbc.edge.sdk.awswaf.com",
    "other.edge.sdk.awswaf.com",
    "sub.cdnhsbcrhostorage.azureedge.net",
    "other.azureedge.net",
)
SPOOF_CONTROLS = (
    "evil-wise.com",
    "evil-octopus.com.hk",
    "evil-vv1865.com",
    "evil-safepal.com",
    "evil-safepal.io",
)
# Deliberately broad overlaps expose a moved/missing local rule-set reference.
OVERLAPS = {
    "Amazon": ["DOMAIN-SUFFIX,awswaf.com"],
    "Microsoft": ["DOMAIN-SUFFIX,azureedge.net"],
    "Crypto": ["DOMAIN-SUFFIX,tradingview.com"],
    "ProxyLite": [
        "DOMAIN-SUFFIX,wise.com",
        "DOMAIN-SUFFIX,transferwise.com",
        "DOMAIN-SUFFIX,hsbc.com.hk",
        "DOMAIN-SUFFIX,vv1865.com",
        "DOMAIN-SUFFIX,safepal.com",
    ],
}
EXPECTED_REJECT_ALLOW = {
    "DOMAIN-SUFFIX,wise.com",
    "DOMAIN-SUFFIX,transferwise.com",
    "DOMAIN-SUFFIX,octopus.com.hk",
    "DOMAIN-SUFFIX,octopuscards.com",
    "DOMAIN-SUFFIX,vv1865.com",
    "DOMAIN,hsbc.edge.sdk.awswaf.com",
    "DOMAIN,cdnhsbcrhostorage.azureedge.net",
    "DOMAIN-SUFFIX,redhotoffers.hsbc.com.hk",
}


def read_rules(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".yaml":
        return yaml.safe_load(text)["payload"]
    return [s.strip() for s in text.splitlines() if s.strip() and not s.startswith(("#", ";"))]


def match(rule: str, host: str) -> bool:
    fields = [s.strip() for s in rule.split(",")]
    if len(fields) < 2:
        return False
    kind, value = fields[:2]
    upper = kind.upper()
    if upper in {"DOMAIN", "HOST"}:
        return host == value
    if upper in {"DOMAIN-SUFFIX", "HOST-SUFFIX"}:
        return host == value or host.endswith("." + value)
    if upper in {"DOMAIN-KEYWORD", "HOST-KEYWORD"}:
        return value in host
    return False


def first_policy(records: list[tuple[str, str]], host: str) -> str | None:
    return next((policy for rule, policy in records if match(rule, host)), None)


def repo_or_overlap(url: str, client: str) -> list[str]:
    local = re.search(rf"/{client}/rules/([a-z_]+\.list)$", url)
    if local:
        return read_rules(ROOT / client / "rules" / local[1])
    service = re.search(r"/rule/(?:Loon|Surge|QuantumultX)/([^/]+)/", url)
    return OVERLAPS.get(service[1], []) if service else []


def section(path: Path, name: str) -> list[str]:
    active = False
    result = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s.startswith("["):
            active = s == name
        elif active and s and not s.startswith(("#", ";")):
            result.append(s)
    return result


def mihomo_records(path: Path) -> list[tuple[str, str]]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    records = []
    for line in config["rules"]:
        fields = line.split(",")
        if fields[0] == "RULE-SET":
            name, policy = fields[1:3]
            spec = config["rule-providers"][name]
            local = re.search(r"/mihomo/rules/([a-z_]+\.yaml)$", spec["url"])
            entries = read_rules(ROOT / "mihomo/rules" / local[1]) if local else OVERLAPS.get(name, [])
            records.extend((entry, policy) for entry in entries)
        elif len(fields) > 2:
            records.append((line, fields[2]))
    return records


def mobile_records(client: str) -> list[tuple[str, str]]:
    records = []
    if client == "qx":
        path = ROOT / "quantumultx/bootstrap.example.conf"
        for line in section(path, "[filter_local]"):
            fields = line.split(",")
            if len(fields) > 2:
                records.append((line, fields[2].strip()))
        for line in section(path, "[filter_remote]"):
            url, *args = [s.strip() for s in line.split(",")]
            params = dict(arg.split("=", 1) for arg in args if "=" in arg)
            if params.get("enabled", "true") == "false":
                continue
            if url.endswith("/quantumultx/filter/repo.snippet"):
                for rule in read_rules(ROOT / "quantumultx/filter/repo.snippet"):
                    records.append((rule, rule.rsplit(",", 1)[1].strip()))
            else:
                records.extend((rule, params["force-policy"]) for rule in repo_or_overlap(url, "quantumultx"))
        return records
    if client == "loon":
        for line in section(ROOT / "loon/bootstrap.example.conf", "[Rule]"):
            fields = line.split(",")
            if len(fields) > 2:
                records.append((line, fields[2].strip()))
        for line in section(ROOT / "loon/bootstrap.example.conf", "[Remote Rule]"):
            url, *args = [s.strip() for s in line.split(",")]
            params = dict(arg.split("=", 1) for arg in args if "=" in arg)
            if params.get("enabled", "true") != "false":
                records.extend((rule, params["policy"]) for rule in repo_or_overlap(url, "loon"))
        return records
    for line in section(ROOT / "surge/proxy-config.conf", "[Rule]"):
        fields = line.split(",")
        if fields[0] == "RULE-SET":
            records.extend((rule, fields[2]) for rule in repo_or_overlap(fields[1], "surge"))
        elif len(fields) > 2:
            records.append((line, fields[2]))
    return records


def main() -> None:
    configs = (
        "mihomo/verge/config.yaml",
        "mihomo/verge/config-single.yaml",
        "mihomo/shellcrash/config-router.template.yaml",
        "mihomo/shellcrash/config-router-single.template.yaml",
    )
    for name in (*configs, "qx", "loon", "surge"):
        records = mihomo_records(ROOT / name) if name in configs else mobile_records(name)
        expected_hk = MOBILE_HK if name in {"qx", "loon"} else HK
        expected_crypto = MOBILE_CRYPTO if name in {"qx", "loon"} else CRYPTO
        for host in DIRECT_CASES:
            assert first_policy(records, host) == "DIRECT", (name, host, first_policy(records, host))
        for host in HK_CASES:
            assert first_policy(records, host) == expected_hk, (name, host, first_policy(records, host))
        for host in CRYPTO_CASES:
            assert first_policy(records, host) == expected_crypto, (name, host, first_policy(records, host))
        for host in EXACT_CONTROLS:
            assert first_policy(records, host) != "DIRECT", (name, host, first_policy(records, host))
        for host in SPOOF_CONTROLS:
            assert first_policy(records, host) not in {"DIRECT", expected_hk, expected_crypto}, (
                name,
                host,
                first_policy(records, host),
            )
        # Priority mutation: moving real IntlBrokers rules behind HkBanks must misroute redhotoffers to DIRECT.
        non_hk = [(rule, policy) for rule, policy in records if policy != expected_hk]
        hk_only = [(rule, policy) for rule, policy in records if policy == expected_hk]
        assert hk_only, name
        assert first_policy(non_hk + hk_only, "www.redhotoffers.hsbc.com.hk") == "DIRECT", name

    for path in (
        "mihomo/rules/hk_banks.yaml",
        "loon/rules/hk_banks.list",
        "surge/rules/hk_banks.list",
        "mihomo/rules/intl_brokers.yaml",
        "loon/rules/intl_brokers.list",
        "surge/rules/intl_brokers.list",
    ):
        entries = read_rules(ROOT / path)
        for host in EXACT_CONTROLS + SPOOF_CONTROLS:
            assert not any(match(rule, host) for rule in entries), (path, host)

    reject_allow = set(read_rules(ROOT / "surge/rules/reject_allow.list"))
    missing_allow = EXPECTED_REJECT_ALLOW - reject_allow
    assert not missing_allow, f"surge/rules/reject_allow.list missing {sorted(missing_allow)}"
    assert "DOMAIN-SUFFIX,safepal.com" not in reject_allow
    assert "DOMAIN-SUFFIX,safepal.io" not in reject_allow

    print(
        f"Finance routing contracts passed: {len(DIRECT_CASES)} direct, {len(HK_CASES)} HK, "
        f"{len(CRYPTO_CASES)} crypto, {len(EXACT_CONTROLS)} exact-host and {len(SPOOF_CONTROLS)} "
        "spoof controls across seven production configs."
    )


if __name__ == "__main__":
    main()
