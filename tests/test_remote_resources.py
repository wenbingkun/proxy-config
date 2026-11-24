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
import urllib.error
import urllib.request
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_remote_resources as check  # noqa: E402
import check_upstreams  # noqa: E402
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
        failures, warnings, cdn_warnings = check.reject_overlap_report(5)
    finally:
        check.read_response, check.surge_reject_urls = original_read, original_urls
    assert len(failures) == 2, failures
    assert any("not-a-cidr" in f for f in failures) and any("unbalanced" in f for f in failures), failures
    assert any("ads.coingecko.com" in w and "excluded" not in w for w in warnings), warnings
    assert any("metrics.citi.com" in w and "[excluded by reject_allow.list]" in w for w in warnings), warnings
    assert not any("noconflict.invalid" in w for w in warnings), warnings
    assert cdn_warnings == [], cdn_warnings


JSDELIVR = "https://cdn.jsdelivr.net/gh/owner/repo@master/rule/QuantumultX/Test/Test.list"
ORIGIN = "https://raw.githubusercontent.com/owner/repo/master/rule/QuantumultX/Test/Test.list"
SIZE_BODY = b"Package size exceeded the configured limit of 50 MB. Try https://github.com/owner/repo instead."
RULES_BODY = b"payload:\n  - '+.ads.example'\n" * 500


def http_error(url: str, code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "error", email.message.Message(), io.BytesIO(body))


def test_jsdelivr_origin_url_mapping() -> None:
    sha = "0123456789abcdef0123456789abcdef01234567"
    cases = {
        JSDELIVR + "#out=IP-CIDR+IP6-CIDR&ntf=0": ORIGIN,
        f"https://cdn.jsdelivr.net/gh/owner/repo@{sha}/a/b/c.yaml":
            f"https://raw.githubusercontent.com/owner/repo/{sha}/a/b/c.yaml",
        "https://cdn.jsdelivr.net/gh/owner/repo@main/": None,
        "https://cdn.jsdelivr.net/gh/owner/repo/rule.list": None,
        "https://cdn.jsdelivr.net/npm/package@1.0.0/index.js": None,
        "https://fastly.jsdelivr.net/gh/owner/repo@master/rule.list": None,
        "http://cdn.jsdelivr.net/gh/owner/repo@master/rule.list": None,
        "https://raw.githubusercontent.com/owner/repo/master/rule.list": None,
    }
    for url, expected in cases.items():
        assert check.jsdelivr_origin_url(url) == expected, (url, check.jsdelivr_origin_url(url))


def run_size_fallback(url: str, cdn_code: int, cdn_body: bytes, origin_code: int,
                      complete: bool = False, retries: int = 0) -> tuple[check.Result, list[str]]:
    requested: list[str] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> FakeResponse:
        requested.append(request.full_url)
        if request.full_url.startswith("https://raw.githubusercontent.com/"):
            if origin_code != 200:
                raise http_error(request.full_url, origin_code, b"404: Not Found")
            return FakeResponse(200, RULES_BODY, request.full_url)
        raise http_error(request.full_url, cdn_code, cdn_body)

    resource = check.Resource(url, "qx-resource", "quantumultx/bootstrap.example.conf:1")
    with patch.object(check.urllib.request, "urlopen", fake_urlopen):
        result = check.fetch(resource, "light", timeout=1, retries=retries, complete=complete)
    return result, requested


def test_jsdelivr_size_error_checks_origin() -> None:
    """jsDelivr's transient whole-repo fallback only warns when the same file passes at the origin."""
    url = JSDELIVR + "#out=IP-CIDR&ntf=0"
    result, requested = run_size_fallback(url, 403, SIZE_BODY, 200, retries=1)
    assert requested == [url, url, ORIGIN], requested
    assert result.ok and result.resource.url == url and result.final_url == ORIGIN, result
    assert "package size fallback" in result.warning and ORIGIN in result.warning, result.warning

    # A file deleted or renamed upstream still fails, and the message names both sides.
    result, requested = run_size_fallback(JSDELIVR, 403, SIZE_BODY, 404)
    assert requested == [JSDELIVR, ORIGIN], requested
    assert not result.ok and not result.warning, result
    assert "package size fallback" in result.message and "HTTP 404" in result.message, result.message

    # Other 403s, other hosts and pinned script comparisons never consult the origin.
    other_host = "https://cdn.example/gh/owner/repo@master/rule/QuantumultX/Test/Test.list"
    for case_url, body, complete in ((JSDELIVR, b"Forbidden", False), (other_host, SIZE_BODY, False),
                                     (JSDELIVR, SIZE_BODY, True)):
        result, requested = run_size_fallback(case_url, 403, body, 200, complete=complete)
        assert requested == [case_url], requested
        assert not result.ok and result.message == "HTTP 403" and not result.warning, result
    result, requested = run_size_fallback(JSDELIVR, 404, SIZE_BODY, 200)
    assert requested == [JSDELIVR] and not result.ok, (requested, result)


def test_reject_overlap_size_error_checks_origin() -> None:
    bodies = {ORIGIN: b"DOMAIN-SUFFIX,noconflict.invalid\n"}
    requested: list[str] = []

    def fake_read(url: str, *_args: object) -> tuple[int, str, bytes, str]:
        requested.append(url)
        if url in bodies:
            return 200, "text/plain", bodies[url], url
        raise http_error(url, 403, SIZE_BODY if url == JSDELIVR else b"Forbidden")

    other = "https://cdn.jsdelivr.net/gh/owner/repo@master/rule/Surge/Other/Other.list"
    with patch.object(check, "surge_reject_urls", return_value=[JSDELIVR, other]), \
         patch.object(check, "read_response", fake_read):
        failures, warnings, cdn_warnings = check.reject_overlap_report(5)
    assert requested == [JSDELIVR, ORIGIN, other], requested
    assert len(failures) == 1 and other in failures[0] and "HTTPError" in failures[0], failures
    assert warnings == [], warnings
    assert len(cdn_warnings) == 1 and JSDELIVR in cdn_warnings[0] and ORIGIN in cdn_warnings[0], cdn_warnings

    # An unparsable origin copy fails without a "passed at the origin" warning.
    bodies[ORIGIN] = b"IP-CIDR,not-a-cidr\n"
    with patch.object(check, "surge_reject_urls", return_value=[JSDELIVR]), \
         patch.object(check, "read_response", fake_read):
        failures, _, cdn_warnings = check.reject_overlap_report(5)
    assert len(failures) == 1 and "not-a-cidr" in failures[0] and cdn_warnings == [], (failures, cdn_warnings)

    bodies.clear()
    with patch.object(check, "surge_reject_urls", return_value=[JSDELIVR]), \
         patch.object(check, "read_response", fake_read):
        failures, _, cdn_warnings = check.reject_overlap_report(5)
    assert len(failures) == 1 and "package size fallback" in failures[0], failures
    assert cdn_warnings == [], cdn_warnings


