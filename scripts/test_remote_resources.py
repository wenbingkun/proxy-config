#!/usr/bin/env python3
"""Offline tests for remote resource extraction and response validation."""
from __future__ import annotations

import email.message
import urllib.request

import check_remote_resources as check


class FakeResponse:
    def __init__(self, status: int, body: bytes, url: str) -> None:
        self.status = status
        self.headers = email.message.Message()
        self.headers["Content-Type"] = "text/plain"
        self._body = body
        self._url = url

    def read(self, amount: int) -> bytes:
        return self._body[:amount]

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_empty_range_falls_back_to_plain_get() -> None:
    """A 206 with an empty body (seen on cdn.jsdelivr.net) must not fail the check."""
    resource = check.Resource(
        "https://cdn.example/rules.txt", "qx-resource", "quantumultx/bootstrap.example.conf:1"
    )
    sent_ranges: list[str | None] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> FakeResponse:
        sent_ranges.append(request.get_header("Range"))
        if request.get_header("Range"):
            return FakeResponse(206, b"", request.full_url)
        return FakeResponse(200, b"payload:\n  - '+.ads.example'\n" * 500, request.full_url)

    original = check.urllib.request.urlopen
    check.urllib.request.urlopen = fake_urlopen
    try:
        result = check.fetch(resource, "light", timeout=1, retries=0)
    finally:
        check.urllib.request.urlopen = original
    assert sent_ranges == [f"bytes=0-{check.LIGHT_BYTES - 1}", None], sent_ranges
    assert result.ok and result.status == 200, result
    assert result.bytes_read == check.LIGHT_BYTES


def main() -> int:
    resources = check.extract_resources()
    urls = [resource.url for resource in resources]
    assert len(urls) == len(set(urls)), "extracted resource URLs must be unique"
    assert len(resources) >= 150, "unexpectedly few remote resources extracted"
    icon_count = sum(resource.kind == "icon" for resource in resources)
    assert icon_count == 33
    assert all(not check.is_skipped_url(url) for url in urls)
    assert any(resource.kind == "shellcrash-template" for resource in resources)
    assert any(resource.source.startswith("quantumultx/") for resource in resources)
    assert {
        "https://ddgksf2013.top/rewrite/StartUpAds.conf",
        "https://ddgksf2013.top/rewrite/XiaoHongShuAds.conf",
        "https://ddgksf2013.top/scripts/zhihu.ads.js",
        "https://ddgksf2013.top/scripts/bdpan.ads.js",
    } <= set(urls)

    qx_resource = check.Resource(
        "https://resources.example/rewrite.conf",
        "qx-resource",
        "quantumultx/bootstrap.example.conf:1",
    )
    clash_resource = check.Resource(
        "https://resources.example/rules.yaml",
        "clash-rule",
        "clash/config.yaml:rule-providers.Example",
    )
    assert check.user_agent_for(qx_resource) == check.QX_USER_AGENT
    assert check.user_agent_for(clash_resource) == check.USER_AGENT

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

    test_empty_range_falls_back_to_plain_get()

    print(
        f"Remote resource offline tests passed "
        f"({len(resources)} resources, {icon_count} icons)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
