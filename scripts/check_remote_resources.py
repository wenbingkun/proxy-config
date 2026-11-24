#!/usr/bin/env python3
"""Lightweight availability checks for externally hosted config resources."""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import hashlib
import http.client
import ipaddress
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

import build_rules
import build_surge_modules
import check_reject_conflicts


ROOT = Path(__file__).resolve().parent.parent
CLASH_CONFIG = ROOT / "mihomo" / "verge" / "config.yaml"
# Intentionally scans the whole quantumultx/rewrite/ directory, including retained
# historical compatibility files / conversion inputs (such as AmapPageCleanup.conf and
# UmetripAds.conf). Like hosted Loon/Surge containers, only runtime-fetched script
# and echo-response body URLs are extracted, not regex patterns, 302 targets or #!icon comments.
QX_REWRITE_FILES = tuple(sorted(p for p in (ROOT / "quantumultx" / "rewrite").glob("*") if p.is_file()))
QX_FILES = (
    ROOT / "quantumultx" / "bootstrap.example.conf",
    ROOT / "quantumultx" / "filter" / "repo.snippet",
) + QX_REWRITE_FILES
LOON_FILES = (ROOT / "loon" / "bootstrap.example.conf",) + tuple(
    sorted((ROOT / "loon" / "plugins").glob("*.plugin"))
)
# Generated from pinned upstream modules (build_surge_modules.py), one per app. Like a hosted Loon
# plugin, only the scripts they load are resources, other URLs are patterns or redirect targets.
SURGE_REWRITE_MODULES = tuple(sorted((ROOT / "surge" / "modules" / "rewrite").glob("*.sgmodule")))
SURGE_FILES = (ROOT / "surge" / "proxy-config.conf",) + tuple(
    sorted((ROOT / "surge" / "modules").glob("*.sgmodule"))
) + SURGE_REWRITE_MODULES
# The module whose RULE-SET lines are the pre-matching REJECT lists (see check_reject_conflicts).
SURGE_REJECT_MODULE = ROOT / "surge" / "modules" / "home-direct.sgmodule"
DEPLOY_SCRIPT = ROOT / "mihomo" / "shellcrash" / "deploy.sh"
UPSTREAMS_POLICY = ROOT / "rules" / "upstreams.yaml"
MUTABLE_BASELINES_FILE = ROOT / "rules" / "mutable_container_baselines.yaml"
MUTABLE_CONTAINER_FIELDS = {
    ("loon", "plugin"),
    ("qx", "rewrite_remote"),
    ("qx", "filter_remote"),
}

URL_RE = re.compile(r"https?://[^\s,\"']+")
# In a hosted Loon plugin only the scripts it loads are resources; other URLs are redirect targets.
SCRIPT_PATH_RE = re.compile(r"script-path=(https?://[^\s,\"']+)")
# Kept verbatim with check_upstreams.QX_SCRIPT_RE (copied locally to avoid circular import).
QX_REWRITE_RESOURCE_RE = re.compile(
    r'\burl(?:-and-header)?\s+(?:script-\S+|echo-response\s+\S+\s+echo-response)\s+(https?://[^\s,"\']+)',
    re.I,
)
HOSTNAME_LINE_RE = re.compile(r"^hostname\s*=\s*(.+)$", re.I)
CONTAINER_SCRIPT_PATTERNS = (
    re.compile(r'script-path\s*=\s*["\']?(https?://[^\s,"\']+)', re.I),
    re.compile(r'\bscript\(\s*["\'](https?://[^"\'\s]+)["\']', re.I),
    re.compile(r'\bjq_file\(\s*["\'](https?://[^"\'\s]+)["\']', re.I),
    re.compile(r'\bjq-path\s*=\s*["\']?(https?://[^\s,"\']+)', re.I),
    re.compile(r'\bdata-path\s*=\s*["\']?(https?://[^\s,"\']+)', re.I),
    QX_REWRITE_RESOURCE_RE,
)
PLAIN_URL_RE = re.compile(r'https?://[^\s,"\')\]}>]+', re.I)
QX_RULE_LINE_RE = re.compile(
    r"^(?:host|host-suffix|host-keyword|host-wildcard|host-regex|ip-cidr|ip6-cidr|ip-asn|geoip|user-agent)\s*,",
    re.I,
)
SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
INLINE_LIST_LIMIT = 20

LOON_SHARED_CDN_SAFE = {
    "DOMAIN",
    "DOMAIN-SUFFIX",
    "DOMAIN-KEYWORD",
    "DOMAIN-REGEX",
    "USER-AGENT",
    "URL-REGEX",
}
LOON_SHARED_CDN_EXACT_TWO = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD"}
LOON_SHARED_CDN_CIDR = {"IP-CIDR", "IP-CIDR6", "IP6-CIDR"}
LOON_SHARED_CDN_RESOLVING = {"IP-ASN", "GEOIP"}
LOON_SHARED_CDN_COMPOUND = {"AND", "OR", "NOT", "SUB-RULE"}
MAX_COMPOUND_DEPTH = 16
QX_SHARED_CDN_SAFE = {
    "HOST",
    "HOST-SUFFIX",
    "HOST-KEYWORD",
    "HOST-WILDCARD",
    "HOST-REGEX",
    "USER-AGENT",
}
QX_SHARED_CDN_IP = {"IP-CIDR", "IP-CIDR6", "IP6-CIDR", "IP-ASN", "GEOIP"}
SHELL_TEMPLATE_RE = re.compile(
    r"^DEFAULT_(?:DUAL|SINGLE)_TEMPLATE_URL=['\"](?P<url>https?://[^'\"]+)['\"]$",
    re.MULTILINE,
)
PLACEHOLDER_HOSTS = {"example.com", "your-provider.example", "proxy-config.invalid"}
OPERATIONAL_HOSTS = {
    "1.1.1.1",
    "1.12.12.12",
    "8.8.8.8",
    "119.29.29.29",
    "223.5.5.5",
    "dns.google",
    "doh.pub",
    "ip-api.com",
    "www.baidu.com",
    "www.gstatic.com",
}
LIGHT_BYTES = 4096
FULL_LIMIT = 16 * 1024 * 1024
USER_AGENT = "proxy-config-remote-check/1.0"
QX_USER_AGENT = "Quantumult X"
# kelee.one only serves requests that look like Loon on iOS (Loon plus CFNetwork/Darwin); this is
# a string verified to work, not a guess at the server's rule.
LOON_USER_AGENT = "Loon/3.5.2 (996) CFNetwork/3826 Darwin/25.0.0"
# jsDelivr serves GitHub files one by one; when that fails it falls back to the whole repository,
# which a large repo (blackmatrix7, ~4.5 GB) exceeds. Seen as a transient 403 that recovers by itself.
JSDELIVR_SIZE_MARKER = b"Package size exceeded the configured limit"


@dataclass(frozen=True)
class Resource:
    url: str
    kind: str
    source: str
    expected_format: str | None = None


@dataclass(frozen=True)
class Result:
    resource: Resource
    ok: bool
    status: int | None
    content_type: str
    final_url: str
    bytes_read: int
    message: str
    # Retain full provider/selected script responses for comparisons without another download.
    body: bytes = field(default=b"", repr=False)
    # Set when the check passed only through the origin fallback; reported but never fails the run.
    warning: str = ""


@dataclass(frozen=True)
class ProviderComparison:
    name: str
    ip_rules: int | None
    origin: str
    sha256: str
    message: str


@dataclass(frozen=True)
class ScriptTarget:
    name: str
    pinned: str
    candidate: str
    source: str


@dataclass(frozen=True)
class ScriptComparison:
    target: ScriptTarget
    state: str
    pinned_sha256: str
    candidate_sha256: str
    message: str


@dataclass(frozen=True)
class MutableContainerAudit:
    client: str
    url: str
    state: str
    summary: str
    details: tuple[str, ...] = ()
    current_mitm_hostnames: tuple[str, ...] | None = None
    current_other_urls: tuple[str, ...] | None = None


@dataclass(frozen=True)
class SharedCdnAudit:
    client: str
    service: str
    url: str
    state: str
    ip_rules: int | None
    issues: tuple[str, ...] = ()
    message: str = ""


def redact_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = "<redacted>" if parsed.query else ""
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


def jsdelivr_origin_url(url: str) -> str | None:
    """The same ref and path on raw.githubusercontent.com for a cdn.jsdelivr.net GitHub file URL."""
    parsed = urllib.parse.urlsplit(url)
    match = re.fullmatch(r"/gh/([^/@]+)/([^/@]+)@([^/]+)/(.+)", parsed.path)
    if parsed.scheme != "https" or parsed.hostname != "cdn.jsdelivr.net" or not match:
        return None
    owner, repo, ref, path = match.groups()
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"


def is_jsdelivr_size_error(url: str, exc: urllib.error.HTTPError) -> bool:
    if exc.code != 403 or jsdelivr_origin_url(url) is None:
        return False
    try:
        return JSDELIVR_SIZE_MARKER in exc.read(1024)
    except (OSError, http.client.HTTPException):
        return False