def test_full_mode_fallback_keeps_provider_url() -> None:
    """A full-mode clash rule checked at the origin still feeds the provider defined by its jsDelivr URL."""
    url = "https://cdn.jsdelivr.net/gh/owner/repo@master/rule/Clash/Test/Test.yaml"
    origin = "https://raw.githubusercontent.com/owner/repo/master/rule/Clash/Test/Test.yaml"
    body = b'payload:\n  - IP-CIDR,192.0.2.0/24\n'

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> FakeResponse:
        if request.full_url == origin:
            return FakeResponse(200, body, origin)
        raise http_error(request.full_url, 403, SIZE_BODY)

    resource = check.Resource(url, "clash-rule", "mihomo/verge/config.yaml:1", "yaml")
    with patch.object(check.urllib.request, "urlopen", fake_urlopen):
        result = check.fetch(resource, "full", timeout=1, retries=0)
    assert result.ok and result.warning and result.body == body, result
    config = content_fixture()
    config["rule-providers"]["Fixture"]["url"] = url
    comparisons, missing, _ = check.mihomo_content_report([result], config)
    assert [(c.name, c.ip_rules, c.origin) for c in comparisons] == [("Fixture", 1, f"http:{url}")], comparisons
    assert missing == ["Fixture"], missing


def test_warnings_do_not_fail_main() -> None:
    resource = check.Resource(JSDELIVR, "qx-resource", "quantumultx/bootstrap.example.conf:1")
    warned = check.Result(resource, True, 200, "text/plain", ORIGIN, 10, "ok",
                          warning="jsDelivr HTTP 403 package size fallback; 100%\nok")
    failed = check.Result(resource, False, 403, "", JSDELIVR, 0, "HTTP 403")

    def run(result: check.Result, env: dict[str, str]) -> tuple[int, str]:
        with patch.object(check, "extract_resources", return_value=[resource]), \
             patch.object(check, "fetch", return_value=result), \
             patch.dict(os.environ, env, clear=True), patch.object(sys, "argv", ["check"]), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            code = check.main()
        return code, output.getvalue()

    code, text = run(warned, {})
    assert code == 0 and "[warn] qx-resource" in text and "1 warnings" in text, text
    assert "::warning::" not in text, text
    code, text = run(warned, {"GITHUB_ACTIONS": "true"})
    assert code == 0 and "::warning::qx-resource" in text and "100%25%0Aok" in text, text
    code, text = run(failed, {"GITHUB_ACTIONS": "true"})
    assert code == 1 and "[fail] qx-resource" in text and "::warning::" not in text, text


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
         patch.object(check, "reject_overlap_report", return_value=([], [], [])), \
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


