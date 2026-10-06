#!/usr/bin/env python3
"""Offline checks for the isolated fixture; the real-core runner is invoked separately."""
from __future__ import annotations

import copy
import tempfile
from pathlib import Path

import integration_mihomo_rules as regression


def main() -> int:
    source = regression.load_yaml(regression.CONFIG)
    fixture = regression.load_yaml(regression.FIXTURE)
    cases = fixture["cases"]
    assert len(cases) == 10 and len({case["host"] for case in cases}) == len(cases)
    for case in cases:
        assert case["host"].endswith(".test") or case["host"] == "192.0.2.66"
        assert case["dns"] in {0, 1}
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp)
        candidate, counts = regression.build_fixture(source, fixture, directory, 10001, 10002, 10003)
        assert len(candidate["rules"]) == len(source["rules"])
        for original, isolated in zip(source["rules"], candidate["rules"], strict=True):
            parts = original.split(",")
            index = 1 if parts[0] == "MATCH" else 2
            parts[index] = "fixture:" + parts[index]
            assert ",".join(parts) == isolated, "production rule order or options changed"
        assert all(provider["type"] == "file" and "url" not in provider for provider in candidate["rule-providers"].values())
        assert "proxy-providers" not in candidate and "proxies" not in candidate
        assert all(group["proxies"] == ["REJECT"] for group in candidate["proxy-groups"])
        assert candidate["dns"]["enhanced-mode"] == "redir-host"
        assert candidate["dns"]["nameserver"] == ["udp://127.0.0.1:10003"]
        assert not candidate["sniffer"]["enable"] and not candidate["tun"]["enable"]
        assert not candidate["dns"]["use-hosts"] and not candidate["dns"]["use-system-hosts"]
        for name, provider in candidate["rule-providers"].items():
            if name not in fixture["providers"]:
                assert counts[name] == 0
                path = directory / provider["path"]
                if provider["format"] == "yaml":
                    assert regression.load_yaml(path) == {"payload": []}
                else:
                    assert path.read_text() == ""
    # A new legitimate provider stays in the production skeleton but receives no test content.
    added = copy.deepcopy(source)
    added["rule-providers"]["NewFixture"] = {"type": "http", "behavior": "domain", "format": "text", "url": "https://never-fetched.invalid/rules"}
    added["rules"].insert(3, "RULE-SET,NewFixture,DIRECT,no-resolve")
    with tempfile.TemporaryDirectory() as temp:
        candidate, counts = regression.build_fixture(added, fixture, Path(temp), 1, 2, 3)
        assert candidate["rules"][3] == "RULE-SET,NewFixture,fixture:DIRECT,no-resolve"
        assert counts["NewFixture"] == 0
    for change in ("undefined-provider", "changed-behavior", "unknown-format", "missing-match"):
        invalid = copy.deepcopy(source)
        if change == "undefined-provider":
            invalid["rules"].insert(0, "RULE-SET,Missing,DIRECT,no-resolve")
        elif change == "changed-behavior":
            invalid["rule-providers"]["OpenAI"]["behavior"] = "ipcidr"
        elif change == "unknown-format":
            invalid["rule-providers"]["Zoom"]["format"] = "mrs"
        else:
            invalid["rules"].pop()
        with tempfile.TemporaryDirectory() as temp:
            try:
                regression.build_fixture(invalid, fixture, Path(temp), 1, 2, 3)
            except ValueError:
                continue
        raise AssertionError(f"invalid input accepted: {change}")
    # Logfmt escapes Unicode (e.g. the ZWJ in Developer's policy); compare actual decoded names.
    assert regression.find_match('time=x msg="[TCP] x --> priority.test:443 match RuleSet(GitHub) using fixture:👨\\u200d💻 开发服务[REJECT]"', "priority.test") == ("RuleSet(GitHub)", "fixture:👨‍💻 开发服务")
    for variant in ("missing-lan-fallback", "early-resolution", "priority-swap"):
        mutated, host = regression.mutated(source, variant)
        assert mutated["rules"] != source["rules"] and host in {case["host"] for case in cases}
        assert source["rules"] == regression.load_yaml(regression.CONFIG)["rules"]
    print("Mihomo fixture offline checks passed: production skeleton, local-only runtime, empty new providers and invalid inputs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
