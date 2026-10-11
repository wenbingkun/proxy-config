#!/usr/bin/env python3
"""Rules Hygiene checker.

Checks:
  - No duplicate entries within the same field in a single rule file (ERROR)
  - No duplicate entries across different rule files for the same field (WARNING)
  - Files end with a newline (ERROR)
  - Only allowed field keys are used (ERROR)
  - No repeated key within one YAML mapping in rules/ or mihomo/ (ERROR);
    safe_load keeps only the last value, so later checks never see the first
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import yaml

from build_rules import RULE_METADATA_FILES

ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = ROOT / "rules"

ALLOWED_KEYS = {
    "domain_suffix",
    "domain",
    "domain_keyword",
    "domain_regex",
    "ip_cidr",
    "ip_cidr6",
}

def find_duplicate_keys(text: str) -> list[tuple[str, int]]:
    """Return (key, line) for each key repeated within the same mapping node.

    Works on the composed node graph, so a merge (``<<: *anchor``) followed by an
    explicit override is not a repeat; aliased nodes are visited once.
    """
    problems: list[tuple[str, int]] = []
    visited: set[int] = set()
    stack = [yaml.compose(text)]
    while stack:
        node = stack.pop()
        if node is None or id(node) in visited:
            continue
        visited.add(id(node))
        if isinstance(node, yaml.MappingNode):
            seen: set[tuple[str, str]] = set()
            for key, value in node.value:
                if isinstance(key, yaml.ScalarNode):
                    ident = (key.tag, key.value)
                    if ident in seen:
                        problems.append((key.value, key.start_mark.line + 1))
                    seen.add(ident)
                stack.extend((key, value))
        elif isinstance(node, yaml.SequenceNode):
            stack.extend(node.value)
    return sorted(problems, key=lambda item: item[1])


def check_duplicate_keys(paths: list[Path], errors: list[str]) -> None:
    for path in paths:
        rel = path.relative_to(ROOT)
        try:
            problems = find_duplicate_keys(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            errors.append(f"{rel}: YAML parse error: {exc}")
            continue
        for key, line in problems:
            errors.append(f"{rel}:{line}: duplicate key '{key}' in the same mapping")


def check_file(path: Path, errors: list[str], warnings: list[str]) -> dict[str, list[str]]:
    """Check a single rule file. Returns the parsed data for cross-file checks."""
    rel = path.relative_to(ROOT)

    # Check trailing newline
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        errors.append(f"{rel}: missing trailing newline")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        errors.append(f"{rel}: top-level must be a mapping")
        return {}

    # Check for unknown keys
    unknown = sorted(set(data) - ALLOWED_KEYS)
    for key in unknown:
        errors.append(f"{rel}: unknown field '{key}' (allowed: {', '.join(sorted(ALLOWED_KEYS))})")

    # Check for intra-file duplicates
    for key, values in data.items():
        if not isinstance(values, list):
            continue
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                continue
            lower = value.lower()
            if lower in seen:
                errors.append(f"{rel}: duplicate entry in '{key}': {value!r}")
            seen.add(lower)

    return {k: [v.lower() for v in vs if isinstance(v, str)]
            for k, vs in data.items() if isinstance(vs, list) and k in ALLOWED_KEYS}


def check_cross_file(
    file_data: dict[str, dict[str, list[str]]],
    warnings: list[str],
) -> None:
    """Detect the same entry appearing in multiple rule files."""
    # Map (field, value) -> list of files that contain it
    entry_locations: dict[tuple[str, str], list[str]] = defaultdict(list)
    for filename, data in file_data.items():
        for field, values in data.items():
            for value in values:
                entry_locations[(field, value)].append(filename)

    for (field, value), files in sorted(entry_locations.items()):
        if len(files) > 1:
            warnings.append(
                f"duplicate across files in '{field}': {value!r} "
                f"appears in {', '.join(sorted(files))}"
            )


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    rule_files = sorted(
        p for p in RULES_DIR.glob("*.yaml") if p.name not in RULE_METADATA_FILES
    )

    if not rule_files:
        errors.append(f"no rule files found in {RULES_DIR.relative_to(ROOT)}")

    file_data: dict[str, dict[str, list[str]]] = {}
    for path in rule_files:
        data = check_file(path, errors, warnings)
        file_data[path.name] = data

    check_cross_file(file_data, warnings)

    yaml_files = sorted(RULES_DIR.glob("*.yaml")) + sorted((ROOT / "mihomo").rglob("*.yaml"))
    check_duplicate_keys(yaml_files, errors)

    if warnings:
        print("WARNINGS (will not block merge):")
        for w in warnings:
            print(f"  [warn] {w}")

    if errors:
        print("ERRORS:")
        for e in errors:
            print(f"  [error] {e}")
        return 1

    print(
        f"Rules hygiene OK ({len(rule_files)} files checked; "
        f"{len(yaml_files)} YAML files checked for duplicate keys)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