def is_skipped_url(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    lowered = url.lower()
    return (
        host in PLACEHOLDER_HOSTS
        or host in OPERATIONAL_HOSTS
        or "replace-me" in lowered
        or "__sub_url_" in lowered
    )


def infer_qx_kind(line: str, url: str) -> str:
    prefix = line[: line.find(url)].lower()
    path = urllib.parse.urlsplit(url).path.lower()
    if "img-url=" in prefix or "profile_img_url" in prefix:
        return "icon"
    if path.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
        return "icon"
    if path.endswith(".js"):
        return "script"
    return "qx-resource"


def source_policy_error(url: str) -> str | None:
    """Recovered Moyu code must be reproducible; Kelee's native Loon sources stay allowed."""
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host == "ddgksf2013.top" or host.endswith(".ddgksf2013.top"):
        return "Moyu's personal-site resources must use a reviewed repository snapshot"
    path = urllib.parse.unquote(parsed.path)
    if host == "raw.githubusercontent.com" and path.lower().startswith("/ddgksf2013/"):
        if parsed.scheme != "https" or not re.fullmatch(r"/ddgksf2013/[^/]+/[0-9a-f]{40}/.+", path, re.I):
            return "Moyu GitHub resources must use a full commit SHA"
    elif host.endswith("jsdelivr.net") and path.lower().startswith("/gh/ddgksf2013/"):
        if parsed.scheme != "https" or not re.fullmatch(r"/gh/ddgksf2013/[^/@]+@[0-9a-f]{40}/.+", path, re.I):
            return "Moyu jsDelivr resources must use a full commit SHA"
    elif host in {"github.com", "www.github.com"} and path.lower().startswith("/ddgksf2013/"):
        if parsed.scheme != "https" or not re.fullmatch(r"/ddgksf2013/[^/]+/(?:raw|blob)/[0-9a-f]{40}/.+", path, re.I):
            return "Moyu GitHub download URLs must use a full commit SHA"
    return None


def resource_kind(path: Path, line: str, url: str) -> str:
    kind = infer_qx_kind(line, url)
    if path in LOON_FILES:
        return kind.replace("qx-", "loon-")
    if path in SURGE_FILES:
        return "surge-rule" if "RULE-SET," in line else kind.replace("qx-", "surge-")
    return kind


def client_of(source: str) -> str:
    """Resources may answer differently per client, so each client checks its own copy."""
    if source.startswith("quantumultx/"):
        return "qx"
    if source.startswith("loon/"):
        return "loon"
    if source.startswith("surge/"):
        return "surge"
    return "default"


def extract_resources() -> list[Resource]:
    resources: dict[tuple[str, str], Resource] = {}

    clash = yaml.safe_load(CLASH_CONFIG.read_text(encoding="utf-8")) or {}
    providers = clash.get("rule-providers", {})
    if not isinstance(providers, dict):
        raise ValueError("mihomo/verge/config.yaml: rule-providers must be a mapping")
    for name, provider in providers.items():
        if not isinstance(provider, dict) or not isinstance(provider.get("url"), str):
            raise ValueError(f"mihomo/verge/config.yaml: rule-provider {name!r} has no URL")
        url = provider["url"]
        if not is_skipped_url(url):
            resources[(url, "default")] = Resource(
                url=url,
                kind="clash-rule",
                source=f"mihomo/verge/config.yaml:rule-providers.{name}",
                expected_format=provider.get("format", "yaml"),
            )

    for path in QX_FILES + LOON_FILES + SURGE_FILES:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.lstrip()
            if not stripped or stripped.startswith(("#", ";")):
                continue
            if path in QX_REWRITE_FILES:
                pattern, group = QX_REWRITE_RESOURCE_RE, 1
            elif path.parent == ROOT / "loon" / "plugins" or path in SURGE_REWRITE_MODULES:
                pattern, group = SCRIPT_PATH_RE, 1
            else:
                pattern, group = URL_RE, 0
            for match in pattern.finditer(line):
                url = match.group(group).rstrip(")]}")
                if is_skipped_url(url):
                    continue
                source = f"{path.relative_to(ROOT)}:{lineno}"
                resources.setdefault(
                    (url, client_of(source)),
                    Resource(
                        url=url,
                        kind=resource_kind(path, line, url),
                        source=source,
                    ),
                )

    deploy_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    for match in SHELL_TEMPLATE_RE.finditer(deploy_text):
        url = match.group("url")
        resources.setdefault(
            (url, "default"),
            Resource(url=url, kind="shellcrash-template", source="mihomo/shellcrash/deploy.sh"),
        )

    return sorted(resources.values(), key=lambda item: (item.kind, item.url, item.source))


FULL_CHECK_KINDS = {"clash-rule", "surge-rule"}


def looks_like_html(content_type: str, body: bytes) -> bool:
    prefix = body.lstrip()[:256].lower()
    return "text/html" in content_type or prefix.startswith((b"<!doctype html", b"<html"))


def validate_body(resource: Resource, content_type: str, body: bytes, mode: str) -> str | None:
    if not body:
        return "empty response body"
    if resource.kind == "icon":
        if not content_type.startswith("image/"):
            return f"icon returned non-image Content-Type {content_type or '<missing>'}"
        return None
    if looks_like_html(content_type, body):
        return "resource returned HTML instead of config content"
    if mode != "full" or resource.kind not in FULL_CHECK_KINDS:
        return None

    text = body.decode("utf-8-sig", errors="replace")
    if resource.kind == "surge-rule":
        # The repo fails any line it cannot validate (Surge itself skips it with a warning).
        try:
            if not check_reject_conflicts.validate_surge_ruleset(text, redact_url(resource.url)):
                return "Surge rule set has no rules"
        except check_reject_conflicts.InputError as exc:
            return f"invalid Surge rule set: {exc}"
        return None
    if resource.expected_format == "yaml":
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            return f"invalid YAML: {exc.__class__.__name__}"
        if not isinstance(parsed, dict) or not isinstance(parsed.get("payload"), list):
            return "YAML rule provider has no payload list"
    elif resource.expected_format == "text":
        useful = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith(("#", "!"))]
        if not useful:
            return "text rule provider has no usable entries"
    return None


def user_agent_for(resource: Resource) -> str:
    """Match the client used to fetch resources with conditional responses."""
    client = client_of(resource.source)
    if client == "qx":
        return QX_USER_AGENT
    if client == "loon":
        return LOON_USER_AGENT
    return USER_AGENT


def read_response(url: str, headers: dict[str, str], timeout: float, limit: int) -> tuple[int, str, bytes, str]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(limit + 1)
        # A truncated 200 can leave a valid YAML/text prefix. Never classify it as a full list.
        length = response.headers.get("Content-Length")
        if limit == FULL_LIMIT and length is not None:
            if not re.fullmatch(r"[0-9]+", length) or len(body) < min(int(length), limit + 1):
                raise OSError("invalid Content-Length or incomplete HTTP response body")
        return (
            response.status,
            response.headers.get_content_type().lower(),
            body,
            response.geturl(),
        )


def fetch(
    resource: Resource,
    mode: str,
    timeout: float,
    retries: int,
    complete: bool = False,
    keep_body: bool = False,
) -> Result:
    problem = source_policy_error(resource.url)
    if problem:
        return Result(resource, False, None, "", resource.url, 0, problem)
    full_rule_check = mode == "full" and resource.kind in FULL_CHECK_KINDS
    full_check = full_rule_check or complete or keep_body
    limit = FULL_LIMIT if full_check else LIGHT_BYTES
    headers = {"User-Agent": user_agent_for(resource), "Accept": "*/*"}
    if not full_check:
        headers["Range"] = f"bytes=0-{LIGHT_BYTES - 1}"
    last_error = "unknown error"

    for attempt in range(retries + 1):
        size_error = False
        try:
            status, content_type, body, final_url = read_response(resource.url, headers, timeout, limit)
            if not body and "Range" in headers:
                # cdn.jsdelivr.net has intermittently answered a ranged GET with an
                # empty body while a plain GET of the same URL returned the file, so
                # retry once without Range and still read only the first bytes.
                plain_headers = {key: value for key, value in headers.items() if key != "Range"}
                status, content_type, body, final_url = read_response(resource.url, plain_headers, timeout, limit)
            if not 200 <= status < 300:
                last_error = f"HTTP {status}"
            elif full_check and status != 200:
                last_error = f"full check requires HTTP 200, got {status}"
            elif full_check and len(body) > limit:
                last_error = f"resource exceeds {FULL_LIMIT // (1024 * 1024)} MiB full-check limit"
            else:
                body = body[:limit]
                problem = source_policy_error(final_url) or validate_body(resource, content_type, body, mode)
                if complete:
                    if final_url != resource.url:
                        problem = "script comparison URL redirected to a different version or source"
                    try:
                        body.decode("utf-8-sig")
                    except UnicodeError:
                        problem = "script comparison returned invalid UTF-8"
                return Result(
                    resource=resource,
                    ok=problem is None,
                    status=status,
                    content_type=content_type,
                    final_url=final_url,
                    bytes_read=len(body),
                    message=problem or "ok",
                    body=body if complete or keep_body or (full_rule_check and resource.kind == "clash-rule") else b"",
                )
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            status = exc.code
            size_error = is_jsdelivr_size_error(resource.url, exc)
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            last_error = f"{exc.__class__.__name__}: {exc.reason if isinstance(exc, urllib.error.URLError) else exc}"
            status = None
        if attempt < retries:
            time.sleep(0.5 * (attempt + 1))

    origin_url = jsdelivr_origin_url(resource.url)
    if size_error and not complete and origin_url:
        # Check the file itself at the origin: a deleted or renamed upstream file still fails.
        origin = fetch(replace(resource, url=origin_url), mode, timeout, retries, keep_body=keep_body)
        note = f"jsDelivr HTTP 403 package size fallback; origin {redact_url(origin_url)}: {origin.message}"
        if origin.ok:
            return replace(origin, resource=resource, warning=note)
        last_error = note

    return Result(
        resource=resource,
        ok=False,
        status=status,
        content_type="",
        final_url=resource.url,
        bytes_read=0,
        message=last_error,
    )


