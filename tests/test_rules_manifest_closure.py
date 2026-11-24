#!/usr/bin/env python3
"""Negative and positive regression tests for rules manifest closure (R6)."""
from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import build_rules  # noqa: E402
import check_hygiene  # noqa: E402
import test_rule_provider_scope as scope  # noqa: E402


def test_load_manifest_rejects_duplicate_and_invalid_ids() -> None:
    base_item = {
        "id": "sample_one",
        "title": "Sample One",
        "source": "sample_one.yaml",
        "clash_policy": "DIRECT",
        "qx_policy": "DIRECT",
    }
    cases = (
        ("duplicate id", [base_item, {**base_item, "source": "sample_two.yaml"}], "duplicate rule set id"),
        ("duplicate source", [base_item, {**base_item, "id": "sample_two"}], "duplicate rule set source"),
        ("path traversal id", [{**base_item, "id": "../escape"}], "invalid rule set id"),
        ("uppercase id", [{**base_item, "id": "SampleOne"}], "invalid rule set id"),
        ("empty id", [{**base_item, "id": ""}], "invalid rule set id"),
        ("non-string id", [{**base_item, "id": 123}], "must be a string"),
        ("reserved geoip_cn", [{**base_item, "id": "geoip_cn"}], "reserved rule set id"),
        ("reserved reject_allow", [{**base_item, "id": "reject_allow"}], "reserved rule set id"),
    )
    with tempfile.TemporaryDirectory() as tmp:
        manifest_path = Path(tmp) / "local_rules.yaml"
        for name, rule_sets, expected_msg in cases:
            manifest_path.write_text(yaml.safe_dump({"rule_sets": rule_sets}), encoding="utf-8")
            with patch.object(build_rules, "LOCAL_RULES_MANIFEST", manifest_path):
                try:
                    build_rules.load_manifest()
                except ValueError as exc:
                    assert expected_msg in str(exc), (name, exc)
                else:
                    raise AssertionError(f"{name}: expected ValueError")
        # Non-mapping top-level YAML (e.g. a list) must raise ValueError, not AttributeError.
        manifest_path.write_text("- not-a-mapping\n", encoding="utf-8")
        with patch.object(build_rules, "LOCAL_RULES_MANIFEST", manifest_path):
            try:
                build_rules.load_manifest()
            except ValueError as exc:
                assert "top-level mapping" in str(exc), exc
            else:
                raise AssertionError("list root: expected ValueError")

    # Duplicate target path registration must raise ValueError before overwriting.
    outputs: dict[Path, str] = {}
    target = Path("/tmp/collision.list")
    build_rules.register_output(outputs, target, "first")
    try:
        build_rules.register_output(outputs, target, "second")
    except ValueError as exc:
        assert "duplicate generated output path" in str(exc), exc
    else:
        raise AssertionError("expected duplicate output path failure")


def test_stale_generated_outputs_and_allowlist() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        clash_dir = base / "mihomo" / "rules"
        loon_dir = base / "loon" / "rules"
        surge_dir = base / "surge" / "rules"
        for d in (clash_dir, loon_dir, surge_dir):
            d.mkdir(parents=True)

        expected_clash = clash_dir / "demo.yaml"
        expected_loon = loon_dir / "demo.list"
        expected_surge = surge_dir / "demo.list"
        outputs = {
            expected_clash: "payload:\n  - DOMAIN,example.com\n",
            expected_loon: "DOMAIN,example.com\n",
            expected_surge: "DOMAIN,example.com\n",
        }
        for path, body in outputs.items():
            path.write_text(body, encoding="utf-8")
        # Allowlisted hand-maintained file and dotfiles/non-matching suffixes are ignored.
        (loon_dir / "geoip_cn.list").write_text("GEOIP,CN\n", encoding="utf-8")
        (loon_dir / ".DS_Store").write_text("ignored", encoding="utf-8")
        (clash_dir / "notes.txt").write_text("ignored", encoding="utf-8")

        gen_dirs = (
            (clash_dir, ".yaml", frozenset()),
            (loon_dir, ".list", frozenset({"geoip_cn.list"})),
            (surge_dir, ".list", frozenset()),
        )
        with patch.object(build_rules, "ROOT", base), patch.object(build_rules, "GENERATED_DIRS", gen_dirs):
            assert build_rules.check_outputs(outputs) == 0

            for stale_path in (
                clash_dir / "orphan.yaml",
                loon_dir / "orphan.list",
                surge_dir / "orphan.list",
            ):
                stale_path.write_text("# stale\n", encoding="utf-8")
                assert build_rules.check_outputs(outputs) == 1, stale_path
                stale_path.unlink()
            assert build_rules.check_outputs(outputs) == 0


