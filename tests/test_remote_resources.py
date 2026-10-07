#!/usr/bin/env python3
"""Offline tests for remote resource extraction and response validation."""
from __future__ import annotations

import email.message
import contextlib
import hashlib
import io
import os
import re
import sys
import tempfile
import urllib.request
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_remote_resources as check  # noqa: E402
import build_surge_modules  # noqa: E402


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


def test_provider_content_types() -> None:
    cases = (
        (b'payload: ["DOMAIN,a.test", "IP-CIDR,192.0.2.0/24", "IP-CIDR6,2001:db8::/32,no-resolve", "IP-ASN,13335", "GEOIP,CN"]', "classical", "yaml", 4),
        (b'# header\n! comment\nDOMAIN-SUFFIX,example.test\nPROCESS-NAME,fixture.exe\nIP-ASN,13335,no-resolve\n', "classical", "text", 1),
        (b'payload: ["+.example.test", "192.0.2.1"]', "domain", "yaml", 0),
        (b'192.0.2.1\n*.example.test\n', "domain", "text", 0),
        (b'payload: ["192.0.2.0/24", "2001:db8::/32"]', "ipcidr", "yaml", 2),
        (b'192.0.2.0/24\n', "ipcidr", "text", 1),
        (b'payload: []', "classical", "yaml", 0),
    )
    for body, behavior, fmt, expected in cases:
        assert check.provider_ip_count(body, behavior, fmt) == expected
    negatives = (
        (b'payload: ["IP-CIDR,broken"]', "classical", "yaml"),
        (b'payload: ["IP-CIDR,2001:db8::/32"]', "classical", "yaml"),
        (b'payload: ["IP-CIDR6,192.0.2.0/24"]', "classical", "yaml"),
        (b'payload: ["IP-ASN,AS13335"]', "classical", "yaml"),
        (b'payload: ["IP-ASN,4294967296"]', "classical", "yaml"),
        (b'payload: ["GEOIP,not-a-code"]', "classical", "yaml"),
        (b'payload: ["DOMAIN,a.test,no-resolve"]', "classical", "yaml"),
        (b'payload: ["FUTURE-TYPE,value"]', "classical", "yaml"),
        (b'payload: ["AND,((DOMAIN,a.test),(IP-CIDR,192.0.2.0/24))"]', "classical", "yaml"),
        (b'payload: ["IP-CIDR,192.0.2.0/24"]', "domain", "yaml"),
        (b'192.0.2.0/24', "domain", "text"),
        (b'192.0.2.1', "ipcidr", "text"),
        (b'payload: [12]', "classical", "yaml"),
        (b'payload: [""]', "classical", "yaml"),
        (b'payload: [', "classical", "yaml"),
        (b'<html>404</html>', "classical", "yaml"),
        (b'payload: ["DOMAIN,a\xff.test"]', "classical", "yaml"),
        (b'payload: []', "unknown", "yaml"),
        (b'payload: []', "domain", "mrs"),
    )
    for body, behavior, fmt in negatives:
        try:
            check.provider_ip_count(body, behavior, fmt)
        except (ValueError, UnicodeError, check.yaml.YAMLError):
            continue
        raise AssertionError((body, behavior, fmt))


def content_fixture(tail: tuple[str, ...] = ()) -> dict:
    return {
        "rule-providers": {
            "Fixture": {"type": "http", "url": "https://rules.example/fixture.yaml", "behavior": "classical"},
        },
        "rules": ["RULE-SET,Fixture,DIRECT,no-resolve"] + list(tail) + ["GEOIP,CN,DIRECT", "MATCH,REJECT"],
    }


def fixture_result(body: bytes, ok: bool = True) -> check.Result:
    return check.Result(
        check.Resource("https://rules.example/fixture.yaml", "clash-rule", "test", "yaml"),
        ok, 200 if ok else None, "text/plain", "https://rules.example/fixture.yaml", len(body),
        "ok" if ok else "timed out", body,
    )


