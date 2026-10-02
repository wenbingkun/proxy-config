#!/usr/bin/env python3
"""Offline tests for remote resource extraction and response validation."""
from __future__ import annotations

import email.message
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_remote_resources as check  # noqa: E402


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


def test_surge_reject_overlap_warns_but_bad_input_fails() -> None:
    """Overlaps with repo rules only warn; an unparsable REJECT list must fail the check."""
    bodies = {
        "conflict": b"DOMAIN-SUFFIX,ads.coingecko.com\nDOMAIN-SUFFIX,noconflict.invalid\n",
        "bad": b"IP-CIDR,not-a-cidr\n",
        "badand": b"AND,((DOMAIN,example.com)\n",
        "bank": b"DOMAIN-SUFFIX,metrics.citi.com\n",
    }
    original_read, original_urls = check.read_response, check.surge_reject_urls
    try:
        check.surge_reject_urls = lambda: [f"https://rules.example/{name}" for name in bodies]
        check.read_response = lambda url, *_args: (200, "text/plain", bodies[url.rsplit("/", 1)[1]], url)
        failures, warnings = check.reject_overlap_report(5)
    finally:
        check.read_response, check.surge_reject_urls = original_read, original_urls
    assert len(failures) == 2, failures
    assert any("not-a-cidr" in f for f in failures) and any("unbalanced" in f for f in failures), failures
    assert any("ads.coingecko.com" in w and "excluded" not in w for w in warnings), warnings
    assert any("metrics.citi.com" in w and "[excluded by reject_allow.list]" in w for w in warnings), warnings
    assert not any("noconflict.invalid" in w for w in warnings), warnings


def main() -> int:
    resources = check.extract_resources()
    urls = [resource.url for resource in resources]
    keys = [(resource.url, check.client_of(resource.source)) for resource in resources]
    assert len(keys) == len(set(keys)), "a URL must be checked once per client"
    assert len(resources) >= 150, "unexpectedly few remote resources extracted"
    icon_count = sum(resource.kind == "icon" for resource in resources)
    qx_icons = sum(r.kind == "icon" and check.client_of(r.source) == "qx" for r in resources)
    assert qx_icons == 33, qx_icons
    assert all(not check.is_skipped_url(url) for url in urls)
    assert any(resource.kind == "shellcrash-template" for resource in resources)
    assert any(resource.source.startswith("quantumultx/") for resource in resources)
    assert {
        "https://ddgksf2013.top/rewrite/StartUpAds.conf",
        "https://ddgksf2013.top/rewrite/XiaoHongShuAds.conf",
        "https://ddgksf2013.top/scripts/zhihu.ads.js",
        "https://ddgksf2013.top/scripts/bdpan.ads.js",
    } <= set(urls)

    # A URL used by both QX and Loon is checked once for each client, each with its own UA.
    shared = {r.url for r in resources if check.client_of(r.source) == "qx"} & {
        r.url for r in resources if check.client_of(r.source) == "loon"
    }
    assert shared, "expected at least one URL shared by QX and Loon (e.g. icons)"
    # The frozen Loon plugins are scanned, so the pinned scripts they load are checked too.
    loon_sources = {r.url: r.source for r in resources if check.client_of(r.source) == "loon"}
    for script in (
        "https://raw.githubusercontent.com/ddgksf2013/Scripts/819a88e0efbfeb5dfdb15e93c6d007a5e790a15f/amdc.js",
        "https://raw.githubusercontent.com/ddgksf2013/Scripts/5bfa7fad4d262740131334169c222ca9ac2d353a/douban.js",
    ):
        assert loon_sources.get(script, "").startswith("loon/plugins/"), script
    assert any(url.startswith("https://kelee.one/") for url in loon_sources)
    # Redirect targets inside hosted plugins (Q-Search, General) are not resources.
    plugin_urls = {url for url, source in loon_sources.items() if source.startswith("loon/plugins/")}
    assert plugin_urls == {
        "https://raw.githubusercontent.com/ddgksf2013/Scripts/819a88e0efbfeb5dfdb15e93c6d007a5e790a15f/amdc.js",
        "https://raw.githubusercontent.com/ddgksf2013/Scripts/5bfa7fad4d262740131334169c222ca9ac2d353a/douban.js",
        "https://raw.githubusercontent.com/ddgksf2013/Scripts/4ff1d89274c694454ac3a494ae1a2d4cfd1edf56/weibo_json.js",
        "https://raw.githubusercontent.com/ddgksf2013/Scripts/08ad3524ab6924afd86ad6dc18ed48050a8abede/weibo_search_info.json",
        "https://raw.githubusercontent.com/ddgksf2013/Scripts/9b35fd55063e995b1ccec2f022a1c56f29d76878/weibo_search_topic.json",
    }, plugin_urls
    # The generated Surge rewrite module is treated like a hosted plugin: only its script-path URLs.
    rewrite_text = check.SURGE_REWRITE_MODULE.read_text(encoding="utf-8")
    rewrite_scripts = {
        url for line in rewrite_text.splitlines() if not line.lstrip().startswith("#")
        for url in check.SCRIPT_PATH_RE.findall(line)
    }
    rewrite_urls = {r.url for r in resources if r.source.startswith("surge/modules/rewrite.sgmodule")}
    assert rewrite_scripts and rewrite_urls == rewrite_scripts, rewrite_urls ^ rewrite_scripts

    loon_resource = check.Resource(
        "https://kelee.one/Tool/Loon/Lpx/Example.lpx", "loon-resource", "loon/bootstrap.example.conf:1"
    )
    assert check.user_agent_for(loon_resource) == check.LOON_USER_AGENT

    qx_resource = check.Resource(
        "https://resources.example/rewrite.conf",
        "qx-resource",
        "quantumultx/bootstrap.example.conf:1",
    )
    clash_resource = check.Resource(
        "https://resources.example/rules.yaml",
        "clash-rule",
        "mihomo/verge/config.yaml:rule-providers.Example",
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

    surge_rule = check.Resource("https://rules.example/list.list", "surge-rule", "surge/proxy-config.conf:1")
    assert check.validate_body(surge_rule, "text/plain", b"DOMAIN-SUFFIX, example.com\n", "full") is None
    assert check.validate_body(surge_rule, "text/plain", b"HOST-SUFFIX,example.com\n", "full") is not None
    assert check.validate_body(surge_rule, "text/plain", b"+.example.com\n", "full") is not None
    assert check.validate_body(surge_rule, "text/plain", b"# only comments\n", "full") is not None
    for bad in (b"AND,((DOMAIN,example.com)\n", b"DEST-PORT,not-a-port\n", b"DOMAIN,example.com,pre-matching\n"):
        assert check.validate_body(surge_rule, "text/plain", bad, "full") is not None, bad
    good = b"AND,((DOMAIN,example.com),(DEST-PORT,443))\nDEST-PORT,>=50000\nIP-CIDR,1.2.3.0/24,no-resolve\n"
    assert check.validate_body(surge_rule, "text/plain", good, "full") is None
    assert len(check.surge_reject_urls()) == 2
    assert any(r.kind == "surge-rule" and r.url.endswith("/surge/rules/reject_allow.list") for r in resources)

    test_empty_range_falls_back_to_plain_get()
    test_surge_reject_overlap_warns_but_bad_input_fails()

    print(
        f"Remote resource offline tests passed "
        f"({len(resources)} resources, {icon_count} icons)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
