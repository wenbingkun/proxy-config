#!/usr/bin/env python3
"""Offline, field-aware governance of published resources and build inputs.

Versioned references remain in their existing sources; the policy holds only bounded
rule/icon patterns and exact mutable exceptions. This is not a transitive code audit.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

import yaml

from check_remote_resources import ROOT, SHELL_TEMPLATE_RE, source_policy_error

URL = r'https?://[^\s,"\']+'
URL_RE = re.compile(URL)
SCRIPT_RE = re.compile(r'script-path\s*=\s*(' + URL + r')', re.I)
ICON_RE = re.compile(r'(?:img-url|profile_img_url|#!icon)\s*=\s*(' + URL + r')')
# QX loads a remote body for `url` and `url-and-header` scripts and for echo-response with a URL.
QX_SCRIPT_RE = re.compile(
    r'\burl(?:-and-header)?\s+(?:script-\S+|echo-response\s+\S+\s+echo-response)\s+(' + URL + r')', re.I)
RULE_RE = re.compile(r'RULE-SET,\s*(' + URL + r')', re.I)
OWN_PREFIXES = ('quantumultx/', 'loon/', 'surge/', 'mihomo/')


@dataclass(frozen=True)
class Reference:
    url: str
    client: str
    field: str
    source: str
    kind: str
    container: bool = False


def scan_text(text: str, source: str, client: str) -> list[Reference]:
    refs = []
    section = ''
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        where = f'{source}:{number}'
        if line.startswith('[') and line.endswith(']'):
            section = line[1:-1].lower()
            continue
        # Surge's converter discards QX/Loon icon metadata; it is not fetched by Surge.
        icon_text = line if client != 'surge' and (not line.startswith(('#', ';')) or line.startswith('#!icon')) else ''
        for match in ICON_RE.finditer(icon_text):
            refs.append(Reference(match[1], client, 'icon', where, 'icon'))
        if line.startswith('#!MANAGED-CONFIG '):
            match = URL_RE.search(line)
            if match:
                refs.append(Reference(match[0], client, 'managed-config', where, 'executable', True))
        if not line or line.startswith(('#', ';')):
            continue
        for match in SCRIPT_RE.finditer(line):
            refs.append(Reference(match[1], client, 'script-path', where, 'executable'))
        if client == 'qx':
            for match in QX_SCRIPT_RE.finditer(line):
                refs.append(Reference(match[1], client, 'script', where, 'executable'))
            if section in ('filter_remote', 'rewrite_remote') and line.startswith(('https://', 'http://')):
                url = line.split(',', 1)[0]
                mixed = urlsplit(url).path.endswith('.snippet') and '/rewrite/' in urlsplit(url).path
                kind = 'executable' if section == 'rewrite_remote' or mixed else 'rule'
                refs.append(Reference(url, client, section, where, kind, kind == 'executable'))
            elif section == 'general' and line.startswith('resource_parser_url'):
                for match in URL_RE.finditer(line):
                    refs.append(Reference(match[0], client, 'resource_parser_url', where, 'executable'))
            elif section == 'general' and line.startswith('geo_location_checker'):
                urls = URL_RE.findall(line)
                for i, url in enumerate(urls):
                    if i == 0 and urlsplit(url).hostname == 'ip-api.com':
                        continue
                    refs.append(Reference(url, client, 'geo_location_checker', where, 'executable'))
            elif section == 'task_local':
                match = URL_RE.search(line)
                if match:
                    refs.append(Reference(match[0], client, 'task_local', where, 'executable'))
            elif section == 'general' and URL_RE.search(line):
                key = line.split('=', 1)[0].strip()
                if key not in ('server_check_url', 'network_check_url', 'profile_img_url'):
                    for url in URL_RE.findall(line):
                        refs.append(Reference(url, client, key, where, 'unknown'))
        elif client == 'loon' and section in ('remote rule', 'plugin') and line.startswith(('https://', 'http://')):
            refs.append(Reference(line.split(',', 1)[0], client, section, where,
                                  'rule' if section == 'remote rule' else 'executable', section == 'plugin'))
        if client == 'surge':
            for match in RULE_RE.finditer(line):
                refs.append(Reference(match[1].rstrip(')'), client, 'rule-set', where, 'rule'))
    return refs


def collect(root: Path = ROOT) -> list[Reference]:
    refs = []
    for client, paths in (
        ('qx', [root / 'quantumultx/bootstrap.example.conf', root / 'quantumultx/filter/repo.snippet', *sorted((root / 'quantumultx/rewrite').glob('*'))]),
        ('loon', [root / 'loon/bootstrap.example.conf', *sorted((root / 'loon/plugins').glob('*.plugin'))]),
        ('surge', [root / 'surge/proxy-config.conf', *sorted((root / 'surge/modules').rglob('*.sgmodule'))]),
    ):
        for path in paths:
            if path.is_file():
                refs.extend(scan_text(path.read_text(), str(path.relative_to(root)), client))
    config = yaml.safe_load((root / 'mihomo/verge/config.yaml').read_text())
    for name, provider in config['rule-providers'].items():
        refs.append(Reference(provider['url'], 'mihomo', 'rule-provider',
                              f'mihomo/verge/config.yaml:rule-providers.{name}', 'rule'))
    for match in SHELL_TEMPLATE_RE.finditer((root / 'mihomo/shellcrash/deploy.sh').read_text()):
        refs.append(Reference(match['url'], 'mihomo', 'template', 'mihomo/shellcrash/deploy.sh', 'executable', True))
    # These modules declare sources without fetching on import. Inspect final pin values,
    # not old replacement keys: those are not execution references.
    import build_surge_modules as surge
    import build_loon_plugins as loon
    for source in surge.SOURCES:
        where = f'scripts/build_surge_modules.py:SOURCES.{source["file"]}'
        if 'url' in source:
            refs.append(Reference(source['url'], 'surge', 'build-source', where, 'executable', True))
        for url in source.get('pins', {}).values():
            refs.append(Reference(url, 'surge', 'build-pin', where, 'executable'))
    for app, part in loon.MIRRORS.items():
        refs.append(Reference(f'{loon.FMZ200}/{part}/{app}.lpx', 'loon', 'build-source',
                              f'scripts/build_loon_plugins.py:MIRRORS.{app}', 'executable', True))
    for name, url in loon.ICONS.items():
        refs.append(Reference(url, 'loon', 'icon', f'scripts/build_loon_plugins.py:ICONS.{name}', 'icon'))
    return refs


def github_revision(url: str) -> tuple[str, str, str] | None:
    parsed = urlsplit(url)
    path = unquote(parsed.path)
    if parsed.scheme != 'https' or parsed.username or parsed.password:
        return None
    if parsed.hostname == 'raw.githubusercontent.com':
        match = re.fullmatch(r'/([^/]+/[^/]+)/([^/]+)/(.*)', path)
    elif parsed.hostname in ('cdn.jsdelivr.net', 'fastly.jsdelivr.net', 'testingcf.jsdelivr.net'):
        match = re.fullmatch(r'/gh/([^/]+/[^/@]+)@([^/]+)/(.*)', path)
    elif parsed.hostname in ('github.com', 'www.github.com'):
        match = re.fullmatch(r'/([^/]+/[^/]+)/(?:raw|blob)/([^/]+)/(.*)', path)
    else:
        return None
    return (match[1], match[2], match[3]) if match else None


def owned(url: str, root: Path) -> bool:
    revision = github_revision(url)
    if not revision or revision[0] != 'wenbingkun/proxy-config' or revision[1] != 'main':
        return False
    relative = revision[2]
    if not relative.startswith(OWN_PREFIXES) or '..' in Path(relative).parts:
        return False
    return (root / relative).is_file()


def exception_key(value: Reference | dict) -> tuple[str, str, str]:
    if isinstance(value, Reference):
        return value.client, value.field, value.url
    return value['client'], value['field'], value['url']


def load_policy(path: Path) -> dict:
    policy = yaml.safe_load(path.read_text())
    if not isinstance(policy, dict) or set(policy) != {'version', 'rules', 'icons', 'exceptions'} or policy['version'] != 1:
        raise ValueError('invalid upstream policy schema/version')
    for name in ('rules', 'icons'):
        if not isinstance(policy[name], list) or not all(isinstance(pattern, str) for pattern in policy[name]):
            raise ValueError(f'{name}: expected regex strings')
        for pattern in policy[name]:
            re.compile(pattern)
    if not isinstance(policy['exceptions'], list):
        raise ValueError('exceptions must be a list')
    seen = set()
    for item in policy['exceptions']:
        if not isinstance(item, dict) or set(item) != {'client', 'field', 'url', 'reason'}:
            raise ValueError('exceptions require client, field, exact url and reason')
        if not all(isinstance(value, str) and value.strip() for value in item.values()):
            raise ValueError('exception fields must be nonempty strings')
        if not item['url'].startswith('https://') or any(c in item['url'] for c in ('*', '?')):
            raise ValueError('exceptions must use exact HTTPS URLs without wildcards')
        key = exception_key(item)
        if key in seen:
            raise ValueError(f'duplicate exception: {key}')
        seen.add(key)
    return policy


def audit(refs: list[Reference], policy: dict, root: Path = ROOT) -> tuple[list[dict], list[str]]:
    exceptions = {exception_key(item): item['reason'] for item in policy['exceptions']}
    used = set()
    results, errors = [], []
    for ref in refs:
        reason = ''
        error = source_policy_error(ref.url)
        revision = github_revision(ref.url)
        if error:
            status = 'rejected'
        elif ref.kind == 'icon':
            if not any(re.fullmatch(pattern, ref.url) for pattern in policy['icons']):
                error, status = 'unregistered PNG icon source', 'rejected'
            else:
                status = 'icon'
        elif ref.kind == 'rule':
            if not any(re.fullmatch(pattern, ref.url) for pattern in policy['rules']) and not owned(ref.url, root):
                error, status = 'unregistered rule source/path/format', 'rejected'
            else:
                status = 'rule'
        elif ref.kind != 'executable':
            error, status = 'unknown resource field', 'rejected'
        elif revision and re.fullmatch('[0-9a-fA-F]{40}', revision[1]):
            status = 'fixed'
        elif owned(ref.url, root):
            status = 'controlled'
        elif exception_key(ref) in exceptions:
            used.add(exception_key(ref))
            reason, status = exceptions[exception_key(ref)], 'mutable-exception'
        else:
            error, status = 'unregistered mutable executable resource', 'rejected'
        if error:
            errors.append(f'{ref.source} [{ref.field}] {error}: {ref.url}')
        transitive = 'not-applicable'
        if ref.kind == 'executable':
            transitive = 'script-body-not-audited'
            if ref.container:
                if owned(ref.url, root):
                    transitive = 'local-container-scanned'
                elif revision and revision[0] == 'wenbingkun/proxy-config':
                    transitive = 'pending-versioned-container'
                else:
                    transitive = 'pending-external-container'
        results.append({**asdict(ref), 'status': status, 'reason': reason, 'transitive': transitive})
    for key in sorted(exceptions.keys() - used):
        errors.append(f'unused exception: {key}')
    return results, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='print every reference position and status')
    args = parser.parse_args()
    try:
        results, errors = audit(collect(), load_policy(ROOT / 'rules/upstreams.yaml'))
    except (ValueError, KeyError, OSError, yaml.YAMLError, re.error) as exc:
        print(f'Upstream input error: {exc}', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({'references': results, 'errors': errors}, ensure_ascii=False, indent=2))
    else:
        print(f'{len(results)} reference positions: {dict(Counter(row["status"] for row in results))}')
        for error in errors:
            print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