def test_mihomo_content_coverage() -> None:
    ip_body = b'payload: ["DOMAIN,a.test", "IP-CIDR,192.0.2.0/24,no-resolve"]'
    # A new IP rule must be caught even when its inner rule says no-resolve.
    comparisons, missing, stale = check.mihomo_content_report([fixture_result(ip_body)], content_fixture())
    assert comparisons[0].ip_rules == 1 and missing == ["Fixture"] and stale == []
    covered = content_fixture(("RULE-SET,Fixture,DIRECT",))
    assert check.mihomo_content_report([fixture_result(ip_body)], covered)[1:] == ([], [])
    domain_body = b'payload: ["DOMAIN,a.test"]'
    comparisons, missing, stale = check.mihomo_content_report([fixture_result(domain_body)], covered)
    assert missing == [] and stale == ["Fixture"] and comparisons[0].ip_rules == 0
    assert covered["rules"][1] == "RULE-SET,Fixture,DIRECT", "inspection must never remove an old fallback"
    comparisons, missing, stale = check.mihomo_content_report([fixture_result(b"", False)], covered)
    assert comparisons[0].ip_rules is None and "not compared" in comparisons[0].message
    assert missing == [] and stale == [], "failure must not look like a provider without IP"
    for body in (b'payload: ["FUTURE,value"]', b'payload: [12]', b'payload: ["DOMAIN,\xff.test"]'):
        comparisons, missing, stale = check.mihomo_content_report([fixture_result(body)], covered)
        assert comparisons[0].ip_rules is None and not missing and not stale
    assert check.mihomo_content_report([], covered)[0][0].ip_rules is None
    # A reference after GEOIP,CN cannot count as IP-stage coverage.
    late = content_fixture()
    late["rules"].append("RULE-SET,Fixture,DIRECT")
    assert check.mihomo_content_report([fixture_result(ip_body)], late)[1] == ["Fixture"]
    bad = content_fixture()
    bad["rules"][0] = "RULE-SET,Undefined,DIRECT,no-resolve"
    try:
        check.mihomo_content_report([fixture_result(ip_body)], bad)
    except ValueError as exc:
        assert "undefined provider" in str(exc)
    else:
        raise AssertionError("undefined provider accepted")
    # A shared URL must still be compared under every provider's own definition.
    aliases = content_fixture(("RULE-SET,Fixture,DIRECT",))
    aliases["rule-providers"]["Alias"] = dict(aliases["rule-providers"]["Fixture"])
    aliases["rules"].insert(1, "RULE-SET,Alias,DIRECT,no-resolve")
    comparisons, missing, stale = check.mihomo_content_report([fixture_result(ip_body)], aliases)
    assert len(comparisons) == 2 and missing == ["Alias"]
    aliases["rule-providers"]["Alias"]["behavior"] = "domain"
    comparisons, _, _ = check.mihomo_content_report([fixture_result(ip_body)], aliases)
    assert comparisons[1].ip_rules is None, "alias behavior must not be copied from the first definition"


def test_candidate_checkout_and_summary() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "mihomo/rules/fixture.yaml"
        path.parent.mkdir(parents=True)
        body = b'payload: ["IP-CIDR6,2001:db8::/32"]'
        path.write_bytes(body)
        config = content_fixture()
        config["rule-providers"]["Fixture"]["url"] = "https://cdn.jsdelivr.net/gh/wenbingkun/proxy-config@main/mihomo/rules/fixture.yaml"
        with patch.object(check, "ROOT", root):
            # A failed remote URL must not substitute stale remote bytes for the candidate.
            comparisons, missing, stale = check.mihomo_content_report([], config)
            assert comparisons[0].ip_rules == 1 and comparisons[0].origin == "checkout:mihomo/rules/fixture.yaml"
            assert comparisons[0].sha256 == hashlib.sha256(body).hexdigest() and missing == ["Fixture"]
            path.unlink()
            missing_file, missing, stale = check.mihomo_content_report([], config)
            assert missing_file[0].ip_rules is None and missing == [] and stale == []
            assert check.checkout_provider_path("https://cdn.jsdelivr.net/gh/wenbingkun/proxy-config@other/mihomo/rules/fixture.yaml") is None
            assert check.checkout_provider_path("https://elsewhere.example/gh/wenbingkun/proxy-config@main/mihomo/rules/fixture.yaml") is None
        summary = root / "summary.md"
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}), contextlib.redirect_stdout(io.StringIO()):
            check.emit_mihomo_report(missing_file, [], [])
        assert "not compared=1" in summary.read_text() and "not compared" in summary.read_text()


def test_content_main_exit_and_modes() -> None:
    """Exercise the actual full-mode exit path, including unavailable comparisons."""
    config = content_fixture()
    ip_result = fixture_result(b'payload: ["IP-CIDR,192.0.2.0/24"]')
    original_report = check.mihomo_content_report
    with patch.object(check, "extract_resources", return_value=[ip_result.resource]), \
         patch.object(check, "script_update_targets", return_value=[]), \
         patch.object(check, "fetch", return_value=ip_result), \
         patch.object(check, "reject_overlap_report", return_value=([], [])), \
         patch.object(check, "mihomo_content_report", side_effect=lambda results: original_report(results, config)) as report, \
         patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()) as output:
        with patch.object(sys, "argv", ["check", "--mode", "light"]):
            assert check.main() == 0 and report.call_count == 0
        with patch.object(sys, "argv", ["check", "--mode", "full"]):
            assert check.main() == 1 and "lack an IP-stage reference" in output.getvalue()
            config["rules"].insert(1, "RULE-SET,Fixture,DIRECT")
            assert check.main() == 0
            with patch.object(check, "fetch", return_value=fixture_result(b"", False)):
                assert check.main() == 1 and "not compared" in output.getvalue()
            with patch.object(check, "fetch", return_value=fixture_result(b'payload: ["FUTURE,value"]')):
                assert check.main() == 1
            # Successful checkout classification cannot hide remote availability failure.
            valid = [check.ProviderComparison("Fixture", 1, "checkout:fixture.yaml", "hash", "compared")]
            with patch.object(check, "mihomo_content_report", return_value=(valid, [], [])), \
                 patch.object(check, "fetch", return_value=fixture_result(b"", False)):
                assert check.main() == 1
            # Removing current IP content only warns; it never fails or mutates rules.
            with patch.object(check, "fetch", return_value=fixture_result(b'payload: ["DOMAIN,a.test"]')):
                assert check.main() == 0 and "do not auto-remove" in output.getvalue()


