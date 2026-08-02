#!/usr/bin/env python3
"""Lightweight availability checks for externally hosted config resources."""
from __future__ import annotations

import argparse
import concurrent.futures
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
CLASH_CONFIG = ROOT / "clash" / "config.yaml"
QX_FILES = (
    ROOT / "quantumultx" / "bootstrap.example.conf",
    ROOT / "quantumultx" / "filter_remote.snippet",
)
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_shellcrash_config.sh"

URL_RE = re.compile(r"https?://[^\s,\"']+")
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
    "ip-api.com",
    "www.baidu.com",
    "www.gstatic.com",
}
LIGHT_BYTES = 4096
FULL_LIMIT = 16 * 1024 * 1024
USER_AGENT = "proxy-config-remote-check/1.0"
QX_USER_AGENT = "Quantumult X"


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


def extract_resources() -> list[Resource]:
    resources: dict[str, Resource] = {}

    clash = yaml.safe_load(CLASH_CONFIG.read_text(encoding="utf-8")) or {}
    providers = clash.get("rule-providers", {})
    if not isinstance(providers, dict):
        raise ValueError("clash/config.yaml: rule-providers must be a mapping")
    for name, provider in providers.items():
        if not isinstance(provider, dict) or not isinstance(provider.get("url"), str):
            raise ValueError(f"clash/config.yaml: rule-provider {name!r} has no URL")
        url = provider["url"]
        if not is_skipped_url(url):
            resources[url] = Resource(
                url=url,
                kind="clash-rule",
                source=f"clash/config.yaml:rule-providers.{name}",
                expected_format=provider.get("format"),
            )

    for path in QX_FILES:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            for match in URL_RE.finditer(line):
                url = match.group(0).rstrip(")]}")
                if is_skipped_url(url):
                    continue
                resources.setdefault(
                    url,
                    Resource(
                        url=url,
                        kind=infer_qx_kind(line, url),
                        source=f"{path.relative_to(ROOT)}:{lineno}",
                    ),
                )

    deploy_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    for match in SHELL_TEMPLATE_RE.finditer(deploy_text):
        url = match.group("url")
        resources.setdefault(
            url,
            Resource(url=url, kind="shellcrash-template", source="scripts/deploy_shellcrash_config.sh"),
        )

    return sorted(resources.values(), key=lambda item: (item.kind, item.url))


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
    if mode != "full" or resource.kind != "clash-rule":
        return None

    text = body.decode("utf-8-sig", errors="replace")
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
    if resource.source.startswith("quantumultx/"):
        return QX_USER_AGENT
    return USER_AGENT


def fetch(resource: Resource, mode: str, timeout: float, retries: int) -> Result:
    full_rule_check = mode == "full" and resource.kind == "clash-rule"
    limit = FULL_LIMIT if full_rule_check else LIGHT_BYTES
    headers = {"User-Agent": user_agent_for(resource), "Accept": "*/*"}
    if not full_rule_check:
        headers["Range"] = f"bytes=0-{LIGHT_BYTES - 1}"
    request = urllib.request.Request(resource.url, headers=headers, method="GET")
    last_error = "unknown error"

    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.status
                content_type = response.headers.get_content_type().lower()
                body = response.read(limit + 1)
                final_url = response.geturl()
            if not 200 <= status < 300:
                last_error = f"HTTP {status}"
            elif full_rule_check and len(body) > limit:
                last_error = f"resource exceeds {FULL_LIMIT // (1024 * 1024)} MiB full-check limit"
            else:
                body = body[:limit]
                problem = validate_body(resource, content_type, body, mode)
                return Result(
                    resource=resource,
                    ok=problem is None,
                    status=status,
                    content_type=content_type,
                    final_url=final_url,
                    bytes_read=len(body),
                    message=problem or "ok",
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
    counts: dict[str, int] = {}
    for result in results:
        counts[result.resource.kind] = counts.get(result.resource.kind, 0) + 1

    if failures:
        print("Remote resource check failed:")
        for result in failures:
            print(
                f"  [fail] {result.resource.kind} {redact_url(result.resource.url)}: "
                f"{result.message} ({result.resource.source})"
            )
        print(f"Failed {len(failures)} of {len(results)} resources.")
        return 1

    summary = ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items()))
    print(f"Remote resources OK ({len(results)} unique; {summary}; mode={args.mode}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