def test_qx_rewrite_extraction_boundaries() -> None:
    assert check.QX_REWRITE_RESOURCE_RE.pattern == check_upstreams.QX_SCRIPT_RE.pattern
    with tempfile.TemporaryDirectory() as tmp:
        rewrite_dir = Path(tmp) / "quantumultx" / "rewrite"
        rewrite_dir.mkdir(parents=True)
        sample = rewrite_dir / "sample.snippet"
        sample.write_text(
            "\n".join(
                (
                    "#!icon=https://assets.example/icon.png",
                    "# comment https://ignored.example/comment.js",
                    "; semicolon https://ignored.example/semicolon.js",
                    "^https://api.example/a url 302 https://redirect.example/target",
                    "^https://api.example/b url script-response-body https://scripts.example/one.js",
                    "^https://api.example/c UA url-and-header script-request-body https://scripts.example/two.js",
                    "^https://api.example/d UA url-and-header echo-response text/html echo-response https://scripts.example/body.json",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        clash_cfg = Path(tmp) / "clash.yaml"
        clash_cfg.write_text("rule-providers: {}\n", encoding="utf-8")
        deploy_sh = Path(tmp) / "deploy.sh"
        deploy_sh.write_text("", encoding="utf-8")
        with patch.multiple(
            check,
            ROOT=Path(tmp),
            CLASH_CONFIG=clash_cfg,
            DEPLOY_SCRIPT=deploy_sh,
            QX_REWRITE_FILES=(sample,),
            QX_FILES=(sample,),
            LOON_FILES=(),
            SURGE_FILES=(),
            SURGE_REWRITE_MODULES=(),
        ):
            extracted = check.extract_resources()
        urls = {item.url for item in extracted}
        assert urls == {
            "https://scripts.example/one.js",
            "https://scripts.example/two.js",
            "https://scripts.example/body.json",
        }, urls
        assert all(check.client_of(item.source) == "qx" for item in extracted)


def test_keep_body_and_complete_boundaries() -> None:
    """keep_body allows normal redirects and 403 fallback; complete stays strict; keep_body=False drops non-clash body."""
    url = JSDELIVR
    redirected = "https://cdn.example/redirected/Test.list"
    body = b"HOST-SUFFIX,example.test,PROXY\n"
    resource = check.Resource(url, "qx-resource", "quantumultx/bootstrap.example.conf:1")

    # keep_body=True allows redirects and retains the full response body.
    with patch.object(check, "read_response", return_value=(200, "text/plain", body, redirected)):
        result = check.fetch(resource, "full", timeout=1, retries=0, keep_body=True)
    assert result.ok and result.body == body and result.final_url == redirected, result

    # complete=True still rejects redirects.
    with patch.object(check, "read_response", return_value=(200, "text/plain", body, redirected)):
        result = check.fetch(resource, "full", timeout=1, retries=0, complete=True)
    assert not result.ok and "redirected" in result.message, result

    # keep_body=False keeps body empty for non-clash resources even in full mode.
    with patch.object(check, "read_response", return_value=(200, "text/plain", body, url)):
        result = check.fetch(resource, "full", timeout=1, retries=0, keep_body=False)
    assert result.ok and result.body == b"", result

    # keep_body=True propagates through jsDelivr HTTP 403 package-size origin fallback.
    requested: list[str] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> FakeResponse:
        requested.append(request.full_url)
        if request.full_url == ORIGIN:
            return FakeResponse(200, body, ORIGIN)
        raise http_error(request.full_url, 403, SIZE_BODY)

    with patch.object(check.urllib.request, "urlopen", fake_urlopen):
        result = check.fetch(resource, "full", timeout=1, retries=0, keep_body=True)
    assert requested == [url, ORIGIN], requested
    assert result.ok and result.body == body and result.resource.url == url and result.warning, result


def test_parse_mutable_container_syntax() -> None:
    loon_body = (
        "#!icon=https://ignored.example/icon.png\n"
        "# comment https://ignored.example/comment.js\n"
        "; semicolon https://ignored.example/semi.js\n"
        "[Rule]\n"
        "AND, ((DOMAIN, a.test), (OR, (DOMAIN, b.test))), PROXY\n"
        "DOMAIN-SUFFIX, bsbsb.top, PROXY\n"
        "[Rewrite]\n"
        "^https:\\/\\/api\\.example\\/v1\\/ad reject-200\n"
        'response if ${url} ~= /^https:\\/\\/api\\.example\\/mine/ then jq_file("https://raw.githubusercontent.com/kokoryh/Sparkle/refs/heads/master/jq/bilibili.mine.jq")\n'
        'response if ${url} ~= /^https:\\/\\/api\\.example\\/tab/ then jq_file("https://raw.githubusercontent.com/kokoryh/Sparkle/refs/heads/master/jq/bilibili.tab.jq")\n'
        'response if ${url} ~= /^https:\\/\\/api\\.example\\/json/ then script("https://raw.githubusercontent.com/kokoryh/Sparkle/refs/heads/master/dist/bilibili.json.js", timeout=10)\n'
        "^https:\\/\\/api\\.example\\/plain 302 https://redirect.example/landing\n"
        "[Script]\n"
        "http-request ^https:\\/\\/api\\.example\\/req script-path=https://raw.githubusercontent.com/kokoryh/Sparkle/refs/heads/master/dist/bilibili.protobuf.request.js\n"
        "http-response ^https:\\/\\/api\\.example\\/res script-path=\"https://raw.githubusercontent.com/kokoryh/Sparkle/refs/heads/master/dist/bilibili.protobuf.response.js\"\n"
        "http-response ^https:\\/\\/api\\.example\\/web script-path=https://raw.githubusercontent.com/kokoryh/Sparkle/refs/heads/master/dist/webpage.bilibili.js\n"
        "http-response ^https:\\/\\/api\\.example\\/jq jq-path=https://cdn.example/extra.jq, data-path=https://cdn.example/data.json\n"
        "[MitM]\n"
        "hostname = %APPEND% API.Bilibili.com, www.bilibili.com\n"
        "hostname = %INSERT% app.bilibili.com, api.bilibili.com\n"
    ).encode("utf-8")
    parsed_loon = check.parse_mutable_container(loon_body, "loon")
    assert parsed_loon["mitm_hostnames"] == ["api.bilibili.com", "app.bilibili.com", "www.bilibili.com"], parsed_loon
    assert parsed_loon["mitm_count"] == 3
    assert parsed_loon["scripts"] == sorted([
        "https://cdn.example/data.json",
        "https://cdn.example/extra.jq",
        "https://raw.githubusercontent.com/kokoryh/Sparkle/refs/heads/master/dist/bilibili.json.js",
        "https://raw.githubusercontent.com/kokoryh/Sparkle/refs/heads/master/dist/bilibili.protobuf.request.js",
        "https://raw.githubusercontent.com/kokoryh/Sparkle/refs/heads/master/dist/bilibili.protobuf.response.js",
        "https://raw.githubusercontent.com/kokoryh/Sparkle/refs/heads/master/dist/webpage.bilibili.js",
        "https://raw.githubusercontent.com/kokoryh/Sparkle/refs/heads/master/jq/bilibili.mine.jq",
        "https://raw.githubusercontent.com/kokoryh/Sparkle/refs/heads/master/jq/bilibili.tab.jq",
    ]), parsed_loon["scripts"]
    assert parsed_loon["other_urls"] == ["https://redirect.example/landing"], parsed_loon["other_urls"]
    assert parsed_loon["rules"] == [
        "AND,((DOMAIN,a.test),(OR,(DOMAIN,b.test))),PROXY",
        "DOMAIN-SUFFIX,bsbsb.top,PROXY",
    ], parsed_loon["rules"]

    qx_body = (
        "# header\n"
        "hostname = %APPEND% *spclient.spotify.com, SPCLIENT.wg.spotify.com\n"
        "^https:\\/\\/spclient\\.wg\\.spotify\\.com\\/a url script-response-body https://scripts.example/sp.js\n"
        "^https:\\/\\/spclient\\.wg\\.spotify\\.com\\/b UA url-and-header echo-response text/json echo-response https://scripts.example/sp.json\n"
        "^https:\\/\\/spclient\\.wg\\.spotify\\.com\\/c url 302 https://redirect.example/sp\n"
        "host-suffix, ads.spotify.test, reject\n"
    ).encode("utf-8")
    parsed_qx = check.parse_mutable_container(qx_body, "qx")
    assert parsed_qx["mitm_hostnames"] == ["*spclient.spotify.com", "spclient.wg.spotify.com"], parsed_qx
    assert parsed_qx["scripts"] == ["https://scripts.example/sp.js", "https://scripts.example/sp.json"], parsed_qx
    assert parsed_qx["other_urls"] == ["https://redirect.example/sp"], parsed_qx
    assert parsed_qx["rules"] == ["host-suffix,ads.spotify.test,reject"], parsed_qx


def test_mutable_container_baselines_offline() -> None:
    """Baselines must match enabled mutable containers bidirectionally and validate internally."""
    observed_date, baselines = check.load_mutable_baselines()
    assert check.DATE_ISO_RE.fullmatch(observed_date), observed_date
    enabled = check.enabled_mutable_containers()
    baseline_keys = [(str(b["client"]), str(b["url"])) for b in baselines]
    assert set(baseline_keys) == set(enabled) and len(baseline_keys) == len(enabled), (baseline_keys, enabled)
    assert len(enabled) == 5, enabled
    assert not any("iRingo.Siri" in url for _, url in enabled), enabled

    # Every QX/Loon exception in rules/upstreams.yaml must fall within MUTABLE_CONTAINER_FIELDS.
    policy = check.yaml.safe_load(check.UPSTREAMS_POLICY.read_text(encoding="utf-8"))
    for item in policy["exceptions"]:
        if item["client"] in {"qx", "loon"}:
            assert (item["client"], item["field"]) in check.MUTABLE_CONTAINER_FIELDS, item

    # Fragment stripping + enabled=false handling in enabled_mutable_containers().
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "rules").mkdir()
        (root / "quantumultx").mkdir()
        (root / "loon").mkdir()
        (root / "rules" / "upstreams.yaml").write_text(
            "version: 1\nrules: []\nicons: []\nexceptions:\n"
            "- client: qx\n  field: rewrite_remote\n  url: https://upstream.example/a.conf\n  reason: test\n"
            "- client: qx\n  field: rewrite_remote\n  url: https://upstream.example/b.conf\n  reason: test\n"
            "- client: loon\n  field: plugin\n  url: https://upstream.example/c.plugin\n  reason: test\n",
            encoding="utf-8",
        )
        (root / "quantumultx" / "bootstrap.example.conf").write_text(
            "[rewrite_remote]\n"
            "https://upstream.example/a.conf#ntf=0, tag=A, enabled = true\n"
            "https://upstream.example/b.conf, tag=B, enabled = FALSE\n",
            encoding="utf-8",
        )
        (root / "loon" / "bootstrap.example.conf").write_text(
            "[Plugin]\n"
            "https://upstream.example/c.plugin, tag=C\n",
            encoding="utf-8",
        )
        got = check.enabled_mutable_containers(root)
        assert got == [
            ("loon", "https://upstream.example/c.plugin"),
            ("qx", "https://upstream.example/a.conf#ntf=0"),
        ], got

        # Negative baseline schema validations.
        valid_entry = {
            "client": "qx",
            "url": "https://upstream.example/a.conf",
            "mitm_count": 1,
            "mitm_sha256": check._sha256_lines(["a.example"]),
            "mitm_hostnames": ["a.example"],
            "scripts": ["https://s.example/a.js"],
            "other_urls_count": 0,
            "other_urls_sha256": check._sha256_lines([]),
            "other_urls": [],
            "rules": ["host,a.example,reject"],
        }
        bad_variants = [
            {**valid_entry, "mitm_count": 2},
            {**valid_entry, "mitm_sha256": "0" * 64},
            {**valid_entry, "mitm_hostnames": ["A.example"]},
            {**valid_entry, "scripts": ["https://s.example/b.js", "https://s.example/a.js"]},
            {**valid_entry, "rules": ["host,a.example,reject", "host,a.example,reject"]},
            {**valid_entry, "unknown_key": 1},
            {**valid_entry, "https://user:fixture-secret@upstream.example:badport/a.conf": 1},
            {**valid_entry, "client": ["loon"]},
            {**valid_entry, 1: "int-key", "extra": "str-key"},
            {**valid_entry, "url": "https://["},
            {**valid_entry, "url": "https://user:fixture-secret@upstream.example:badport/a.conf"},
            {**valid_entry, "mitm_count": True},
        ]
        for variant in bad_variants:
            bad_file = root / "bad.yaml"
            bad_file.write_text(
                check.yaml.safe_dump({"version": 1, "observed_date": "2026-11-24", "containers": [variant]}),
                encoding="utf-8",
            )
            try:
                check.load_mutable_baselines(bad_file)
            except ValueError as exc:
                assert "fixture-secret" not in str(exc), str(exc)
                continue
            raise AssertionError(f"invalid baseline accepted: {variant}")

        # YAML syntax error (e.g. bad escape `\q` in quoted URL) must raise ValueError without echoing raw source.
        yaml_err_file = root / "yaml_err.yaml"
        yaml_err_file.write_text(
            'version: 1\nobserved_date: "2026-11-24"\ncontainers:\n'
            '- client: loon\n  url: "https://user:fixture-secret@test.example/path\\q"\n',
            encoding="utf-8",
        )
        try:
            check.load_mutable_baselines(yaml_err_file)
        except ValueError as exc:
            assert "fixture-secret" not in str(exc) and "line " in str(exc), str(exc)
        else:
            raise AssertionError("invalid YAML escape accepted")

        # Deeply nested YAML (triggers RecursionError inside PyYAML) must raise ValueError cleanly.
        deep_yaml = root / "deep.yaml"
        deep_yaml.write_text("containers: " + "[" * 500 + "]" * 500 + "\n", encoding="utf-8")
        try:
            check.load_mutable_baselines(deep_yaml)
        except ValueError as exc:
            assert "nesting too deep" in str(exc), str(exc)
        else:
            raise AssertionError("deeply nested YAML accepted")

        # YAML surrogate escape (cannot encode to UTF-8) must be rejected at load time.
        surrogate_file = root / "surrogate.yaml"
        surrogate_file.write_text(
            "version: 1\nobserved_date: '2026-11-24'\ncontainers:\n"
            '- client: loon\n  url: "https://test.example/\\uD800"\n'
            f"  mitm_count: 0\n  mitm_sha256: {'0' * 64}\n  scripts: []\n"
            f"  other_urls_count: 0\n  other_urls_sha256: {'0' * 64}\n  rules: []\n",
            encoding="utf-8",
        )
        try:
            check.load_mutable_baselines(surrogate_file)
        except ValueError:
            pass
        else:
            raise AssertionError("surrogate URL accepted")

        bad_docs = [
            {"version": 1.0, "observed_date": "2026-11-24", "containers": [valid_entry]},
            {"version": 1, "observed_date": "2026-99-99", "containers": [valid_entry]},
            {"version": 1, "observed_date": "2026-02-30", "containers": [valid_entry]},
            {1: "bad-top-key", "version": 1, "observed_date": "2026-11-24", "containers": []},
        ]
        for doc in bad_docs:
            bad_file = root / "bad_doc.yaml"
            bad_file.write_text(check.yaml.safe_dump(doc), encoding="utf-8")
            try:
                check.load_mutable_baselines(bad_file)
            except ValueError:
                continue
            raise AssertionError(f"invalid baseline document accepted: {doc}")


def test_mutable_container_audit_drift_and_escaping() -> None:
    small_url = "https://upstream.example/small.lpx"
    large_url = "https://upstream.example/large.plugin"
    small_base = {
        "client": "loon",
        "url": small_url,
        "mitm_count": 1,
        "mitm_sha256": check._sha256_lines(["api.example"]),
        "mitm_hostnames": ["api.example"],
        "scripts": ["https://cdn.example/a.js"],
        "other_urls_count": 0,
        "other_urls_sha256": check._sha256_lines([]),
        "other_urls": [],
        "rules": ["DOMAIN,a.test,PROXY"],
    }
    large_base = {
        "client": "loon",
        "url": large_url,
        "mitm_count": 1,
        "mitm_sha256": check._sha256_lines(["old.example"]),
        "scripts": [],
        "other_urls_count": 0,
        "other_urls_sha256": check._sha256_lines([]),
        "rules": [],
    }
    # Clean unchanged check.
    clean_body = (
        "[Rule]\nDOMAIN, a.test, PROXY\n"
        "[Script]\nhttp-response ^x script-path=https://cdn.example/a.js\n"
        "[MitM]\nhostname = api.example\n"
    ).encode("utf-8")
    clean_res = check.Result(
        check.Resource(small_url, "loon-resource", "loon/bootstrap.example.conf:1"),
        True, 200, "text/plain", small_url, len(clean_body), "ok", clean_body,
    )
    audits = check.mutable_container_audit([clean_res], [small_base])
    assert len(audits) == 1 and audits[0].state == "unchanged", audits

    # Drifted + hostile upstream strings (C3 escaping).
    hostile_small = (
        "[Rule]\nDOMAIN, a.test, PROXY\nDOMAIN, <script>bad</script>|pipe, PROXY\n"
        "[Script]\nhttp-response ^x script-path=https://cdn.example/new%25.js\n"
        "[MitM]\nhostname = api.example, evil|<b>100%</b>.example\n"
    ).encode("utf-8")
    hostile_large = b"[MitM]\nhostname = a.example, <img src=x>|pipe.example\n"
    res_small = check.Result(
        check.Resource(small_url, "loon-resource", "loon/bootstrap.example.conf:1"),
        True, 200, "text/plain", small_url, len(hostile_small), "ok", hostile_small,
    )
    res_large = check.Result(
        check.Resource(large_url, "loon-resource", "loon/bootstrap.example.conf:2"),
        True, 200, "text/plain", large_url, len(hostile_large), "ok", hostile_large,
    )
    audits = check.mutable_container_audit([res_small, res_large], [small_base, large_base])
    assert [a.state for a in audits] == ["changed", "changed"], audits
    assert audits[1].current_mitm_hostnames == ("<img src=x>|pipe.example", "a.example"), audits[1]

    with tempfile.TemporaryDirectory() as tmp:
        summary_file = Path(tmp) / "summary.md"
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_STEP_SUMMARY": str(summary_file)}, clear=True), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            check.emit_mutable_container_report(audits, ["bad\n::error::injected"], "2026-11-24")
        stdout_text = out.getvalue()
        summary_text = summary_file.read_text(encoding="utf-8")
        assert all(not line.startswith("::error::") for line in stdout_text.splitlines()), stdout_text
        assert "::warning::" in stdout_text and "%25" in stdout_text, stdout_text
        assert "<script>" not in summary_text and "<img " not in summary_text, summary_text
        assert "&lt;script&gt;" in summary_text and "&#124;pipe" in summary_text, summary_text
        assert "<details><summary>" in summary_text and "<pre>" in summary_text, summary_text


def test_shared_cdn_guard_loon_and_qx() -> None:
    targets = check.shared_cdn_guard_targets()
    assert [(c, s) for c, s, _ in targets] == [
        ("loon", "Amazon"),
        ("loon", "Cloudflare"),
        ("qx", "Amazon"),
        ("qx", "Cloudflare"),
    ], targets

    # Loon: clean list with no-resolve and pure-domain compound rules passes.
    loon_ok = (
        "# comment\n"
        "DOMAIN-SUFFIX,cloudflare.com\n"
        "DOMAIN,dash.cloudflare.com\n"
        "DOMAIN-KEYWORD,cloudflare\n"
        "DOMAIN-REGEX,^cf\\.\n"
        "USER-AGENT,Cloudflare*\n"
        "URL-REGEX,^https://cf\n"
        "IP-CIDR,1.1.1.0/24,no-resolve\n"
        "IP-CIDR6,2606:4700::/32,no-resolve\n"
        "IP6-CIDR,2606:4700::/32,NO-RESOLVE\n"
        "AND,((DOMAIN,a.test),(DOMAIN,b.test))\n"
        "AND,((DOMAIN,a.test),(OR,((DOMAIN-SUFFIX,b.test),(NOT,((DOMAIN-KEYWORD,c))))))\n"
        "SUB-RULE,(DOMAIN-SUFFIX,sub.test),SubGroup\n"
    ).encode("utf-8")
    count, issues = check.check_loon_shared_cdn(loon_ok)
    assert count == 3 and issues == [], (count, issues)

    # Loon: missing no-resolve, IP-ASN, GEOIP, compound IP, unknown/malformed compound sub-rule, depth cap, unknown type all warn.
    deep_nested = "DOMAIN,a.test"
    for _ in range(1010):
        deep_nested = f"NOT,(({deep_nested}))"
    loon_bad = (
        "IP-CIDR,1.1.1.0/24\n"
        "IP-CIDR6,2606:4700::/32\n"
        "IP-ASN,13335,no-resolve\n"
        "GEOIP,US,no-resolve\n"
        "AND,((DOMAIN,a.test),(IP-CIDR,1.1.1.0/24))\n"
        "AND,((DOMAIN,a.test),(FUTURE-TYPE,x))\n"
        "AND,((DOMAIN,,),(DOMAIN,b.test))\n"
        "AND,((DOMAIN,a.test)\n"
        "SUB-RULE,(FUTURE-TYPE,x),SubGroup\n"
        "SUB-RULE,(DOMAIN,a.test),SubGroup)\n"
        f"{deep_nested}\n"
        "FUTURE-TYPE,foo\n"
    ).encode("utf-8")
    count, issues = check.check_loon_shared_cdn(loon_bad)
    assert count == 5 and len(issues) == 12, (count, issues)
    assert any("unrecognized sub-rule type 'FUTURE-TYPE'" in i for i in issues), issues
    assert any("malformed DOMAIN sub-rule" in i for i in issues), issues
    assert any("malformed SUB-RULE target" in i for i in issues), issues
    assert any("maximum nesting depth" in i for i in issues), issues
    assert any("malformed AND compound syntax" in i for i in issues), issues

    # QX (Opus C1): case-sensitive raw-line substring #out first, then classify surviving lines.
    qx_url = "https://cdn.example/Cloudflare.list#out=IP-CIDR+IP6-CIDR+IP-ASN&ntf=0"
    qx_ok = (
        "# comment\n"
        "HOST-SUFFIX,cloudflare.com,Cloudflare\n"
        "HOST,dash.cloudflare.com,Cloudflare\n"
        "HOST-KEYWORD,cloudflare,Cloudflare\n"
        "HOST-WILDCARD,*.cloudflare.com,Cloudflare\n"
        "HOST-REGEX,^cf\\.,Cloudflare\n"
        "USER-AGENT,Cloudflare*,Cloudflare\n"
        "IP-CIDR,1.1.1.0/24,Cloudflare\n"
        "IP-CIDR6,2606:4700::/32,Cloudflare\n"  # C1: contains "IP-CIDR" -> filtered by KOP #out!
        "IP6-CIDR,2606:4700::/32,Cloudflare\n"
        "IP-ASN,13335,Cloudflare\n"
    ).encode("utf-8")
    filtered, issues = check.check_qx_shared_cdn(qx_ok, qx_url)
    assert filtered == 4 and issues == [], (filtered, issues)

    # C1: lowercase ip-cidr is NOT excluded by case-sensitive #out and must warn; GEOIP and unknown also warn.
    qx_bad = (
        "ip-cidr,1.1.1.0/24,Cloudflare\n"
        "GEOIP,US,Cloudflare\n"
        "FUTURE-TYPE,<script>x</script>|pipe,Cloudflare\n"
    ).encode("utf-8")
    filtered, issues = check.check_qx_shared_cdn(qx_bad, qx_url)
    assert filtered == 0 and len(issues) == 3, (filtered, issues)
    assert "unfiltered IP-CIDR" in issues[0], issues
    assert "unfiltered GEOIP" in issues[1], issues
    assert "unrecognized rule type" in issues[2], issues

    # Without #out=, even uppercase IP-CIDR survives and warns.
    _, no_out_issues = check.check_qx_shared_cdn(b"IP-CIDR,1.1.1.0/24,Cloudflare\n", "https://cdn.example/Amazon.list")
    assert len(no_out_issues) == 1, no_out_issues

    # C3 escaping in emit_shared_cdn_report.
    res = check.Result(
        check.Resource(qx_url, "qx-resource", "quantumultx/bootstrap.example.conf:1"),
        True, 200, "text/plain", qx_url, len(qx_bad), "ok", qx_bad,
    )
    audits = check.shared_cdn_guard_report([res], [("qx", "Cloudflare", qx_url)])
    assert len(audits) == 1 and audits[0].state == "warn", audits
    with tempfile.TemporaryDirectory() as tmp:
        summary_file = Path(tmp) / "summary.md"
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_STEP_SUMMARY": str(summary_file)}, clear=True), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            check.emit_shared_cdn_report(audits, ["err\n::error::pwn"])
        stdout_text = out.getvalue()
        summary_text = summary_file.read_text(encoding="utf-8")
        assert all(not line.startswith("::error::") for line in stdout_text.splitlines()), stdout_text
        assert "<script>" not in summary_text and "&lt;script&gt;" in summary_text and "&#124;pipe" in summary_text, summary_text


def test_print_baselines_cli_exclusivity() -> None:
    for bad_argv in (["check", "--print-baselines", "--list"], ["check", "--print-baselines", "--mode", "full"]):
        with patch.object(sys, "argv", bad_argv), contextlib.redirect_stderr(io.StringIO()):
            try:
                check.parse_args()
            except SystemExit as exc:
                assert exc.code == 2, (bad_argv, exc.code)
            else:
                raise AssertionError(f"expected argparse error for {bad_argv}")

    # print_mutable_baselines reuses extract_resources() Resource objects and outputs valid YAML to stdout.
    sample_url = "https://upstream.example/sample.lpx"
    sample_res = check.Resource(sample_url, "loon-resource", "loon/bootstrap.example.conf:1")
    sample_body = b"[MitM]\nhostname = a.example\n"
    seen_sources: list[str] = []

    def fake_fetch(resource: check.Resource, mode: str, timeout: float, retries: int,
                   complete: bool = False, keep_body: bool = False) -> check.Result:
        seen_sources.append(resource.source)
        assert mode == "full" and keep_body is True and complete is False
        return check.Result(resource, True, 200, "text/plain", resource.url, len(sample_body), "ok", sample_body)

    with patch.object(check, "enabled_mutable_containers", return_value=[("loon", sample_url)]), \
         patch.object(check, "fetch", side_effect=fake_fetch), \
         contextlib.redirect_stdout(io.StringIO()) as out:
        code = check.print_mutable_baselines([sample_res], 1.0, 0, 1)
    assert code == 0 and seen_sources == ["loon/bootstrap.example.conf:1"], (code, seen_sources)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "printed.yaml"
        path.write_text(out.getvalue(), encoding="utf-8")
        _, entries = check.load_mutable_baselines(path)
        assert len(entries) == 1 and entries[0]["mitm_hostnames"] == ["a.example"], entries


def test_main_full_mode_keep_body_and_nonblocking_warnings() -> None:
    """Full mode requests keep_body for mutable containers & shared CDN lists, and drift/guard/loader issues never fail exit code."""
    _, baselines = check.load_mutable_baselines()
    cdn_targets = check.shared_cdn_guard_targets()
    mut_entry = baselines[0]
    cdn_client, _, cdn_url = cdn_targets[0]
    mut_res = check.Resource(
        str(mut_entry["url"]),
        "loon-resource" if mut_entry["client"] == "loon" else "qx-resource",
        "loon/bootstrap.example.conf:1" if mut_entry["client"] == "loon" else "quantumultx/bootstrap.example.conf:1",
    )
    cdn_res = check.Resource(
        cdn_url,
        "loon-resource" if cdn_client == "loon" else "qx-resource",
        "loon/bootstrap.example.conf:2" if cdn_client == "loon" else "quantumultx/bootstrap.example.conf:2",
    )
    seen_keep_body: dict[str, bool] = {}

    def fake_fetch(resource: check.Resource, mode: str, timeout: float, retries: int,
                   complete: bool = False, keep_body: bool = False) -> check.Result:
        seen_keep_body[resource.url] = keep_body
        if resource.url == mut_res.url:
            body = b"[MitM]\nhostname = drifted.example\n" if keep_body else b""
        else:
            body = b"IP-CIDR,1.1.1.0/24\n" if keep_body else b""
        return check.Result(resource, True, 200, "text/plain", resource.url, len(body), "ok", body)

    with patch.object(check, "extract_resources", return_value=[mut_res, cdn_res]), \
         patch.object(check, "script_update_targets", return_value=[]), \
         patch.object(check, "mihomo_content_report", return_value=([], [], [])), \
         patch.object(check, "reject_overlap_report", return_value=([], [], [])), \
         patch.object(check, "fetch", side_effect=fake_fetch), \
         patch.dict(os.environ, {}, clear=True), \
         contextlib.redirect_stdout(io.StringIO()) as out:
        with patch.object(sys, "argv", ["check", "--mode", "light"]):
            assert check.main() == 0
            assert seen_keep_body == {mut_res.url: False, cdn_res.url: False}, seen_keep_body
        seen_keep_body.clear()
        with patch.object(sys, "argv", ["check", "--mode", "full"]):
            assert check.main() == 0
            assert seen_keep_body == {mut_res.url: True, cdn_res.url: True}, seen_keep_body
            text = out.getvalue()
            assert "Mutable MitM container drift audit" in text and "[warn] mutable-container" in text, text
            assert "Shared CDN guard" in text and "[warn] shared-cdn" in text, text
        # Real corrupt baseline files on disk warn non-blockingly in main() without crashing.
        with tempfile.TemporaryDirectory() as tmp:
            bad_samples = (
                "version: 1\nobserved_date: '2026-11-24'\ncontainers:\n- client: [loon]\n  url: https://a.example/x\n  mitm_count: 0\n  mitm_sha256: " + "0" * 64 + "\n  scripts: []\n  other_urls_count: 0\n  other_urls_sha256: " + "0" * 64 + "\n  rules: []\n",
                "version: 1\nobserved_date: '2026-11-24'\ncontainers:\n- client: loon\n  url: 'https://['\n  mitm_count: 0\n  mitm_sha256: " + "0" * 64 + "\n  scripts: []\n  other_urls_count: 0\n  other_urls_sha256: " + "0" * 64 + "\n  rules: []\n",
                'version: 1\nobserved_date: \'2026-11-24\'\ncontainers:\n- client: loon\n  url: "https://test.example/\\uD800"\n  mitm_count: 0\n  mitm_sha256: ' + "0" * 64 + "\n  scripts: []\n  other_urls_count: 0\n  other_urls_sha256: " + "0" * 64 + "\n  rules: []\n",
                "version: 1\nobserved_date: '2026-11-24'\ncontainers:\n- client: loon\n  url: 'https://user:fixture-secret@test.example:badport/x'\n  mitm_count: 0\n  mitm_sha256: " + "0" * 64 + "\n  scripts: []\n  other_urls_count: 0\n  other_urls_sha256: " + "0" * 64 + "\n  rules: []\n",
                "version: 1\nobserved_date: '2026-11-24'\ncontainers:\n- client: loon\n  url: https://a.example/x\n  mitm_count: 0\n  mitm_sha256: " + "0" * 64 + "\n  scripts: []\n  other_urls_count: 0\n  other_urls_sha256: " + "0" * 64 + "\n  rules: []\n  'https://user:fixture-secret@test.example:badport/x': 1\n",
                'version: 1\nobserved_date: "2026-11-24"\ncontainers:\n- client: loon\n  url: "https://user:fixture-secret@test.example/path\\q"\n',
                "containers: " + "[" * 500 + "]" * 500 + "\n",
                "version: 1.0\nobserved_date: '2026-99-99'\ncontainers: []\n",
                "version: 1\nobserved_date: '2026-11-24'\ncontainers:\n- 1: bad\n  extra: key\n",
            )
            for idx, content in enumerate(bad_samples):
                bad_path = Path(tmp) / f"bad-{idx}.yaml"
                bad_path.write_text(content, encoding="utf-8")
                summary_path = Path(tmp) / f"summary-{idx}.md"
                with patch.object(check, "MUTABLE_BASELINES_FILE", bad_path), \
                     patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_path)}, clear=True), \
                     patch.object(sys, "argv", ["check", "--mode", "full"]), \
                     contextlib.redirect_stdout(io.StringIO()) as warn_out:
                    assert check.main() == 0, (idx, warn_out.getvalue())
                    out_text = warn_out.getvalue()
                    assert "failed to load mutable container baselines" in out_text, (idx, out_text)
                    assert "fixture-secret" not in out_text, (idx, out_text)
                    summary_text = summary_path.read_text(encoding="utf-8")
                    assert "fixture-secret" not in summary_text, (idx, summary_text)

            # Query-string credential in a baseline URL triggering container-set mismatch must be redacted everywhere (P2-1a).
            mismatch_path = Path(tmp) / "query-mismatch.yaml"
            mismatch_path.write_text(
                "version: 1\nobserved_date: '2026-11-24'\ncontainers:\n"
                "- client: loon\n"
                "  url: 'https://a.example/x?credential=fixture-value'\n"
                "  mitm_count: 0\n"
                f"  mitm_sha256: '{'0' * 64}'\n"
                "  scripts: []\n"
                "  other_urls_count: 0\n"
                f"  other_urls_sha256: '{'0' * 64}'\n"
                "  rules: []\n",
                encoding="utf-8",
            )
            mismatch_summary = Path(tmp) / "summary-mismatch.md"
            with patch.object(check, "MUTABLE_BASELINES_FILE", mismatch_path), \
                 patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_STEP_SUMMARY": str(mismatch_summary)}, clear=True), \
                 patch.object(sys, "argv", ["check", "--mode", "full"]), \
                 contextlib.redirect_stdout(io.StringIO()) as mismatch_out:
                assert check.main() == 0, mismatch_out.getvalue()
                out_text = mismatch_out.getvalue()
                summary_text = mismatch_summary.read_text(encoding="utf-8")
                assert "baseline container set differs" in out_text and "<redacted>" in out_text, out_text
                assert "fixture-value" not in out_text, out_text
                assert "fixture-value" not in summary_text and "&lt;redacted&gt;" in summary_text, summary_text


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
    assert {own + "amdc.js", own + "xiaohongshu-channels-v2.js", own + "wechat-mp.js",
            "https://raw.githubusercontent.com/wenbingkun/proxy-config/cf80e249924723800a4efd98bfc42dfbf724547b/quantumultx/scripts/umetrip-safe.js"} <= plugin_urls, plugin_urls
    # The generated Surge rewrite modules are treated like hosted plugins: only their script-path URLs.
    rewrite_scripts = {
        url for path in check.SURGE_REWRITE_MODULES for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#") for url in check.SCRIPT_PATH_RE.findall(line)
    }
    rewrite_urls = {r.url for r in resources if r.source.startswith("surge/modules/rewrite/")}
    assert len(check.SURGE_REWRITE_MODULES) == len(build_surge_modules.SOURCES), check.SURGE_REWRITE_MODULES
    assert rewrite_scripts and rewrite_urls == rewrite_scripts, rewrite_urls ^ rewrite_scripts
    # Hosted QX rewrites are scanned the same way: runtime-fetched scripts and echo-response bodies only.
    qx_rewrite_scripts = {
        match.group(1).rstrip(")]}")
        for path in check.QX_REWRITE_FILES
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.lstrip() and not line.lstrip().startswith(("#", ";"))
        for match in check.QX_REWRITE_RESOURCE_RE.finditer(line)
    }
    qx_rewrite_urls = {r.url for r in resources if r.source.startswith("quantumultx/rewrite/")}
    assert len(qx_rewrite_scripts) == 25, len(qx_rewrite_scripts)
    assert qx_rewrite_urls == qx_rewrite_scripts, qx_rewrite_urls ^ qx_rewrite_scripts
    for expected_url in (
        "https://raw.githubusercontent.com/wenbingkun/proxy-config/main/quantumultx/scripts/bilibili_json-safe.js",
        "https://raw.githubusercontent.com/app2smile/rules/df6366a7024e0b3f0aa3510c5b791eea6f3cba89/js/bilibili-proto.js",
        "https://raw.githubusercontent.com/yjqiang/surge_scripts/ae855a8935e8a77d81998bb503ee6158ca83faa73/scripts/bilibili/bilibili_dynamic.js".replace(
            "ae855a8935e8a77d81998bb503ee6158ca83faa73", "ae855a8935e8a770780c9cb11d6830176e895131"
        ),
        "https://raw.githubusercontent.com/fmz200/wool_scripts/5d5f63fcf98bc69d5f8f1b1bae6f86a01ee4bb97/Scripts/template/reject-dict.json",
        "https://raw.githubusercontent.com/wenbingkun/proxy-config/main/quantumultx/scripts/12306-ads.js",
    ):
        assert expected_url in qx_rewrite_urls, expected_url
    assert not any("biliad.png" in u or "soupian.icu" in u or "duckduckgo.com" in u for u in qx_rewrite_urls)

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
    test_jsdelivr_origin_url_mapping()
    test_jsdelivr_size_error_checks_origin()
    test_reject_overlap_size_error_checks_origin()
    test_warnings_do_not_fail_main()
    test_full_mode_fallback_keeps_provider_url()
    test_provider_content_types()
    test_mihomo_content_coverage()
    test_candidate_checkout_and_summary()
    test_content_main_exit_and_modes()
    test_full_download_must_be_complete()
    test_qx_rewrite_extraction_boundaries()
    test_keep_body_and_complete_boundaries()
    test_parse_mutable_container_syntax()
    test_mutable_container_baselines_offline()
    test_mutable_container_audit_drift_and_escaping()
    test_shared_cdn_guard_loon_and_qx()
    test_print_baselines_cli_exclusivity()
    test_main_full_mode_keep_body_and_nonblocking_warnings()

    print(
        f"Remote resource offline tests passed "
        f"({len(resources)} resources, {icon_count} icons)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