def test_check_hygiene_rejects_unregistered_and_missing_sources() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        rules_dir = base / "rules"
        mihomo_dir = base / "mihomo"
        rules_dir.mkdir()
        mihomo_dir.mkdir()
        (rules_dir / "demo.yaml").write_text("domain:\n  - demo.example\n", encoding="utf-8")
        manifest_path = rules_dir / "local_rules.yaml"
        manifest_path.write_text(
            yaml.safe_dump(
                {
                    "rule_sets": [
                        {
                            "id": "demo",
                            "title": "Demo",
                            "source": "demo.yaml",
                            "clash_policy": "DIRECT",
                            "qx_policy": "DIRECT",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        with patch.object(check_hygiene, "ROOT", base), \
                patch.object(check_hygiene, "RULES_DIR", rules_dir), \
                patch.object(build_rules, "RULES_DIR", rules_dir), \
                patch.object(build_rules, "LOCAL_RULES_MANIFEST", manifest_path):
            assert check_hygiene.main() == 0

            # Unregistered rule source in rules/ must fail hygiene.
            orphan = rules_dir / "unregistered.yaml"
            orphan.write_text("domain:\n  - orphan.example\n", encoding="utf-8")
            assert check_hygiene.main() == 1
            orphan.unlink()
            assert check_hygiene.main() == 0

            # Manifest entry pointing to a missing source file must fail hygiene cleanly.
            manifest_path.write_text(
                yaml.safe_dump(
                    {
                        "rule_sets": [
                            {
                                "id": "demo",
                                "title": "Demo",
                                "source": "demo.yaml",
                                "clash_policy": "DIRECT",
                                "qx_policy": "DIRECT",
                            },
                            {
                                "id": "ghost",
                                "title": "Ghost",
                                "source": "ghost.yaml",
                                "clash_policy": "DIRECT",
                                "qx_policy": "DIRECT",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            assert check_hygiene.main() == 1

            # Invalid manifest (duplicate id) must be caught as ERROR rather than crashing.
            manifest_path.write_text(
                yaml.safe_dump(
                    {
                        "rule_sets": [
                            {
                                "id": "demo",
                                "title": "Demo",
                                "source": "demo.yaml",
                                "clash_policy": "DIRECT",
                                "qx_policy": "DIRECT",
                            },
                            {
                                "id": "demo",
                                "title": "Dup",
                                "source": "dup.yaml",
                                "clash_policy": "DIRECT",
                                "qx_policy": "DIRECT",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            assert check_hygiene.main() == 1


def test_manifest_mihomo_parity_negative_cases() -> None:
    manifest = build_rules.load_manifest()
    base_config = scope.load_yaml(ROOT / "mihomo" / "verge" / "config.yaml")
    scope.assert_manifest_mihomo_parity(base_config, manifest, "baseline")

    # 1. Policy mismatch between manifest clash_policy and Mihomo RULE-SET.
    bad_manifest = copy.deepcopy(manifest)
    bad_manifest[0]["clash_policy"] = "REJECT"
    try:
        scope.assert_manifest_mihomo_parity(base_config, bad_manifest, "policy-mismatch")
    except AssertionError as exc:
        assert "routed to" in str(exc), exc
    else:
        raise AssertionError("expected policy mismatch failure")

    # 2. Manifest entry not mounted in rule-providers.
    extra_manifest = copy.deepcopy(manifest) + [
        {
            "id": "unmounted_set",
            "title": "Unmounted",
            "source": "unmounted_set.yaml",
            "clash_policy": "DIRECT",
            "qx_policy": "DIRECT",
        }
    ]
    try:
        scope.assert_manifest_mihomo_parity(base_config, extra_manifest, "missing-provider")
    except AssertionError as exc:
        assert "unmounted_set" in str(exc), exc
    else:
        raise AssertionError("expected missing provider failure")

    # 3. Provider mounted but missing RULE-SET in rules.
    missing_rule = copy.deepcopy(base_config)
    missing_rule["rules"] = [r for r in missing_rule["rules"] if not str(r).startswith("RULE-SET,Speedtest,")]
    try:
        scope.assert_manifest_mihomo_parity(missing_rule, manifest, "missing-rule")
    except AssertionError as exc:
        assert "speedtest" in str(exc), exc
    else:
        raise AssertionError("expected missing rule reference failure")

    # 4. Non-standard repo URL (e.g. pinned SHA) pointing to /mihomo/rules/ cannot escape check.
    escaped_url = copy.deepcopy(base_config)
    escaped_url["rule-providers"]["Speedtest"]["url"] = (
        "https://cdn.jsdelivr.net/gh/wenbingkun/proxy-config@0123456789abcdef0123456789abcdef01234567/mihomo/rules/speedtest.yaml"
    )
    try:
        scope.assert_manifest_mihomo_parity(escaped_url, manifest, "non-standard-url")
    except AssertionError as exc:
        assert "non-standard URL" in str(exc), exc
    else:
        raise AssertionError("expected non-standard repo URL failure")

    # 5. Compound rule referencing a repo provider (with or without space after comma) is rejected,
    # while a distinct provider sharing a prefix (SpeedtestOther) is not falsely rejected.
    for rule_text in (
        "AND,((RULE-SET,Speedtest),(DOMAIN,example.com)),DIRECT",
        "AND,((RULE-SET, Speedtest),(DOMAIN,example.com)),DIRECT",
    ):
        compound = copy.deepcopy(base_config)
        compound["rules"].insert(0, rule_text)
        try:
            scope.assert_manifest_mihomo_parity(compound, manifest, "compound-rule")
        except AssertionError as exc:
            assert "compound rule" in str(exc), exc
        else:
            raise AssertionError(f"expected compound rule failure for {rule_text!r}")

    benign_compound = copy.deepcopy(base_config)
    benign_compound["rules"].insert(0, "AND,((RULE-SET,SpeedtestOther),(DOMAIN,example.com)),DIRECT")
    scope.assert_manifest_mihomo_parity(benign_compound, manifest, "compound-other-provider")


def main() -> int:
    test_load_manifest_rejects_duplicate_and_invalid_ids()
    test_stale_generated_outputs_and_allowlist()
    test_check_hygiene_rejects_unregistered_and_missing_sources()
    test_manifest_mihomo_parity_negative_cases()
    print("Rules manifest closure tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
