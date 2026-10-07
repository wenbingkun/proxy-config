#!/usr/bin/env python3
"""Protect narrow Mihomo policy exceptions against upstream overlap."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]

# Minimal upstream overlap fixtures, independent of production rule order and exceptions.
PROVIDERS = {
    'AdGuard': ['DOMAIN-KEYWORD,pancakeswap'],
    'Microsoft': ['DOMAIN-SUFFIX,visualstudio.com', 'DOMAIN-SUFFIX,vscode.dev', 'DOMAIN-SUFFIX,vsassets.io'],
    'Netflix': ['DOMAIN-SUFFIX,fast.com'],
    'CryptoExtra': ['DOMAIN-SUFFIX,pancakeswap.finance'],
    'DevExtra': ['DOMAIN-SUFFIX,marketplace.visualstudio.com', 'DOMAIN-SUFFIX,vscode.dev', 'DOMAIN-SUFFIX,vsassets.io'],
    'Speedtest': ['DOMAIN-SUFFIX,fast.com'],
    'HkBanks': ['DOMAIN-SUFFIX,bank.test'],
    'IntlBrokers': ['DOMAIN-SUFFIX,broker.test'],
    'LocalNetwork': ['DOMAIN-SUFFIX,local'],
}


def matches(rule, host):
    kind, value = rule.split(',')[:2]
    if kind == 'DOMAIN-SUFFIX':
        return host == value or host.endswith('.' + value)
    if kind == 'DOMAIN-KEYWORD':
        return value in host
    return False


def first_policy(rules, host):
    for rule in rules:
        parts = rule.split(',')
        if parts[0] == 'RULE-SET':
            if any(matches(r, host) for r in PROVIDERS.get(parts[1], [])):
                return parts[2]
        elif matches(rule, host):
            return parts[2]
    return None


def main():
    expected = {
        'pancakeswap.finance': '💰 加密货币', 'app.pancakeswap.finance': '💰 加密货币',
        'marketplace.visualstudio.com': '👨‍💻 开发服务', 'vscode.dev': '👨‍💻 开发服务',
        'cdn.vsassets.io': '👨‍💻 开发服务', 'fast.com': '📡 网络测速',
        'visualstudio.com': 'Ⓜ️ 微软服务', 'evil-pancakeswap.example': '🛡️ 安全防护',
        'bank.test': 'DIRECT', 'broker.test': '🇭🇰 香港节点', 'printer.local': 'DIRECT',
    }
    for file in ('mihomo/verge/config.yaml', 'mihomo/verge/config-single.yaml',
                 'mihomo/shellcrash/config-router.template.yaml', 'mihomo/shellcrash/config-router-single.template.yaml'):
        rules = yaml.safe_load((ROOT / file).read_text())['rules']
        assert {host: first_policy(rules, host) for host in expected} == expected, file
        # Removing each exception must reproduce the original wrong policy.
        for host in ('pancakeswap.finance', 'marketplace.visualstudio.com', 'vscode.dev', 'vsassets.io', 'fast.com'):
            changed = [r for r in rules if not r.startswith('DOMAIN-SUFFIX,' + host + ',')]
            assert first_policy(changed, host) != expected.get(host, '👨‍💻 开发服务'), (file, host)
    print('Mihomo policy exceptions, negative mutations and protected policies passed.')


if __name__ == '__main__':
    main()
