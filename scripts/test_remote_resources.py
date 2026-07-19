#!/usr/bin/env python3
"""Offline tests for remote resource extraction and response validation."""
from __future__ import annotations

import check_remote_resources as check


def main() -> int:
    resources = check.extract_resources()
    urls = [resource.url for resource in resources]
    assert len(urls) == len(set(urls)), "extracted resource URLs must be unique"
    assert len(resources) >= 150, "unexpectedly few remote resources extracted"
    icon_count = sum(resource.kind == "icon" for resource in resources)
    assert icon_count == 36
    assert all(not check.is_skipped_url(url) for url in urls)
    assert any(resource.kind == "shellcrash-template" for resource in resources)
    assert any(resource.source.startswith("quantumultx/") for resource in resources)

    private_url = "https://provider.example/resource?credential=fixture-value#fragment"
    redacted = check.redact_url(private_url)
    assert "fixture-value" not in redacted and "fragment" not in redacted
    assert "<redacted>" in redacted

    icon = check.Resource("https://assets.example/icon.png", "icon", "test")
    assert check.validate_body(icon, "image/png", b"PNG", "light") is None
    assert check.validate_body(icon, "text/html", b"<html>", "light") is not None

    yaml_rule = check.Resource(
        "https://rules.example/list.yaml", "clash-rule", "test", expected_format="yaml"
    )
    assert check.validate_body(yaml_rule, "text/plain", b"payload:\n  - DOMAIN,example.com\n", "full") is None
    assert check.validate_body(yaml_rule, "text/plain", b"rules: []\n", "full") is not None
    assert check.validate_body(yaml_rule, "text/html", b"<!doctype html>", "light") is not None

    print(
        f"Remote resource offline tests passed "
        f"({len(resources)} resources, {icon_count} icons)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