def script_update_targets() -> list[ScriptTarget]:
    """Only the parser and YouTube pair; version URLs stay in their configuration sources."""
    text = QX_FILES[0].read_text(encoding="utf-8")
    urls = re.findall(r"^resource_parser_url\s*=\s*(\S+)\s*$", text, re.M)
    if len(urls) != 1:
        raise ValueError("expected one QX resource_parser_url")
    parser = re.fullmatch(r"(https://cdn\.jsdelivr\.net/gh/[^/@]+/[^/@]+)@([0-9a-f]{40})(/[^?#]+\.js)", urls[0])
    if parser is None:
        raise ValueError("QX parser comparison needs an original CDN full-commit URL")
    targets = [ScriptTarget("KOP parser", urls[0], f"{parser[1]}@master{parser[3]}", "quantumultx/bootstrap.example.conf")]
    sources = [source for source in build_surge_modules.SOURCES if source["file"] == "youtube"]
    if len(sources) != 1:
        raise ValueError("expected one YouTube source")
    pins = sources[0].get("pins", {})
    if len(pins) != 2:
        raise ValueError("YouTube comparison needs two script pins")
    for candidate, pinned in sorted(pins.items()):
        match = re.fullmatch(r"(https://raw\.githubusercontent\.com/[^/]+/[^/]+)/([0-9a-f]{40})(/[^?#]+\.js)", pinned)
        if match is None or candidate != f"{match[1]}/master{match[3]}":
            raise ValueError("YouTube comparison needs full-commit pins with same-path master candidates")
        targets.append(ScriptTarget(f"Maasea {match[3].rsplit('/', 1)[-1]}", pinned, candidate,
                                    "surge/modules/rewrite/youtube.sgmodule"))
    return targets


def script_update_report(results: list[Result], targets: list[ScriptTarget], timeout: float,
                         retries: int, workers: int) -> list[ScriptComparison]:
    """A candidate is just a byte-change notice; never execute it or upgrade configurations."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(fetch, Resource(t.candidate, "script", t.source), "full", timeout,
                                   retries, complete=True) for t in targets]
        candidates = [future.result() for future in futures]
    comparisons = []
    for target, candidate in zip(targets, candidates):
        pinned = next((r for r in results if r.resource.url == target.pinned
                       and client_of(r.resource.source) == client_of(target.source)), None)
        pinned_sha = hashlib.sha256(pinned.body).hexdigest() if pinned and pinned.ok and pinned.body else ""
        candidate_sha = hashlib.sha256(candidate.body).hexdigest() if candidate.ok and candidate.body else ""
        if not pinned_sha or not candidate_sha:
            error = ("pinned resource missing or incomplete" if pinned is None else
                     f"pinned: {pinned.message if not pinned.ok else 'complete body unavailable'}" if not pinned_sha else
                     f"candidate: {candidate.message if not candidate.ok else 'complete body unavailable'}")
            comparisons.append(ScriptComparison(target, "not compared", pinned_sha, candidate_sha, error))
            continue
        assert pinned is not None
        message = f"bytes {len(pinned.body)} -> {len(candidate.body)}"
        state = "unchanged" if pinned.body == candidate.body else "changed"
        if state == "changed":
            offset = next((i for i, (a, b) in enumerate(zip(pinned.body, candidate.body)) if a != b),
                          min(len(pinned.body), len(candidate.body)))
            message += f"; first differing byte {offset}; review candidate manually, no automatic upgrade"
        comparisons.append(ScriptComparison(target, state, pinned_sha, candidate_sha, message))
    return comparisons


def emit_script_report(comparisons: list[ScriptComparison], errors: list[str]) -> None:
    print("Pinned script content updates (byte comparison; changes do not prove an App failure):")
    rows = ["", "## Pinned script content updates", "",
            "Only actual file bytes are compared; candidates require manual review and device regression.", "",
            "| Script | Result | Pinned SHA-256 | Candidate SHA-256 | Detail |",
            "| --- | --- | --- | --- | --- |"]
    for result in comparisons:
        print(f"  [{result.state}] {result.target.name}: {result.message}; "
              f"pinned sha256={result.pinned_sha256 or '-'}; candidate sha256={result.candidate_sha256 or '-'}")
        print(f"    pinned: {result.target.pinned}\n    candidate: {result.target.candidate}")
        rows.append(f"| {result.target.name} | {result.state} | {result.pinned_sha256 or '-'} | "
                    f"{result.candidate_sha256 or '-'} | {result.message.replace('|', '&#124;').replace(chr(10), ' ')} |")
    for result in comparisons:
        rows.append(f"\n{result.target.name} pinned: {result.target.pinned}\n\nCandidate: {result.target.candidate}\n")
    for error in errors:
        print(f"  [not compared] {error}")
        rows.append(f"\nNot compared: {error.replace('|', '&#124;').replace(chr(10), ' ')}\n")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write("\n".join(rows) + "\n")


DESTINATION_IP_TYPES = {"IP-CIDR", "IP-CIDR6", "IP-ASN", "GEOIP"}
CLASSICAL_NON_IP_TYPES = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "DOMAIN-REGEX", "PROCESS-NAME"}


def provider_ip_count(body: bytes, behavior: str, fmt: str) -> int:
    """Classify supported provider entries, failing closed on unknown syntax.

    This is a content/coverage check, not a replacement for the core's rule parser.
    Numeric domain patterns remain domains; inner no-resolve does not remove IP rules.
    """
    if not isinstance(behavior, str) or behavior not in {"domain", "ipcidr", "classical"}:
        raise ValueError(f"unsupported behavior {behavior!r}")
    text = body.decode("utf-8-sig")
    if fmt == "yaml":
        document = yaml.safe_load(text)
        if not isinstance(document, dict) or not isinstance(document.get("payload"), list):
            raise ValueError("expected YAML payload list")
        entries = document["payload"]
    elif fmt == "text":
        entries = [line.strip() for line in text.splitlines()
                   if line.strip() and not line.lstrip().startswith(("#", "!"))]
    else:
        raise ValueError(f"unsupported format {fmt!r}")
    count = 0
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(f"entry {index}: expected nonempty string")
        entry = entry.strip()
        if behavior == "domain":
            if not re.fullmatch(r"[A-Za-z0-9_.*+\-]+", entry):
                raise ValueError(f"entry {index}: unsupported domain pattern")
            continue
        if behavior == "ipcidr":
            if "/" not in entry:
                raise ValueError(f"entry {index}: expected CIDR")
            ipaddress.ip_network(entry, strict=False)
            count += 1
            continue
        parts = [part.strip() for part in entry.split(",")]
        kind = parts[0]
        if kind not in DESTINATION_IP_TYPES | CLASSICAL_NON_IP_TYPES:
            raise ValueError(f"entry {index}: unsupported classical type {kind!r}")
        if len(parts) not in {2, 3} or not parts[1]:
            raise ValueError(f"entry {index}: unsupported classical fields")
        if len(parts) == 3 and (kind not in DESTINATION_IP_TYPES or parts[2] != "no-resolve"):
            raise ValueError(f"entry {index}: unsupported classical modifier")
        if kind in {"IP-CIDR", "IP-CIDR6"}:
            if "/" not in parts[1]:
                raise ValueError(f"entry {index}: expected CIDR")
            network = ipaddress.ip_network(parts[1], strict=False)
            if network.version != (4 if kind == "IP-CIDR" else 6):
                raise ValueError(f"entry {index}: CIDR address family mismatch")
        elif kind == "IP-ASN":
            if not re.fullmatch(r"[0-9]+", parts[1]) or int(parts[1]) > 0xFFFFFFFF:
                raise ValueError(f"entry {index}: invalid ASN")
        elif kind == "GEOIP" and not re.fullmatch(r"(?:[A-Za-z]{2}|LAN)", parts[1]):
            raise ValueError(f"entry {index}: invalid GEOIP code")
        count += kind in DESTINATION_IP_TYPES
    return count


def checkout_provider_path(url: str) -> Path | None:
    """Only this repo's own mutable generated rules use the candidate checkout."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.query or parsed.fragment:
        return None
    prefix = "/gh/wenbingkun/proxy-config@main/mihomo/rules/"
    if parsed.netloc != "cdn.jsdelivr.net" or not parsed.path.startswith(prefix):
        return None
    filename = parsed.path[len(prefix):]
    if not re.fullmatch(r"[a-z0-9_]+\.yaml", filename):
        raise ValueError("unsupported checkout provider path")
    return ROOT / "mihomo" / "rules" / filename