def test_full_download_must_be_complete() -> None:
    resource = fixture_result(b"").resource
    body = b'payload: ["DOMAIN,a.test"]'
    with patch.object(check, "read_response", return_value=(200, "text/plain", body, resource.url)) as read:
        result = check.fetch(resource, "full", 1, 0)
        assert result.ok and result.body == body and read.call_count == 1
        assert "Range" not in read.call_args.args[1]
    for status, content in ((206, body), (200, b"x" * (check.FULL_LIMIT + 1))):
        with patch.object(check, "read_response", return_value=(status, "text/plain", content, resource.url)):
            result = check.fetch(resource, "full", 1, 0)
            assert not result.ok and not result.body
    with patch.object(check, "read_response", return_value=(200, "text/plain", body, resource.url)):
        assert check.fetch(resource, "light", 1, 0).body == b""
    # A valid-looking 200 prefix must not pass when HTTP framing says bytes are missing.
    for length, expected_ok in ((str(len(body)), True), (str(len(body) + 10), False), ("broken", False)):
        response = FakeResponse(200, body, resource.url)
        response.headers["Content-Length"] = length
        with patch.object(check.urllib.request, "urlopen", return_value=response):
            assert check.fetch(resource, "full", 1, 0).ok == expected_ok
    with patch.object(check, "read_response", side_effect=check.http.client.IncompleteRead(body, 10)):
        result = check.fetch(resource, "full", 1, 0)
        assert not result.ok and not result.body and "IncompleteRead" in result.message


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
    # Check execution resources from every client, including nested scripts in hosted containers.
    loose_moyu = [(url, check.source_policy_error(url)) for url in urls if check.source_policy_error(url)]
    assert not loose_moyu, loose_moyu
    sha = "314f61060a4c72c8a8b9f6b2ff457c8d35abbdac"
    for url in (
        f"https://raw.githubusercontent.com/ddgksf2013/Scripts/{sha}/amap.js",
        f"https://cdn.jsdelivr.net/gh/ddgksf2013/Scripts@{sha}/amap.js",
        "https://raw.githubusercontent.com/wenbingkun/proxy-config/main/quantumultx/scripts/amap.js",
        "https://kelee.one/Tool/Loon/Lpx/Amap_remove_ads.lpx",
    ):
        assert check.source_policy_error(url) is None, url
    for url in (
        "https://raw.githubusercontent.com/ddgksf2013/Scripts/master/amap.js",
        "https://raw.githubusercontent.com/ddgksf2013/Rewrite/refs/heads/master/AdBlock/AmapAds.conf",
        "https://cdn.jsdelivr.net/gh/ddgksf2013/Scripts@main/amap.js",
        "https://cdn.jsdelivr.net/gh/ddgksf2013/Scripts@314f610/amap.js",
        "https://github.com/ddgksf2013/Scripts/raw/master/amap.js",
        "https://ddgksf2013.top/scripts/amap.js",
        "https://cdn.ddgksf2013.top/scripts/amap.js",
    ):
        assert check.source_policy_error(url), url
        # Exercise the checker entry point: forbidden sources fail without fetching.
        result = check.fetch(check.Resource(url, "script", "test"), "light", timeout=1, retries=0)
        assert not result.ok and result.bytes_read == 0 and result.status is None, result

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
    # Every script a hosted plugin loads is pinned to a commit, or is this repo's own script.
    pinned = re.compile(r"^https://raw\.githubusercontent\.com/[^/]+/[^/]+/[0-9a-f]{40}/")
    own = "https://raw.githubusercontent.com/wenbingkun/proxy-config/main/quantumultx/scripts/"
    loose = sorted(u for u in plugin_urls if not pinned.match(u) and not u.startswith(own))
    assert not loose, loose
    assert {own + "amdc.js", own + "xiaohongshu.js",
            "https://raw.githubusercontent.com/wenbingkun/proxy-config/9f2bd622348198b7eaa84ccbfe2c57bc010e7bfd/quantumultx/scripts/umetrip.js"} <= plugin_urls, plugin_urls
    # The generated Surge rewrite modules are treated like hosted plugins: only their script-path URLs.
    rewrite_scripts = {
        url for path in check.SURGE_REWRITE_MODULES for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#") for url in check.SCRIPT_PATH_RE.findall(line)
    }
    rewrite_urls = {r.url for r in resources if r.source.startswith("surge/modules/rewrite/")}
    assert len(check.SURGE_REWRITE_MODULES) == len(build_surge_modules.SOURCES), check.SURGE_REWRITE_MODULES
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
    test_provider_content_types()
    test_mihomo_content_coverage()
    test_candidate_checkout_and_summary()
    test_content_main_exit_and_modes()
    test_full_download_must_be_complete()

    print(
        f"Remote resource offline tests passed "
        f"({len(resources)} resources, {icon_count} icons)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
