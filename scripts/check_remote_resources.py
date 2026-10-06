#!/usr/bin/env python3
"""Lightweight availability checks for externally hosted config resources."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import ipaddress
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

import build_rules
import check_reject_conflicts


ROOT = Path(__file__).resolve().parent.parent
CLASH_CONFIG = ROOT / "mihomo" / "verge" / "config.yaml"
QX_FILES = (
    ROOT / "quantumultx" / "bootstrap.example.conf",
    ROOT / "quantumultx" / "filter" / "repo.snippet",
)
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

URL_RE = re.compile(r"https?://[^\s,\"']+")
# In a hosted Loon plugin only the scripts it loads are resources; other URLs are redirect targets.
SCRIPT_PATH_RE = re.compile(r"script-path=(https?://[^\s,\"']+)")
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
    # Retain full Mihomo responses for classification; do not download providers twice.
    body: bytes = field(default=b"", repr=False)


@dataclass(frozen=True)
class ProviderComparison:
    name: str
    ip_rules: int | None
    origin: str
    sha256: str
    message: str


def redact_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = "<redacted>" if parsed.query else ""
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


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
            if not stripped or stripped.startswith("#"):
                continue
            is_plugin = path.parent == ROOT / "loon" / "plugins" or path in SURGE_REWRITE_MODULES
            for match in (SCRIPT_PATH_RE if is_plugin else URL_RE).finditer(line):
                url = match.group(1 if is_plugin else 0).rstrip(")]}")
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
        return (
            response.status,
            response.headers.get_content_type().lower(),
            response.read(limit + 1),
            response.geturl(),
        )


def fetch(resource: Resource, mode: str, timeout: float, retries: int) -> Result:
    problem = source_policy_error(resource.url)
    if problem:
        return Result(resource, False, None, "", resource.url, 0, problem)
    full_rule_check = mode == "full" and resource.kind in FULL_CHECK_KINDS
    limit = FULL_LIMIT if full_rule_check else LIGHT_BYTES
    headers = {"User-Agent": user_agent_for(resource), "Accept": "*/*"}
    if not full_rule_check:
        headers["Range"] = f"bytes=0-{LIGHT_BYTES - 1}"
    last_error = "unknown error"

    for attempt in range(retries + 1):
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
            elif full_rule_check and status != 200:
                last_error = f"full rule check requires HTTP 200, got {status}"
            elif full_rule_check and len(body) > limit:
                last_error = f"resource exceeds {FULL_LIMIT // (1024 * 1024)} MiB full-check limit"
            else:
                body = body[:limit]
                problem = source_policy_error(final_url) or validate_body(resource, content_type, body, mode)
                return Result(
                    resource=resource,
                    ok=problem is None,
                    status=status,
                    content_type=content_type,
                    final_url=final_url,
                    bytes_read=len(body),
                    message=problem or "ok",
                    body=body if full_rule_check and resource.kind == "clash-rule" else b"",
                )
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            status = exc.code
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = f"{exc.__class__.__name__}: {exc.reason if isinstance(exc, urllib.error.URLError) else exc}"
            status = None
        if attempt < retries:
            time.sleep(0.5 * (attempt + 1))

    return Result(
        resource=resource,
        ok=False,
        status=status,
        content_type="",
        final_url=resource.url,
        bytes_read=0,
        message=last_error,
    )


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
        def escape(value: str) -> str:
            return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "&#124;").replace("\n", " ").replace("\r", " ")

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
            rows.append("| " + " | ".join(escape(value) for value in values) + " |")
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write("\n".join(rows) + "\n")


def surge_reject_urls() -> list[str]:
    """The REJECT RULE-SET URLs of the Surge home module (not its reject_allow.list)."""
    urls = []
    for line in SURGE_REJECT_MODULE.read_text(encoding="utf-8").splitlines():
        if line.startswith("AND,((RULE-SET,"):
            urls.append(line[len("AND,((RULE-SET,"):].split(",", 1)[0])
    return urls


def reject_overlap_report(timeout: float) -> tuple[list[str], list[str]]:
    """Return (failures, warnings). Overlaps only warn; download and parse problems fail."""
    failures: list[str] = []
    warnings: list[str] = []
    urls = surge_reject_urls()
    if not urls:
        return [f"no REJECT RULE-SET lines in {SURGE_REJECT_MODULE.relative_to(ROOT)}"], []
    try:
        repo = check_reject_conflicts.load_repo()
    except check_reject_conflicts.InputError as exc:
        return [f"repo rules: {exc}"], []
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    for url in urls:
        try:
            status, _, body, _ = read_response(url, headers, timeout, FULL_LIMIT)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
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
        out = check_reject_conflicts.analyse(repo, rules)
        for kind in ("definite", "partial", "possible", "unanalysed"):
            for source, repo_rule, reject_rule in out[kind]:
                # The module's NOT(reject_allow.list) keeps these bank/broker hosts unblocked.
                note = " [excluded by reject_allow.list]" if source in build_rules.SURGE_REJECT_ALLOW_SOURCES else ""
                warnings.append(f"{kind}: {reject_rule} over {repo_rule} ({source}) in {redact_url(url)}{note}")
    return failures, warnings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("light", "full"), default="light")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--list", action="store_true", help="list extracted resources without network access")
    return parser.parse_args()


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

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(fetch, resource, args.mode, args.timeout, args.retries): resource
            for resource in resources
        }
        results = [future.result() for future in concurrent.futures.as_completed(futures)]

    failures = sorted((result for result in results if not result.ok), key=lambda item: item.resource.url)
    overlap_failures: list[str] = []
    content_failures: list[str] = []
    if args.mode == "full":
        try:
            comparisons, missing, stale = mihomo_content_report(results)
            emit_mihomo_report(comparisons, missing, stale)
            content_failures = [f"{r.name}: {r.message}" for r in comparisons if r.ip_rules is None]
            content_failures += [f"{name}: destination IP rules lack an IP-stage reference" for name in missing]
        except (ValueError, UnicodeError, yaml.YAMLError, OSError) as exc:
            content_failures = [f"not compared: {exc}"]
        overlap_failures, overlap_warnings = reject_overlap_report(args.timeout)
        if overlap_warnings:
            excluded = sum("[excluded by reject_allow.list]" in w for w in overlap_warnings)
            print(f"Surge REJECT lists: {len(overlap_warnings)} candidate overlaps with repo-routed rules before "
                  f"the reject_allow.list exclusion ({excluded} excluded, {len(overlap_warnings) - excluded} "
                  "still blocked; warning only, today's lists, see surge/README.md):")
            for warning in overlap_warnings:
                print(f"  [warn] {warning}")
    counts: dict[str, int] = {}
    for result in results:
        counts[result.resource.kind] = counts.get(result.resource.kind, 0) + 1

    if failures or overlap_failures or content_failures:
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
        print(f"Failed {len(failures)} of {len(results)} resources; {len(overlap_failures)} REJECT list errors; "
              f"{len(content_failures)} Mihomo content/coverage errors.")
        return 1

    summary = ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items()))
    print(f"Remote resources OK ({len(results)} unique; {summary}; mode={args.mode}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