def mihomo_content_report(
    results: list[Result], config: dict | None = None,
) -> tuple[list[ProviderComparison], list[str], list[str]]:
    """Compare each provider definition, including aliases sharing a download URL."""
    if config is None:
        config = yaml.safe_load(CLASH_CONFIG.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("rule-providers"), dict):
        raise ValueError("expected rule-providers mapping")
    providers = config["rule-providers"]
    if not all(isinstance(name, str) and name for name in providers):
        raise ValueError("expected nonempty provider names")
    rules = config.get("rules")
    if not isinstance(rules, list) or not all(isinstance(rule, str) for rule in rules):
        raise ValueError("expected rules string list")
    geoip = next((i for i, rule in enumerate(rules) if rule.startswith("GEOIP,CN,")), None)
    if geoip is None:
        raise ValueError("missing terminal GEOIP,CN rule")
    stage = set()
    for index, rule in enumerate(rules):
        if not rule.startswith("RULE-SET,"):
            continue
        parts = [part.strip() for part in rule.split(",")]
        if len(parts) not in {3, 4} or (len(parts) == 4 and parts[3] != "no-resolve"):
            raise ValueError(f"rule {index + 1}: unsupported RULE-SET syntax")
        if parts[1] not in providers:
            raise ValueError(f"rule {index + 1}: undefined provider {parts[1]!r}")
        if index < geoip and len(parts) == 3:
            stage.add(parts[1])
    downloads = {result.resource.url: result for result in results if result.resource.kind == "clash-rule"}
    comparisons = []
    for name, provider in providers.items():
        origin = "unavailable"
        digest = ""
        try:
            if not isinstance(name, str) or not isinstance(provider, dict):
                raise ValueError("invalid provider definition")
            if provider.get("type") != "http" or not isinstance(provider.get("url"), str):
                raise ValueError("unsupported provider transport or missing URL")
            url = provider["url"]
            path = checkout_provider_path(url)
            if path is not None:
                origin = f"checkout:{path.relative_to(ROOT)}"
                body = path.read_bytes()
                if len(body) > FULL_LIMIT:
                    raise ValueError("checkout provider exceeds full-check limit")
            else:
                origin = f"http:{redact_url(url)}"
                result = downloads.get(url)
                if result is None or not result.ok:
                    raise ValueError("download unavailable" if result is None else f"download failed: {result.message}")
                body = result.body
                if not body:
                    raise ValueError("full response body unavailable")
            digest = hashlib.sha256(body).hexdigest()
            count = provider_ip_count(body, provider.get("behavior"), provider.get("format", "yaml"))
            comparisons.append(ProviderComparison(name, count, origin, digest, "compared"))
        except (ValueError, UnicodeError, yaml.YAMLError, OSError) as exc:
            comparisons.append(ProviderComparison(name, None, origin, digest, f"not compared: {exc}"))
    missing = [r.name for r in comparisons if r.ip_rules and r.name not in stage]
    stale = [r.name for r in comparisons if r.ip_rules == 0 and r.name in stage]
    return comparisons, missing, stale


def emit_mihomo_report(comparisons: list[ProviderComparison], missing: list[str], stale: list[str]) -> None:
    unknown = sum(r.ip_rules is None for r in comparisons)
    heading = (f"Mihomo IP coverage: compared {len(comparisons) - unknown}/{len(comparisons)}; "
               f"not compared={unknown}; missing IP-stage references={len(missing)}; "
               f"IP-stage providers without current IP rules={len(stale)}")
    print(heading)
    for result in comparisons:
        status = "not compared" if result.ip_rules is None else f"IP rules={result.ip_rules}"
        print(f"  [{status}] {result.name}: {result.origin}; sha256={result.sha256 or '-'}; {result.message}")
    for name in missing:
        print(f"  [fail] {name}: destination IP rules lack an IP-stage reference")
    for name in stale:
        print(f"  [warn] {name}: IP stage has no current destination IP rules; review, do not auto-remove")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        rows = ["## Mihomo provider IP coverage", "", heading, "",
                "This compares current provider contents and configured IP coverage; it does not test runtime DNS/routing.", "",
                "| Provider | Destination IP rules | Source | SHA-256 | Result |",
                "|---|---:|---|---|---|"]
        for result in comparisons:
            verdict = result.message
            if result.name in missing:
                verdict = "FAIL: missing IP-stage reference"
            elif result.name in stale:
                verdict = "WARN: no current IP rules; keep until reviewed"
            values = (result.name, "not compared" if result.ip_rules is None else str(result.ip_rules),
                      result.origin, result.sha256 or "-", verdict)
            rows.append("| " + " | ".join(escape_markdown_cell(value) for value in values) + " |")
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write("\n".join(rows) + "\n")


def surge_reject_urls() -> list[str]:
    """The REJECT RULE-SET URLs of the Surge home module (not its reject_allow.list)."""
    urls = []
    for line in SURGE_REJECT_MODULE.read_text(encoding="utf-8").splitlines():
        if line.startswith("AND,((RULE-SET,"):
            urls.append(line[len("AND,((RULE-SET,"):].split(",", 1)[0])
    return urls


def reject_overlap_report(timeout: float) -> tuple[list[str], list[str], list[str]]:
    """Return (failures, overlap warnings, CDN fallback warnings).

    Overlaps only warn; download and parse problems fail.
    """
    failures: list[str] = []
    warnings: list[str] = []
    cdn_warnings: list[str] = []
    urls = surge_reject_urls()
    if not urls:
        return [f"no REJECT RULE-SET lines in {SURGE_REJECT_MODULE.relative_to(ROOT)}"], [], []
    try:
        repo = check_reject_conflicts.load_repo()
    except check_reject_conflicts.InputError as exc:
        return [f"repo rules: {exc}"], [], []
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    errors = (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException)
    for url in urls:
        cdn_note = ""
        try:
            try:
                status, _, body, _ = read_response(url, headers, timeout, FULL_LIMIT)
            except urllib.error.HTTPError as exc:
                if not is_jsdelivr_size_error(url, exc):
                    raise
                origin_url = jsdelivr_origin_url(url) or ""
                fallback = f"jsDelivr HTTP 403 package size fallback; origin {redact_url(origin_url)}"
                try:
                    status, _, body, _ = read_response(origin_url, headers, timeout, FULL_LIMIT)
                except errors as origin_exc:
                    failures.append(f"{redact_url(url)}: {fallback}: {origin_exc.__class__.__name__}: {origin_exc}")
                    continue
                cdn_note = f"{redact_url(url)}: {fallback}: HTTP {status}"
        except errors as exc:
            failures.append(f"{redact_url(url)}: {exc.__class__.__name__}: {exc}")
            continue
        if not 200 <= status < 300 or len(body) > FULL_LIMIT:
            failures.append(f"{redact_url(url)}: HTTP {status} or over {FULL_LIMIT} bytes")
            continue
        try:
            rules, _ = check_reject_conflicts.parse_rules(body.decode("utf-8-sig"), redact_url(url))
        except (check_reject_conflicts.InputError, UnicodeDecodeError) as exc:
            failures.append(f"{redact_url(url)}: {exc}")
            continue
        if cdn_note:
            cdn_warnings.append(cdn_note)
        out = check_reject_conflicts.analyse(repo, rules)
        for kind in ("definite", "partial", "possible", "unanalysed"):
            for source, repo_rule, reject_rule in out[kind]:
                # The module's NOT(reject_allow.list) keeps these bank/broker hosts unblocked.
                note = " [excluded by reject_allow.list]" if source in build_rules.SURGE_REJECT_ALLOW_SOURCES else ""
                warnings.append(f"{kind}: {reject_rule} over {repo_rule} ({source}) in {redact_url(url)}{note}")
    return failures, warnings, cdn_warnings


