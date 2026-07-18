#!/usr/bin/env python3
"""Acceptance Audit checker.

Checks architecture compliance, security, and documentation completeness.

Exit code 1 if any check fails.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

PLACEHOLDER_PATTERNS = re.compile(
    r"replace[-_]me|example\.com|\.test(?:[/:?]|$)|your[-_]token|placeholder",
    re.IGNORECASE,
)
TOKEN_QUERY_KEY = "token"


def fail(msg: str, failures: list[str]) -> None:
    failures.append(msg)


def check_architecture(failures: list[str]) -> None:
    """Check that the three-layer architecture is in place."""

    # clash/config.yaml has rule-providers with at least one entry
    clash_config = ROOT / "clash" / "config.yaml"
    if not clash_config.exists():
        fail("clash/config.yaml not found", failures)
    else:
        try:
            data = yaml.safe_load(clash_config.read_text(encoding="utf-8")) or {}
            providers = data.get("rule-providers")
            if not isinstance(providers, dict) or len(providers) == 0:
                fail("clash/config.yaml: 'rule-providers' missing or empty", failures)
            proxy_providers = data.get("proxy-providers")
            if not isinstance(proxy_providers, dict) or set(proxy_providers) != {"Sub", "Sub2"}:
                fail("clash/config.yaml: expected dual proxy providers Sub and Sub2", failures)
        except yaml.YAMLError as exc:
            fail(f"clash/config.yaml: YAML parse error: {exc}", failures)

    single_config = ROOT / "clash" / "config-single.yaml"
    if not single_config.exists():
        fail("clash/config-single.yaml not found", failures)
    else:
        try:
            raw = single_config.read_text(encoding="utf-8")
            data = yaml.safe_load(raw) or {}
            proxy_providers = data.get("proxy-providers")
            if not isinstance(proxy_providers, dict) or set(proxy_providers) != {"Sub"}:
                fail("clash/config-single.yaml: expected only proxy provider Sub", failures)
            if "Sub2" in raw:
                fail("clash/config-single.yaml: contains residual Sub2 reference", failures)
        except yaml.YAMLError as exc:
            fail(f"clash/config-single.yaml: YAML parse error: {exc}", failures)

    # bootstrap.example.conf has [filter_remote] and [mitm] sections
    bootstrap = ROOT / "quantumultx" / "bootstrap.example.conf"
    if not bootstrap.exists():
        fail("quantumultx/bootstrap.example.conf not found", failures)
    else:
        content = bootstrap.read_text(encoding="utf-8")
        for section in ("[filter_remote]", "[mitm]"):
            if section not in content:
                fail(f"quantumultx/bootstrap.example.conf: missing section {section}", failures)

    # rules/local_rules.yaml exists and has rule_sets list
    manifest = ROOT / "rules" / "local_rules.yaml"
    if not manifest.exists():
        fail("rules/local_rules.yaml not found", failures)
    else:
        try:
            data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
            if not isinstance(data.get("rule_sets"), list) or len(data["rule_sets"]) == 0:
                fail("rules/local_rules.yaml: 'rule_sets' missing or empty", failures)
        except yaml.YAMLError as exc:
            fail(f"rules/local_rules.yaml: YAML parse error: {exc}", failures)


def check_security(failures: list[str]) -> None:
    """Scan committed files for accidental secrets."""

    # bootstrap.conf must not be tracked by git
    result = subprocess.run(
        ["git", "ls-files", "quantumultx/bootstrap.conf"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        fail(
            "quantumultx/bootstrap.conf is tracked by git — "
            "it contains secrets and must be gitignored",
            failures,
        )

    # Scan tracked files and untracked candidates that are not gitignored.
    candidates = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout.splitlines()

    # Scan executable/config formats where machine-local secrets are most likely.
    SCAN_SUFFIXES = {".conf", ".yaml", ".yml", ".ini", ".txt", ".py", ".sh"}

    for rel_path in candidates:
        path = ROOT / rel_path
        if not path.is_file():
            continue
        is_env_example = path.name.endswith((".env", ".env.example"))
        if path.suffix.lower() not in SCAN_SUFFIXES and not is_env_example:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        # passphrase with a real value — use [^\S\n]* to avoid crossing line boundaries
        for match in re.finditer(r"(?m)^passphrase[^\S\n]*=[^\S\n]*(.+)$", text):
            value = match.group(1).strip()
            if value and not value.startswith("#"):
                fail(
                    f"{rel_path}: 'passphrase' has a non-empty value — "
                    "MitM passphrase must not be committed",
                    failures,
                )

        # p12 with a real value
        for match in re.finditer(r"(?m)^p12[^\S\n]*=[^\S\n]*(.+)$", text):
            value = match.group(1).strip()
            if value and not value.startswith("#"):
                fail(
                    f"{rel_path}: 'p12' has a non-empty value — "
                    "MitM certificate must not be committed",
                    failures,
                )

        # subscription token that looks real (not a placeholder)
        token_pattern = rf"{TOKEN_QUERY_KEY}=([^\s&\"']+)"
        for match in re.finditer(token_pattern, text):
            token_value = match.group(1)
            if not PLACEHOLDER_PATTERNS.search(token_value):
                fail(
                    f"{rel_path}: possible real subscription token: "
                    f"{TOKEN_QUERY_KEY}={token_value!r} — "
                    "use a placeholder instead",
                    failures,
                )

        # Device-local subscription variables must remain obvious placeholders.
        for match in re.finditer(r"(?m)^SUB_URL_[0-9]+\s*=\s*['\"]?([^'\"\s]+)", text):
            subscription_url = match.group(1)
            if subscription_url.startswith(("http://", "https://")) and not PLACEHOLDER_PATTERNS.search(
                subscription_url
            ):
                fail(
                    f"{rel_path}: possible real subscription URL in {match.group(0).split('=', 1)[0].strip()} — "
                    "use a placeholder instead",
                    failures,
                )


def check_documentation(failures: list[str]) -> None:
    """Check that required documentation files exist and are non-trivial."""

    readme = ROOT / "README.md"
    if not readme.exists():
        fail("README.md not found", failures)
    elif len(readme.read_text(encoding="utf-8").strip()) < 100:
        fail("README.md exists but appears to be nearly empty (< 100 chars)", failures)

    agents = ROOT / "AGENTS.md"
    if not agents.exists():
        fail("AGENTS.md not found", failures)


def main() -> int:
    failures: list[str] = []

    print("=== Architecture Compliance ===")
    check_architecture(failures)

    print("=== Security Scan ===")
    check_security(failures)

    print("=== Documentation Completeness ===")
    check_documentation(failures)

    if failures:
        print("\nAUDIT FAILED:")
        for f in failures:
            print(f"  [FAIL] {f}")
        return 1

    print("\nAcceptance audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
