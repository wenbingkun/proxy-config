#!/usr/bin/env python3
"""Acceptance Audit checker.

Checks architecture compliance, security, and documentation completeness.

Exit code 1 if any check fails.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

PLACEHOLDER_PATTERNS = re.compile(
    r"replace[-_]me|replace[-_]with|example\.com|\.test(?:[/:?]|$)|your[-_]token|placeholder",
    re.IGNORECASE,
)
STRICT_PLACEHOLDER_RE = re.compile(
    r"^(?:"
    r"<[^<>\s]+>"
    r"|\$(?:[0-9]+|[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})"
    r"|\{[^}\r\n]*\}"
    r"|%(?:\([A-Za-z_][A-Za-z0-9_]*\))?[sd]"
    r"|replace[-_]me(?:[-_](?:[0-9]+|one|two|one-changed))?"
    r"|replace[-_]with[-_]random[-_]key"
    r"|your[-_](?:token|key|secret)"
    r"|placeholder"
    r"|changeme"
    r")$",
    re.IGNORECASE,
)
TOKEN_QUERY_KEY = "token"
# Construct the PEM marker dynamically so this script does not trigger its own check.
PEM_PRIVATE_KEY_RE = re.compile(r"-----" + r"BEGIN [A-Z0-9 ]*PRIVATE KEY" + r"-----")
BEARER_TOKEN_RE = re.compile(
    r"(?:"
    r"['\"]?Authorization['\"]?[ \t]*[:=][ \t]*['\"]?Bearer[ \t]+([^\s\"'`<>}{$,;]+)"
    r"|"
    r"\bBearer[ \t]+((?=[A-Za-z0-9._~+/=-]{12,})(?=\S*[0-9._~+/=-])[^\s\"'`<>}{$,;]+)"
    r")",
    re.IGNORECASE,
)
LOCAL_SECRET_ASSIGN_RE = re.compile(
    r"(?m)^[ \t]*(?:export[ \t]+)?(ROUTER_SECRET|SURGE_KEY)[ \t]*=(?:"
    r"[ \t]*\"([^\"\r\n]+)\""
    r"|[ \t]*'([^'\r\n]+)'"
    r"|([^\s'\"][^\s'\"]*)"
    r"|[ \t]+([^\s#'\"][^\s'\"]*)"
    r")"
)
SURGE_HTTP_API_RE = re.compile(r"(?m)^[ \t]*http-api[ \t]*=[ \t]*([^@\s]+)@")
EXTRA_QUERY_SECRET_RE = re.compile(r"[?&](key|secret|auth)=([^\s&\"'`]+)", re.IGNORECASE)
FORBIDDEN_TRACKED_FILES = (
    "quantumultx/bootstrap.conf",
    "loon/bootstrap.conf",
    "surge/bootstrap.conf",
    "mihomo/shellcrash/providers.env",
    "surge/Airport.conf",
    "surge/airport-dns.sgmodule",
    "surge/netdiag-api.sgmodule",
)


def is_template_placeholder(value: str) -> bool:
    return bool(STRICT_PLACEHOLDER_RE.fullmatch(value))


def fail(msg: str, failures: list[str]) -> None:
    failures.append(msg)


def check_architecture(failures: list[str]) -> None:
    """Check that the three-layer architecture is in place."""

    # mihomo/verge/config.yaml has rule-providers with at least one entry
    clash_config = ROOT / "mihomo" / "verge" / "config.yaml"
    if not clash_config.exists():
        fail("mihomo/verge/config.yaml not found", failures)
    else:
        try:
            data = yaml.safe_load(clash_config.read_text(encoding="utf-8")) or {}
            providers = data.get("rule-providers")
            if not isinstance(providers, dict) or len(providers) == 0:
                fail("mihomo/verge/config.yaml: 'rule-providers' missing or empty", failures)
            elif not {"OpenAI", "Anthropic", "GitHubCopilot", "Gemini", "AIExtra"} <= set(providers):
                fail("mihomo/verge/config.yaml: granular AI providers are incomplete", failures)
            elif {"AI", "Tracking", "Epic", "PlayStation", "Xbox", "Nintendo", "BattleNet"} & set(
                providers
            ):
                fail("mihomo/verge/config.yaml: contains a superseded duplicate rule provider", failures)
            proxy_providers = data.get("proxy-providers")
            if not isinstance(proxy_providers, dict) or set(proxy_providers) != {"Sub", "Sub2"}:
                fail("mihomo/verge/config.yaml: expected dual proxy providers Sub and Sub2", failures)
        except yaml.YAMLError as exc:
            fail(f"mihomo/verge/config.yaml: YAML parse error: {exc}", failures)

    single_config = ROOT / "mihomo" / "verge" / "config-single.yaml"
    if not single_config.exists():
        fail("mihomo/verge/config-single.yaml not found", failures)
    else:
        try:
            raw = single_config.read_text(encoding="utf-8")
            data = yaml.safe_load(raw) or {}
            proxy_providers = data.get("proxy-providers")
            if not isinstance(proxy_providers, dict) or set(proxy_providers) != {"Sub"}:
                fail("mihomo/verge/config-single.yaml: expected only proxy provider Sub", failures)
            if "Sub2" in raw:
                fail("mihomo/verge/config-single.yaml: contains residual Sub2 reference", failures)
        except yaml.YAMLError as exc:
            fail(f"mihomo/verge/config-single.yaml: YAML parse error: {exc}", failures)

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

    # Local secret files must not be tracked by git
    for local_secret in FORBIDDEN_TRACKED_FILES:
        result = subprocess.run(
            ["git", "ls-files", local_secret],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            fail(f"git ls-files failed for {local_secret}", failures)
        if result.stdout.strip():
            fail(
                f"{local_secret} is tracked by git — "
                "it contains secrets and must be gitignored",
                failures,
            )

    # Any tracked file matching .gitignore (e.g. *.p12, *.pem, *.key, *.dconf, *.local.env) must fail.
    ignored_tracked = subprocess.run(
        ["git", "ls-files", "-ci", "-z", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
    )
    if ignored_tracked.returncode != 0:
        fail("git ls-files -ci failed; ignored-file tracking check incomplete", failures)
    else:
        for raw in ignored_tracked.stdout.split(b"\0"):
            if raw:
                tracked_rel = os.fsdecode(raw)
                fail(
                    f"{tracked_rel} is tracked by git despite matching .gitignore — "
                    "remove it from tracking",
                    failures,
                )

    # Scan tracked files and untracked candidates that are not gitignored.
    # -z gives raw NUL-separated paths; without it Git quotes and escapes
    # non-ASCII or special names, which would then be skipped as missing files.
    candidate_result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
    )
    if candidate_result.returncode != 0:
        fail("git ls-files failed; credential scan incomplete", failures)
        return
    candidates = [os.fsdecode(raw) for raw in candidate_result.stdout.split(b"\0") if raw]

    # Scan executable/config formats where machine-local secrets are most likely.
    SCAN_SUFFIXES = {".conf", ".yaml", ".yml", ".ini", ".txt", ".py", ".sh", ".dconf", ".sgmodule", ".md", ".js", ".json", ".snippet", ".plugin", ".list"}

    for rel_path in candidates:
        path = ROOT / rel_path
        if not path.is_file():
            continue
        is_env_example = path.name.endswith((".env", ".env.example"))
        if path.suffix.lower() not in SCAN_SUFFIXES and not is_env_example:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            fail(f"{rel_path}: unable to read credential scan input", failures)
            continue

        # passphrase (QX) / ca-passphrase (Loon, Surge) with a real value — use [^\S\n]* to avoid
        # crossing line boundaries
        for match in re.finditer(r"(?m)^(?:ca-)?passphrase[^\S\n]*=[^\S\n]*(.+)$", text):
            value = match.group(1).strip()
            if value and not value.startswith("#"):
                fail(
                    f"{rel_path}: 'passphrase' has a non-empty value — "
                    "MitM passphrase must not be committed",
                    failures,
                )

        # p12 (QX) / ca-p12 (Loon, Surge) with a real value
        for match in re.finditer(r"(?m)^(?:ca-)?p12[^\S\n]*=[^\S\n]*(.+)$", text):
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
                line = text.count("\n", 0, match.start()) + 1
                fail(
                    f"{rel_path}:{line}: possible real subscription token: "
                    f"{TOKEN_QUERY_KEY}=<redacted> — "
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

        # PEM private key material must never be committed.
        for match in PEM_PRIVATE_KEY_RE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            fail(
                f"{rel_path}:{line}: PEM private key header detected — "
                "private keys must not be committed",
                failures,
            )

        # Bearer credentials must remain template expressions or obvious placeholders.
        for match in BEARER_TOKEN_RE.finditer(text):
            bearer_value = match.group(1) or match.group(2)
            if not is_template_placeholder(bearer_value):
                line = text.count("\n", 0, match.start()) + 1
                fail(
                    f"{rel_path}:{line}: possible real Bearer token: Bearer <redacted> — "
                    "use a placeholder instead",
                    failures,
                )

        # Device-local API secret variables and Surge http-api keys.
        for match in LOCAL_SECRET_ASSIGN_RE.finditer(text):
            var_name = match.group(1)
            secret_value = match.group(2) or match.group(3) or match.group(4) or match.group(5) or ""
            if secret_value and not is_template_placeholder(secret_value):
                line = text.count("\n", 0, match.start()) + 1
                fail(
                    f"{rel_path}:{line}: possible real secret in {var_name} — "
                    "use a placeholder instead",
                    failures,
                )
        for match in SURGE_HTTP_API_RE.finditer(text):
            api_key = match.group(1)
            if not is_template_placeholder(api_key):
                line = text.count("\n", 0, match.start()) + 1
                fail(
                    f"{rel_path}:{line}: possible real key in http-api — "
                    "use a placeholder instead",
                    failures,
                )

        # Query-string credentials for key/secret/auth (token= is checked above).
        for match in EXTRA_QUERY_SECRET_RE.finditer(text):
            param_name, param_value = match.group(1), match.group(2)
            if not is_template_placeholder(param_value):
                line = text.count("\n", 0, match.start()) + 1
                fail(
                    f"{rel_path}:{line}: possible real query credential: "
                    f"{param_name}=<redacted> — use a placeholder instead",
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


def check_policy_references(failures: list[str]) -> None:
    clash_data = yaml.safe_load((ROOT / "mihomo" / "verge" / "config.yaml").read_text(encoding="utf-8")) or {}
    clash_groups = {
        group.get("name")
        for group in clash_data.get("proxy-groups", [])
        if isinstance(group, dict) and isinstance(group.get("name"), str)
    }

    qx_groups: set[str] = set()
    in_policy = False
    for raw_line in (ROOT / "quantumultx" / "bootstrap.example.conf").read_text(
        encoding="utf-8"
    ).splitlines():
        line = raw_line.strip()
        if line == "[policy]":
            in_policy = True
            continue
        if line.startswith("["):
            in_policy = False
        if not in_policy or not line or line.startswith("#") or "=" not in line:
            continue
        value = line.split("=", 1)[1]
        qx_groups.add(value.split(",", 1)[0].strip())

    manifest = yaml.safe_load((ROOT / "rules" / "local_rules.yaml").read_text(encoding="utf-8")) or {}
    builtin = {"DIRECT", "REJECT"}
    for item in manifest.get("rule_sets", []):
        if not isinstance(item, dict):
            continue
        rule_id = item.get("id", "<unknown>")
        clash_policy = item.get("clash_policy")
        if clash_policy not in clash_groups | builtin:
            fail(
                f"rules/local_rules.yaml: {rule_id} references missing Clash policy {clash_policy!r}",
                failures,
            )
        qx_policy = item.get("qx_policy")
        if qx_policy not in qx_groups | builtin:
            fail(
                f"rules/local_rules.yaml: {rule_id} references missing QX policy {qx_policy!r}",
                failures,
            )


def check_windows_runtime_defaults(failures: list[str]) -> None:
    data = yaml.safe_load((ROOT / "mihomo" / "verge" / "config.yaml").read_text(encoding="utf-8")) or {}
    dns = data.get("dns", {})
    tun = data.get("tun", {})
    expected = {
        "allow-lan": False,
        "external-controller": "127.0.0.1:9090",
        "unified-delay": True,
        "ipv6": True,
    }
    for key, value in expected.items():
        if data.get(key) != value:
            fail(f"mihomo/verge/config.yaml: expected {key}={value!r}", failures)
    if "bind-address" in data:
        fail("mihomo/verge/config.yaml: bind-address must not be explicitly exposed", failures)
    if not isinstance(dns, dict) or dns.get("enable") is not True:
        fail("mihomo/verge/config.yaml: DNS must be enabled", failures)
    elif dns.get("enhanced-mode") != "fake-ip" or dns.get("ipv6") is not True:
        fail("mihomo/verge/config.yaml: expected fake-ip DNS with IPv6 enabled", failures)
    if not isinstance(tun, dict) or tun.get("enable") is not False:
        fail("mihomo/verge/config.yaml: TUN must remain disabled by default", failures)


def qx_section_lines(section: str) -> list[str]:
    lines: list[str] = []
    in_section = False
    text = (ROOT / "quantumultx" / "bootstrap.example.conf").read_text(encoding="utf-8")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("["):
            in_section = line == f"[{section}]"
            continue
        if in_section and line and not line.startswith(("#", ";")):
            lines.append(line)
    return lines


def check_qx_runtime_defaults(failures: list[str]) -> None:
    if "no-ipv6" in qx_section_lines("dns"):
        fail(
            "quantumultx/bootstrap.example.conf: [dns] must not set no-ipv6 "
            "(IPv6 is enabled on all clients)",
            failures,
        )
    general = qx_section_lines("general")
    if "udp_whitelist = 1-442, 444-65535" not in general or "udp_drop_list = 443" not in general:
        fail(
            "quantumultx/bootstrap.example.conf: [general] must keep UDP 443 (QUIC) out of the "
            "tunnel so MitM rewrites apply (udp_whitelist = 1-442, 444-65535; udp_drop_list = 443)",
            failures,
        )
    if "fallback_udp_policy = reject" not in general:
        fail(
            "quantumultx/bootstrap.example.conf: [general] fallback_udp_policy must be reject "
            "so UDP never goes direct when a node lacks UDP relay",
            failures,
        )


def main() -> int:
    failures: list[str] = []

    print("=== Architecture Compliance ===")
    check_architecture(failures)

    print("=== Security Scan ===")
    check_security(failures)

    print("=== Documentation Completeness ===")
    check_documentation(failures)

    print("=== Policy References ===")
    check_policy_references(failures)

    print("=== Windows Runtime Defaults ===")
    check_windows_runtime_defaults(failures)

    print("=== Quantumult X Runtime Defaults ===")
    check_qx_runtime_defaults(failures)

    if failures:
        print("\nAUDIT FAILED:")
        for f in failures:
            print(f"  [FAIL] {f}")
        return 1

    print("\nAcceptance audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