def escape_html(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_markdown_cell(value: str) -> str:
    return escape_html(value).replace("|", "&#124;").replace("\n", " ").replace("\r", " ")


def sanitize_line(value: str) -> str:
    """Keep upstream-controlled text on one line so it cannot start a stdout workflow command."""
    return value.replace("\r", "\\r").replace("\n", "\\n")


def annotation(message: str) -> str:
    """Escape a GitHub Actions workflow command message."""
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _iter_conf_section_lines(path: Path):
    section = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip().lower()
            continue
        yield section, stripped


def _is_entry_enabled(line: str) -> bool:
    for part in line.split(",")[1:]:
        normalized = re.sub(r"\s*=\s*", "=", part.strip(), count=1).lower()
        if normalized == "enabled=false":
            return False
    return True


def _strip_fragment(url: str) -> str:
    return url.split("#", 1)[0]


def _sha256_lines(values: list[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def enabled_mutable_containers(root: Path | None = None) -> list[tuple[str, str]]:
    """Derive default-enabled QX/Loon mutable MitM containers intersecting rules/upstreams.yaml."""
    base = ROOT if root is None else root
    policy_path = UPSTREAMS_POLICY if root is None else base / "rules" / "upstreams.yaml"
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    exceptions = policy.get("exceptions")
    if not isinstance(exceptions, list):
        raise ValueError("rules/upstreams.yaml: exceptions must be a list")
    allowed: set[tuple[str, str]] = set()
    for item in exceptions:
        if not isinstance(item, dict):
            continue
        client = item.get("client")
        field_name = item.get("field")
        url = item.get("url")
        if (client, field_name) in MUTABLE_CONTAINER_FIELDS and isinstance(url, str):
            allowed.add((client, _strip_fragment(url.strip())))

    found: set[tuple[str, str]] = set()
    qx_conf = base / "quantumultx" / "bootstrap.example.conf"
    for section, line in _iter_conf_section_lines(qx_conf):
        if section not in {"rewrite_remote", "filter_remote"}:
            continue
        if not line.lower().startswith(("https://", "http://")) or not _is_entry_enabled(line):
            continue
        url = line.split(",", 1)[0].strip()
        if section == "filter_remote":
            path = urllib.parse.urlsplit(url).path
            if not ("/rewrite/" in path and path.endswith(".snippet")):
                continue
        if ("qx", _strip_fragment(url)) in allowed:
            found.add(("qx", url))

    loon_conf = base / "loon" / "bootstrap.example.conf"
    for section, line in _iter_conf_section_lines(loon_conf):
        if section != "plugin":
            continue
        if not line.lower().startswith(("https://", "http://")) or not _is_entry_enabled(line):
            continue
        url = line.split(",", 1)[0].strip()
        if ("loon", _strip_fragment(url)) in allowed:
            found.add(("loon", url))

    return sorted(found)


def parse_mutable_container(body: bytes, client: str) -> dict[str, object]:
    """Extract MitM hostnames, script/jq/data URLs, fallback plain URLs and routing rules."""
    if client not in {"qx", "loon"}:
        raise ValueError(f"unsupported container client {client!r}")
    text = body.decode("utf-8-sig")
    section = ""
    hostnames: set[str] = set()
    scripts: set[str] = set()
    plain_urls: set[str] = set()
    rules: set[str] = set()

    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip().lower()
            continue

        host_match = HOSTNAME_LINE_RE.match(stripped)
        if host_match:
            raw_hosts = re.sub(r"^(?:%APPEND%|%INSERT%)\s*,?\s*", "", host_match.group(1).strip(), flags=re.I)
            for token in raw_hosts.split(","):
                cleaned = token.strip().lower()
                if cleaned and cleaned not in {"%append%", "%insert%"}:
                    hostnames.add(cleaned)

        for pattern in CONTAINER_SCRIPT_PATTERNS:
            for match in pattern.finditer(stripped):
                scripts.add(match.group(1).rstrip(")]}"))

        for match in PLAIN_URL_RE.finditer(stripped):
            plain_urls.add(match.group(0).rstrip(")]}"))

        if client == "loon" and section == "rule":
            rules.add(",".join(part.strip() for part in stripped.split(",")))
        elif client == "qx" and QX_RULE_LINE_RE.match(stripped):
            rules.add(",".join(part.strip() for part in stripped.split(",")))

    mitm_hostnames = sorted(hostnames)
    scripts_sorted = sorted(scripts)
    other_urls = sorted(plain_urls - scripts)
    rules_sorted = sorted(rules)
    return {
        "mitm_hostnames": mitm_hostnames,
        "mitm_count": len(mitm_hostnames),
        "mitm_sha256": _sha256_lines(mitm_hostnames),
        "scripts": scripts_sorted,
        "other_urls": other_urls,
        "other_urls_count": len(other_urls),
        "other_urls_sha256": _sha256_lines(other_urls),
        "rules": rules_sorted,
    }


def _ensure_utf8(value: str, label: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{label}: string is not valid UTF-8") from None


def _validate_sorted_str_list(value: object, label: str, lowercase: bool = False) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{label}: expected list of nonempty strings")
    for item in value:
        _ensure_utf8(item, label)
    if lowercase and any(item != item.lower() for item in value):
        raise ValueError(f"{label}: expected lowercase entries")
    if value != sorted(set(value)):
        raise ValueError(f"{label}: must be sorted and deduplicated")
    return value


def _yaml_error_summary(exc: yaml.YAMLError) -> str:
    """Summarize a PyYAML error with class and line/column only, never echoing raw source lines."""
    mark = getattr(exc, "problem_mark", None)
    loc = f" at line {mark.line + 1}, column {mark.column + 1}" if mark is not None else ""
    return f"invalid YAML ({exc.__class__.__name__}{loc})"


def load_mutable_baselines(path: Path | None = None) -> tuple[str, list[dict[str, object]]]:
    """Load and validate observed drift baselines for default-enabled mutable containers."""
    target = MUTABLE_BASELINES_FILE if path is None else path
    try:
        doc = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{target}: {_yaml_error_summary(exc)}") from None
    except RecursionError:
        raise ValueError(f"{target}: invalid YAML (nesting too deep)") from None
    if (
        not isinstance(doc, dict)
        or not all(isinstance(key, str) for key in doc)
        or set(doc) != {"version", "observed_date", "containers"}
    ):
        raise ValueError(f"{target}: expected top-level string keys version, observed_date, containers")
    if type(doc["version"]) is not int or doc["version"] != 1:
        raise ValueError(f"{target}: unsupported baseline version")
    observed_date = doc["observed_date"]
    if not isinstance(observed_date, str) or not DATE_ISO_RE.fullmatch(observed_date):
        raise ValueError(f"{target}: observed_date must be a YYYY-MM-DD string")
    _ensure_utf8(observed_date, f"{target}: observed_date")
    try:
        datetime.date.fromisoformat(observed_date)
    except ValueError:
        raise ValueError(f"{target}: invalid calendar date") from None
    containers = doc["containers"]
    if not isinstance(containers, list):
        raise ValueError(f"{target}: containers must be a list")

    required_keys = {
        "client",
        "url",
        "mitm_count",
        "mitm_sha256",
        "scripts",
        "other_urls_count",
        "other_urls_sha256",
        "rules",
    }
    optional_keys = {"mitm_hostnames", "other_urls"}
    seen: set[tuple[str, str]] = set()
    validated: list[dict[str, object]] = []
    for index, entry in enumerate(containers, 1):
        label = f"{target}: entry {index}"
        if not isinstance(entry, dict):
            raise ValueError(f"{label} must be a mapping")
        if not all(isinstance(key, str) for key in entry):
            raise ValueError(f"{label} keys must all be strings")
        for key in entry:
            _ensure_utf8(key, f"{label} key")
        keys = set(entry)
        missing = sorted(required_keys - keys)
        unknown = keys - (required_keys | optional_keys)
        if missing or unknown:
            raise ValueError(f"{label} invalid keys (missing={missing}, unknown_count={len(unknown)})")
        client = entry["client"]
        url = entry["url"]
        if not isinstance(client, str) or client not in {"qx", "loon"}:
            raise ValueError(f"{label} unsupported client")
        if not isinstance(url, str) or not url.strip() or any(ch.isspace() for ch in url):
            raise ValueError(f"{label} url must be a non-empty HTTPS URL without whitespace")
        _ensure_utf8(url, f"{label} url")
        try:
            parsed_url = urllib.parse.urlsplit(url)
            _ = parsed_url.port
        except ValueError:
            raise ValueError(f"{label} invalid https URL") from None
        if (
            parsed_url.scheme != "https"
            or not parsed_url.hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
        ):
            raise ValueError(f"{label} url must be a valid https:// URL without credentials")
        key = (client, url)
        if key in seen:
            raise ValueError(f"{label} duplicates an earlier baseline entry")
        seen.add(key)

        for count_key in ("mitm_count", "other_urls_count"):
            val = entry[count_key]
            if type(val) is not int or val < 0:
                raise ValueError(f"{label} {count_key} must be a non-negative integer")
        for hash_key in ("mitm_sha256", "other_urls_sha256"):
            val = entry[hash_key]
            if not isinstance(val, str) or not SHA256_HEX_RE.fullmatch(val):
                raise ValueError(f"{label} {hash_key} must be a 64-hex SHA-256")

        _validate_sorted_str_list(entry["scripts"], f"{label} scripts")
        _validate_sorted_str_list(entry["rules"], f"{label} rules")

        if "mitm_hostnames" in entry:
            hosts = _validate_sorted_str_list(entry["mitm_hostnames"], f"{label} mitm_hostnames", lowercase=True)
            if len(hosts) != entry["mitm_count"] or _sha256_lines(hosts) != entry["mitm_sha256"]:
                raise ValueError(f"{label} mitm_hostnames disagrees with mitm_count/mitm_sha256")
        if "other_urls" in entry:
            others = _validate_sorted_str_list(entry["other_urls"], f"{label} other_urls")
            if len(others) != entry["other_urls_count"] or _sha256_lines(others) != entry["other_urls_sha256"]:
                raise ValueError(f"{label} other_urls disagrees with other_urls_count/other_urls_sha256")
        validated.append(entry)
    return observed_date, validated


def _format_set_diff(label: str, expected: list[str], actual: list[str]) -> str | None:
    expected_set, actual_set = set(expected), set(actual)
    if expected_set == actual_set:
        return None
    added = sorted(actual_set - expected_set)
    removed = sorted(expected_set - actual_set)
    parts: list[str] = []
    if added:
        parts.append("+" + ", ".join(sanitize_line(item) for item in added))
    if removed:
        parts.append("-" + ", ".join(sanitize_line(item) for item in removed))
    return f"{label} changed ({'; '.join(parts)})"


def mutable_container_audit(
    results: list[Result], baselines: list[dict[str, object]],
) -> list[MutableContainerAudit]:
    """Compare fetched mutable containers against observed baselines without failing the run."""
    by_key = {(client_of(result.resource.source), result.resource.url): result for result in results}
    audits: list[MutableContainerAudit] = []
    for base in baselines:
        client = str(base["client"])
        url = str(base["url"])
        result = by_key.get((client, url))
        if result is None:
            audits.append(MutableContainerAudit(client, url, "not audited", "resource not present in fetch results"))
            continue
        if not result.ok or not result.body:
            reason = f"download failed: {result.message}" if not result.ok else "full response body unavailable"
            audits.append(MutableContainerAudit(client, url, "not audited", sanitize_line(reason)))
            continue
        try:
            actual = parse_mutable_container(result.body, client)
        except (ValueError, UnicodeError) as exc:
            audits.append(MutableContainerAudit(client, url, "not audited", f"parse failed: {sanitize_line(str(exc))}"))
            continue

        diffs: list[str] = []
        current_mitm: tuple[str, ...] | None = None
        current_other: tuple[str, ...] | None = None

        if actual["mitm_count"] != base["mitm_count"] or actual["mitm_sha256"] != base["mitm_sha256"]:
            if "mitm_hostnames" in base:
                diff_msg = _format_set_diff("mitm", list(base["mitm_hostnames"]), list(actual["mitm_hostnames"]))
                diffs.append(
                    diff_msg or f"mitm count {base['mitm_count']} -> {actual['mitm_count']}, "
                    f"sha256 {str(base['mitm_sha256'])[:12]} -> {str(actual['mitm_sha256'])[:12]}"
                )
            else:
                diffs.append(
                    f"mitm count {base['mitm_count']} -> {actual['mitm_count']}, "
                    f"sha256 {str(base['mitm_sha256'])[:12]} -> {str(actual['mitm_sha256'])[:12]}"
                )
                current_mitm = tuple(actual["mitm_hostnames"])

        script_diff = _format_set_diff("scripts", list(base["scripts"]), list(actual["scripts"]))
        if script_diff:
            diffs.append(script_diff)

        if actual["other_urls_count"] != base["other_urls_count"] or actual["other_urls_sha256"] != base["other_urls_sha256"]:
            if "other_urls" in base:
                other_diff = _format_set_diff("other_urls", list(base["other_urls"]), list(actual["other_urls"]))
                diffs.append(
                    other_diff or f"other_urls count {base['other_urls_count']} -> {actual['other_urls_count']}, "
                    f"sha256 {str(base['other_urls_sha256'])[:12]} -> {str(actual['other_urls_sha256'])[:12]}"
                )
            else:
                diffs.append(
                    f"other_urls count {base['other_urls_count']} -> {actual['other_urls_count']}, "
                    f"sha256 {str(base['other_urls_sha256'])[:12]} -> {str(actual['other_urls_sha256'])[:12]}"
                )
                current_other = tuple(actual["other_urls"])

        rules_diff = _format_set_diff("rules", list(base["rules"]), list(actual["rules"]))
        if rules_diff:
            diffs.append(rules_diff)

        if not diffs:
            summary = (
                f"mitm={actual['mitm_count']}, scripts={len(actual['scripts'])}, "
                f"other_urls={actual['other_urls_count']}, rules={len(actual['rules'])}"
            )
            audits.append(MutableContainerAudit(client, url, "unchanged", summary))
        else:
            audits.append(
                MutableContainerAudit(
                    client,
                    url,
                    "changed",
                    f"{len(diffs)} field(s) drifted",
                    tuple(diffs),
                    current_mitm,
                    current_other,
                )
            )
    return audits


def emit_mutable_container_report(
    audits: list[MutableContainerAudit], errors: list[str], observed_date: str = "",
) -> None:
    changed = sum(item.state == "changed" for item in audits)
    skipped = sum(item.state == "not audited" for item in audits)
    date_note = f"; observed baseline={observed_date}" if observed_date else ""
    heading = (
        f"Mutable MitM container drift audit (warning-only{date_note}): "
        f"{len(audits) - changed - skipped}/{len(audits)} unchanged; changed={changed}; not audited={skipped}"
    )
    print(heading)
    for item in audits:
        safe_url = sanitize_line(redact_url(item.url))
        if item.state == "unchanged":
            print(f"  [unchanged] {item.client} {safe_url}: {sanitize_line(item.summary)}")
        else:
            detail_text = "; ".join(sanitize_line(d) for d in item.details) if item.details else sanitize_line(item.summary)
            msg = f"mutable-container {item.client} {safe_url}: {item.state}: {detail_text}"
            print(f"  [warn] {msg}")
            if os.environ.get("GITHUB_ACTIONS") == "true":
                print(f"::warning::{annotation(msg)}")
    for error in errors:
        msg = f"mutable-container: {sanitize_line(error)}"
        print(f"  [warn] {msg}")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::warning::{annotation(msg)}")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        rows = [
            "",
            "## Mutable MitM container drift audit (warning-only)",
            "",
            escape_html(heading),
            "",
            "| Client | Container | Result | Detail |",
            "|---|---|---|---|",
        ]
        for item in audits:
            detail = "; ".join(item.details) if item.details else item.summary
            values = (item.client, redact_url(item.url), item.state, detail)
            rows.append("| " + " | ".join(escape_markdown_cell(v) for v in values) + " |")
        for error in errors:
            rows.append(f"\nWarning: {escape_html(error)}\n")
        for item in audits:
            if item.current_mitm_hostnames is not None:
                title = escape_html(
                    f"Current MitM hostnames for {item.client} {redact_url(item.url)} "
                    f"({len(item.current_mitm_hostnames)})"
                )
                body = escape_html("\n".join(item.current_mitm_hostnames))
                rows.append(f"\n<details><summary>{title}</summary>\n\n<pre>{body}</pre>\n\n</details>")
            if item.current_other_urls is not None:
                title = escape_html(
                    f"Current other_urls for {item.client} {redact_url(item.url)} "
                    f"({len(item.current_other_urls)})"
                )
                body = escape_html("\n".join(item.current_other_urls))
                rows.append(f"\n<details><summary>{title}</summary>\n\n<pre>{body}</pre>\n\n</details>")
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write("\n".join(rows) + "\n")


def shared_cdn_guard_targets(root: Path | None = None) -> list[tuple[str, str, str]]:
    """Return enabled Loon and QX Cloudflare/Amazon list targets as (client, service, url)."""
    base = ROOT if root is None else root
    targets: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()

    loon_conf = base / "loon" / "bootstrap.example.conf"
    for section, line in _iter_conf_section_lines(loon_conf):
        if section != "remote rule" or not line.lower().startswith(("https://", "http://")):
            continue
        if not _is_entry_enabled(line):
            continue
        url = line.split(",", 1)[0].strip()
        match = re.search(r"/rule/Loon/(Cloudflare|Amazon)/\1\.list(?:#.*)?$", url)
        if match and ("loon", url) not in seen:
            seen.add(("loon", url))
            targets.append(("loon", match.group(1), url))

    qx_conf = base / "quantumultx" / "bootstrap.example.conf"
    for section, line in _iter_conf_section_lines(qx_conf):
        if section != "filter_remote" or not line.lower().startswith(("https://", "http://")):
            continue
        if not _is_entry_enabled(line):
            continue
        url = line.split(",", 1)[0].strip()
        match = re.search(r"/rule/QuantumultX/(Cloudflare|Amazon)/\1\.list(?:#.*)?$", url)
        if match and ("qx", url) not in seen:
            seen.add(("qx", url))
            targets.append(("qx", match.group(1), url))

    return sorted(targets)


def _split_top_level_parenthesized(body: str) -> list[str] | None:
    """Parse `(item1),(item2),...` into inner strings; return None on unbalanced or malformed syntax."""
    items: list[str] = []
    i = 0
    n = len(body)
    while i < n:
        while i < n and body[i].isspace():
            i += 1
        if i >= n or body[i] != "(":
            return None
        depth = 0
        start = i + 1
        while i < n:
            if body[i] == "(":
                depth += 1
            elif body[i] == ")":
                depth -= 1
                if depth == 0:
                    items.append(body[start:i].strip())
                    i += 1
                    break
            i += 1
        else:
            return None
        while i < n and body[i].isspace():
            i += 1
        if i < n:
            if body[i] != ",":
                return None
            i += 1
            while i < n and body[i].isspace():
                i += 1
            if i >= n:
                return None
    return items if items else None


def _is_valid_loon_safe_leaf(kind_upper: str, expr: str, rest: str) -> bool:
    if not rest or rest.startswith(","):
        return False
    if kind_upper in LOON_SHARED_CDN_EXACT_TWO:
        parts = [part.strip() for part in expr.split(",")]
        return len(parts) == 2 and bool(parts[1])
    return any(part.strip() for part in rest.split(","))


def _check_loon_compound_expr(expr: str, depth: int = 0) -> tuple[int, list[str]]:
    """Recursively validate a Loon compound rule; allow only pure-domain/non-resolving sub-rules."""
    if depth > MAX_COMPOUND_DEPTH:
        return 0, [f"compound rule exceeds maximum nesting depth ({MAX_COMPOUND_DEPTH})"]
    kind, sep, rest = expr.partition(",")
    kind_upper = kind.strip().upper()
    rest = rest.strip()
    if not sep or not rest:
        return 0, [f"malformed compound sub-rule: {sanitize_line(expr)}"]
    if kind_upper in LOON_SHARED_CDN_SAFE:
        if not _is_valid_loon_safe_leaf(kind_upper, expr, rest):
            return 0, [f"malformed {kind_upper} sub-rule"]
        return 0, []
    if kind_upper in LOON_SHARED_CDN_CIDR or kind_upper in LOON_SHARED_CDN_RESOLVING:
        return 1, [f"compound rule contains {kind_upper} predicate"]
    if kind_upper in {"AND", "OR", "NOT"}:
        outer = _split_top_level_parenthesized(rest)
        if outer is None or len(outer) != 1:
            return 0, [f"malformed {kind_upper} compound syntax"]
        subrules = _split_top_level_parenthesized(outer[0])
        if subrules is None or (kind_upper == "NOT" and len(subrules) != 1):
            return 0, [f"malformed {kind_upper} sub-rule list"]
        ip_total = 0
        problems: list[str] = []
        for sub in subrules:
            sub_ip, sub_problems = _check_loon_compound_expr(sub, depth + 1)
            ip_total += sub_ip
            problems.extend(sub_problems)
        return ip_total, problems
    if kind_upper == "SUB-RULE":
        if not rest.startswith("("):
            return 0, ["malformed SUB-RULE syntax"]
        depth_count = 0
        end_idx = -1
        for idx, ch in enumerate(rest):
            if ch == "(":
                depth_count += 1
            elif ch == ")":
                depth_count -= 1
                if depth_count == 0:
                    end_idx = idx
                    break
        if end_idx == -1:
            return 0, ["malformed SUB-RULE parentheses"]
        cond = rest[1:end_idx].strip()
        tail = rest[end_idx + 1 :].strip()
        target = tail[1:].strip() if tail.startswith(",") else ""
        if not target or any(ch in target for ch in ",()") or any(ch.isspace() for ch in target):
            return 0, ["malformed SUB-RULE target"]
        return _check_loon_compound_expr(cond, depth + 1)
    return 0, [f"unrecognized sub-rule type {sanitize_line(kind.strip())!r}"]


def check_loon_shared_cdn(body: bytes) -> tuple[int, list[str]]:
    """Check Loon Cloudflare/Amazon lists: CIDRs need no-resolve; IP-ASN/GEOIP/unknown fail closed."""
    text = body.decode("utf-8-sig")
    ip_rules = 0
    issues: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        parts = [part.strip() for part in stripped.split(",")]
        kind = parts[0].upper()
        if kind in LOON_SHARED_CDN_SAFE:
            _, sep, rest = stripped.partition(",")
            if not sep or not _is_valid_loon_safe_leaf(kind, stripped, rest.strip()):
                issues.append(f"line {lineno}: malformed {kind} rule: {sanitize_line(stripped)}")
            continue
        if kind in LOON_SHARED_CDN_CIDR:
            ip_rules += 1
            modifiers = {part.lower() for part in parts[2:]}
            if "no-resolve" not in modifiers:
                issues.append(f"line {lineno}: {kind} missing no-resolve: {sanitize_line(stripped)}")
        elif kind in LOON_SHARED_CDN_RESOLVING:
            ip_rules += 1
            issues.append(f"line {lineno}: {kind} rule in shared CDN list: {sanitize_line(stripped)}")
        elif kind in LOON_SHARED_CDN_COMPOUND:
            sub_ip, sub_problems = _check_loon_compound_expr(stripped)
            ip_rules += sub_ip
            if sub_problems:
                detail = "; ".join(sub_problems)
                issues.append(f"line {lineno}: {detail}: {sanitize_line(stripped)}")
        else:
            issues.append(f"line {lineno}: unrecognized rule type {sanitize_line(parts[0])!r}: {sanitize_line(stripped)}")
    return ip_rules, issues


def _qx_out_tokens(url: str) -> list[str]:
    """Extract case-sensitive `#out=A+B+C` exclusion tokens as KOP resource-parser.js parses them."""
    fragment = urllib.parse.urlsplit(url).fragment
    tokens: list[str] = []
    for seg in fragment.split("&"):
        if seg.startswith("out="):
            tokens.extend(tok for tok in seg[4:].split("+") if tok)
    return tokens


def check_qx_shared_cdn(body: bytes, url: str) -> tuple[int, list[str]]:
    """Simulate KOP `#out` case-sensitive raw-line substring filtering, then classify surviving rules."""
    text = body.decode("utf-8-sig")
    out_tokens = _qx_out_tokens(url)
    filtered_count = 0
    issues: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        # KOP resource-parser.js Rule_Handle() runs case-sensitive substring matching on the raw line.
        if out_tokens and any(token in stripped for token in out_tokens):
            filtered_count += 1
            continue
        parts = [part.strip() for part in stripped.split(",")]
        kind = parts[0].upper()
        if kind in QX_SHARED_CDN_SAFE:
            continue
        if kind in QX_SHARED_CDN_IP:
            issues.append(f"line {lineno}: unfiltered {kind} rule survives #out: {sanitize_line(stripped)}")
        else:
            issues.append(f"line {lineno}: unrecognized rule type {sanitize_line(parts[0])!r}: {sanitize_line(stripped)}")
    return filtered_count, issues


def shared_cdn_guard_report(
    results: list[Result], targets: list[tuple[str, str, str]],
) -> list[SharedCdnAudit]:
    """Audit Loon/QX shared CDN lists for DNS-resolving or unfiltered IP rules (warning-only)."""
    by_key = {(client_of(result.resource.source), result.resource.url): result for result in results}
    audits: list[SharedCdnAudit] = []
    for client, service, url in targets:
        result = by_key.get((client, url))
        if result is None:
            audits.append(SharedCdnAudit(client, service, url, "not audited", None, message="resource not present in fetch results"))
            continue
        if not result.ok or not result.body:
            reason = f"download failed: {result.message}" if not result.ok else "full response body unavailable"
            audits.append(SharedCdnAudit(client, service, url, "not audited", None, message=sanitize_line(reason)))
            continue
        try:
            if client == "loon":
                ip_count, issues = check_loon_shared_cdn(result.body)
                ok_msg = f"ok ({ip_count} CIDR rule(s), all no-resolve)"
            else:
                ip_count, issues = check_qx_shared_cdn(result.body, url)
                ok_msg = f"ok ({ip_count} line(s) excluded by #out, 0 surviving IP/unknown rules)"
        except (ValueError, UnicodeError) as exc:
            audits.append(SharedCdnAudit(client, service, url, "not audited", None, message=f"parse failed: {sanitize_line(str(exc))}"))
            continue
        if issues:
            audits.append(SharedCdnAudit(client, service, url, "warn", ip_count, tuple(issues), f"{len(issues)} issue(s)"))
        else:
            audits.append(SharedCdnAudit(client, service, url, "ok", ip_count, (), ok_msg))
    return audits


def emit_shared_cdn_report(audits: list[SharedCdnAudit], errors: list[str]) -> None:
    warned = sum(item.state == "warn" for item in audits)
    skipped = sum(item.state == "not audited" for item in audits)
    heading = (
        f"Shared CDN guard (Loon/QX Cloudflare & Amazon, warning-only): "
        f"{len(audits) - warned - skipped}/{len(audits)} ok; warnings={warned}; not audited={skipped}"
    )
    print(heading)
    for item in audits:
        safe_url = sanitize_line(redact_url(item.url))
        if item.state == "ok":
            print(f"  [ok] {item.client} {item.service} {safe_url}: {sanitize_line(item.message)}")
        elif item.state == "not audited":
            msg = f"shared-cdn {item.client} {item.service} {safe_url}: not audited: {sanitize_line(item.message)}"
            print(f"  [warn] {msg}")
            if os.environ.get("GITHUB_ACTIONS") == "true":
                print(f"::warning::{annotation(msg)}")
        else:
            preview = "; ".join(sanitize_line(i) for i in item.issues[:5])
            if len(item.issues) > 5:
                preview += f" (+{len(item.issues) - 5} more)"
            msg = f"shared-cdn {item.client} {item.service} {safe_url}: {len(item.issues)} issue(s): {preview}"
            print(f"  [warn] {msg}")
            if os.environ.get("GITHUB_ACTIONS") == "true":
                print(f"::warning::{annotation(msg)}")
    for error in errors:
        msg = f"shared-cdn: {sanitize_line(error)}"
        print(f"  [warn] {msg}")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::warning::{annotation(msg)}")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        rows = [
            "",
            "## Shared CDN guard (Loon/QX Cloudflare & Amazon, warning-only)",
            "",
            escape_html(heading),
            "",
            "| Client | Service | Result | IP/Excluded | Detail |",
            "|---|---|---|---:|---|",
        ]
        for item in audits:
            detail = "; ".join(item.issues[:5]) if item.issues else item.message
            if len(item.issues) > 5:
                detail += f" (+{len(item.issues) - 5} more)"
            values = (
                item.client,
                item.service,
                item.state,
                "-" if item.ip_rules is None else str(item.ip_rules),
                detail,
            )
            rows.append("| " + " | ".join(escape_markdown_cell(v) for v in values) + " |")
        for error in errors:
            rows.append(f"\nWarning: {escape_html(error)}\n")
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write("\n".join(rows) + "\n")


def print_mutable_baselines(
    resources: list[Resource], timeout: float, retries: int, workers: int,
) -> int:
    """Fetch current enabled mutable containers and print candidate baseline YAML to stdout only."""
    targets = enabled_mutable_containers()
    resource_by_key = {(client_of(r.source), r.url): r for r in resources}
    selected: list[tuple[str, Resource]] = []
    missing = [key for key in targets if key not in resource_by_key]
    if missing:
        print(f"Missing extracted resources for mutable containers: {missing}", file=sys.stderr)
        return 1
    for client, url in targets:
        selected.append((client, resource_by_key[(client, url)]))

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch, resource, "full", timeout, retries, keep_body=True): (client, resource)
            for client, resource in selected
        }
        fetched: dict[tuple[str, str], Result] = {}
        for future in concurrent.futures.as_completed(futures):
            client, resource = futures[future]
            fetched[(client, resource.url)] = future.result()

    entries: list[dict[str, object]] = []
    for client, resource in selected:
        result = fetched[(client, resource.url)]
        if not result.ok or not result.body:
            print(f"Failed to fetch {client} {redact_url(resource.url)}: {result.message}", file=sys.stderr)
            return 1
        try:
            parsed = parse_mutable_container(result.body, client)
        except (ValueError, UnicodeError) as exc:
            print(f"Failed to parse {client} {redact_url(resource.url)}: {exc}", file=sys.stderr)
            return 1
        entry: dict[str, object] = {
            "client": client,
            "url": resource.url,
            "mitm_count": parsed["mitm_count"],
            "mitm_sha256": parsed["mitm_sha256"],
        }
        if int(parsed["mitm_count"]) <= INLINE_LIST_LIMIT:
            entry["mitm_hostnames"] = parsed["mitm_hostnames"]
        entry["scripts"] = parsed["scripts"]
        entry["other_urls_count"] = parsed["other_urls_count"]
        entry["other_urls_sha256"] = parsed["other_urls_sha256"]
        if int(parsed["other_urls_count"]) <= INLINE_LIST_LIMIT:
            entry["other_urls"] = parsed["other_urls"]
        entry["rules"] = parsed["rules"]
        entries.append(entry)

    document = {
        "version": 1,
        "observed_date": time.strftime("%Y-%m-%d"),
        "containers": entries,
    }
    header = (
        "# Observed baselines for default-enabled mutable MitM containers (for drift detection in\n"
        "# weekly `check_remote_resources.py --mode full`, not a line-by-line upstream code audit).\n"
        "# Print a candidate refresh (stdout only; review diff manually before editing) with:\n"
        "#   python3 scripts/check_remote_resources.py --print-baselines\n"
    )
    sys.stdout.write(header + yaml.safe_dump(document, sort_keys=False, allow_unicode=True))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("light", "full"), default=None)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--list", action="store_true", help="list extracted resources without network access")
    parser.add_argument(
        "--print-baselines",
        action="store_true",
        help="fetch default-enabled mutable containers and print candidate baseline YAML to stdout",
    )
    args = parser.parse_args()
    if args.print_baselines and (args.list or args.mode is not None):
        parser.error("--print-baselines cannot be combined with --list or --mode")
    if args.mode is None:
        args.mode = "light"
    return args


def main() -> int:
    args = parse_args()
    if args.workers < 1 or args.timeout <= 0 or args.retries < 0:
        raise SystemExit("workers and timeout must be positive; retries cannot be negative")

    resources = extract_resources()
    if not resources:
        raise SystemExit("no remote resources found")
    if args.list:
        for resource in resources:
            print(f"{resource.kind:20} {redact_url(resource.url)}  ({resource.source})")
        print(f"Listed {len(resources)} unique remote resources.")
        return 0
    if args.print_baselines:
        return print_mutable_baselines(resources, args.timeout, args.retries, args.workers)

    targets: list[ScriptTarget] = []
    script_failures: list[str] = []
    mutable_baselines: list[dict[str, object]] = []
    mutable_observed_date = ""
    mutable_errors: list[str] = []
    cdn_targets: list[tuple[str, str, str]] = []
    cdn_guard_errors: list[str] = []
    body_keys: set[tuple[str, str]] = set()
    if args.mode == "full":
        try:
            targets = script_update_targets()
        except (ValueError, OSError) as exc:
            script_failures = [f"not compared: {exc}"]
        try:
            mutable_observed_date, mutable_baselines = load_mutable_baselines()
            for entry in mutable_baselines:
                body_keys.add((str(entry["url"]), str(entry["client"])))
            expected_containers = set(enabled_mutable_containers())
            baseline_containers = {(str(entry["client"]), str(entry["url"])) for entry in mutable_baselines}
            if expected_containers != baseline_containers:
                missing_redacted = sorted((c, redact_url(u)) for c, u in (expected_containers - baseline_containers))
                extra_redacted = sorted((c, redact_url(u)) for c, u in (baseline_containers - expected_containers))
                mutable_errors.append(
                    f"baseline container set differs from enabled mutable containers "
                    f"(missing={missing_redacted}, extra={extra_redacted})"
                )
        except yaml.YAMLError as exc:
            mutable_errors.append(f"failed to load mutable container baselines: {_yaml_error_summary(exc)}")
        except RecursionError:
            mutable_errors.append("failed to load mutable container baselines: invalid YAML (nesting too deep)")
        except (ValueError, OSError) as exc:
            mutable_errors.append(f"failed to load mutable container baselines: {exc}")
        try:
            cdn_targets = shared_cdn_guard_targets()
            for client, _, url in cdn_targets:
                body_keys.add((url, client))
        except (ValueError, OSError) as exc:
            cdn_guard_errors.append(f"failed to resolve shared CDN guard targets: {exc}")

    pinned_urls = {target.pinned for target in targets}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                fetch,
                resource,
                args.mode,
                args.timeout,
                args.retries,
                complete=resource.url in pinned_urls,
                **({"keep_body": True} if (resource.url, client_of(resource.source)) in body_keys else {}),
            ): resource
            for resource in resources
        }
        results = [future.result() for future in concurrent.futures.as_completed(futures)]

    failures = sorted((result for result in results if not result.ok), key=lambda item: item.resource.url)
    overlap_failures: list[str] = []
    content_failures: list[str] = []
    cdn_warnings: list[str] = []
    if args.mode == "full":
        script_comparisons = script_update_report(results, targets, args.timeout, args.retries, args.workers)
        emit_script_report(script_comparisons, script_failures)
        script_failures += [f"{r.target.name}: not compared: {r.message}" for r in script_comparisons
                            if r.state == "not compared"]
        try:
            comparisons, missing, stale = mihomo_content_report(results)
            emit_mihomo_report(comparisons, missing, stale)
            content_failures = [f"{r.name}: {r.message}" for r in comparisons if r.ip_rules is None]
            content_failures += [f"{name}: destination IP rules lack an IP-stage reference" for name in missing]
        except (ValueError, UnicodeError, yaml.YAMLError, OSError) as exc:
            content_failures = [f"not compared: {exc}"]
        overlap_failures, overlap_warnings, cdn_warnings = reject_overlap_report(args.timeout)
        if overlap_warnings:
            excluded = sum("[excluded by reject_allow.list]" in w for w in overlap_warnings)
            print(f"Surge REJECT lists: {len(overlap_warnings)} candidate overlaps with repo-routed rules before "
                  f"the reject_allow.list exclusion ({excluded} excluded, {len(overlap_warnings) - excluded} "
                  "still blocked; warning only, today's lists, see surge/README.md):")
            for warning in overlap_warnings:
                print(f"  [warn] {warning}")
        mutable_audits = mutable_container_audit(results, mutable_baselines)
        emit_mutable_container_report(mutable_audits, mutable_errors, mutable_observed_date)
        shared_cdn_audits = shared_cdn_guard_report(results, cdn_targets)
        emit_shared_cdn_report(shared_cdn_audits, cdn_guard_errors)
    counts: dict[str, int] = {}
    for result in results:
        counts[result.resource.kind] = counts.get(result.resource.kind, 0) + 1

    warnings = [
        f"{result.resource.kind} {redact_url(result.resource.url)}: {result.warning} ({result.resource.source})"
        for result in sorted(results, key=lambda item: item.resource.url) if result.warning
    ]
    warnings += [f"surge-reject-list {message}" for message in cdn_warnings]
    if warnings:
        print("Remote resource warnings (jsDelivr package size fallback; the same file passed at the origin):")
        for message in warnings:
            print(f"  [warn] {message}")
            if os.environ.get("GITHUB_ACTIONS") == "true":
                print(f"::warning::{annotation(message)}")

    if failures or overlap_failures or content_failures or script_failures:
        print("Remote resource check failed:")
        for result in failures:
            print(
                f"  [fail] {result.resource.kind} {redact_url(result.resource.url)}: "
                f"{result.message} ({result.resource.source})"
            )
        for message in overlap_failures:
            print(f"  [fail] surge-reject-overlap {message}")
        for message in content_failures:
            print(f"  [fail] mihomo-content {message}")
        for message in script_failures:
            print(f"  [fail] script-update {message}")
        print(f"Failed {len(failures)} of {len(results)} resources; {len(overlap_failures)} REJECT list errors; "
              f"{len(content_failures)} Mihomo content/coverage errors; {len(script_failures)} script comparison errors.")
        return 1

    summary = ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items()))
    noted = f"; {len(warnings)} warnings" if warnings else ""
    print(f"Remote resources OK ({len(results)} unique; {summary}; mode={args.mode}{noted}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
