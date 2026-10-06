#!/usr/bin/env python3
"""Offline contracts and adversarial cases for dependency governance."""
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import check_upstreams as check
import check_reject_conflicts


def validate(refs, exceptions=()):
    policy = deepcopy(check.load_policy(ROOT / 'rules/upstreams.yaml'))
    policy['exceptions'] = list(exceptions)
    return check.audit(refs, policy)


def main():
    refs = check.collect()
    results, errors = check.audit(refs, check.load_policy(ROOT / 'rules/upstreams.yaml'))
    assert not errors, errors
    assert any(r.source.startswith('quantumultx/rewrite/bilibili_ad.conf:') and
               r.url.endswith('/quantumultx/scripts/bilibili_json.js') for r in refs)
    assert any(r.source.startswith('surge/modules/converted/') for r in refs)
    assert any(r.field == 'build-source' for r in refs)
    assert any(r.field == 'build-pin' for r in refs)
    assert any(row['transitive'] == 'pending-external-container' for row in results)
    sha = '1' * 40
    pinned = f'https://raw.githubusercontent.com/example/scripts/{sha}/test.js'
    dynamic = 'https://raw.githubusercontent.com/example/scripts/master/test.js'
    mixed = f'https://raw.githubusercontent.com/fmz200/wool_scripts/{sha}/QuantumultX/rewrite/split/partZ/Test.snippet'
    bm = 'https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/QuantumultX/Test/Test.list'
    icon = 'https://cdn.jsdelivr.net/gh/Koolson/Qure@master/IconSet/Color/Test.png'
    github_raw = f'https://github.com/ddgksf2013/Scripts/raw/{sha}/test.js'
    assert not validate([check.Reference(github_raw, 'qx', 'script', 'fixture', 'executable')])[1]
    sample = f'[filter_remote]\n{mixed}, enabled=false\n[rewrite_remote]\n{mixed}, enabled=false\n'
    scanned = check.scan_text(sample, 'fixture.conf', 'qx')
    assert len(scanned) == 2 and all(r.kind == 'executable' for r in scanned)
    assert len({r.source for r in scanned}) == 2
    assert not validate(scanned)[1]
    assert not validate(check.scan_text(f'[filter_remote]\n{bm}\n[general]\nprofile_img_url = {icon}', 'fixture', 'qx'))[1]
    for url in (dynamic, dynamic.replace('test.js', 'test.conf'), icon,
                'https://github.com/example/scripts/releases/latest/download/test.js',
                bm.replace('Test.list', 'Test.js')):
        # Adding an icon to an executing line must not exempt the whole line.
        text = f'^https://fixture.invalid url script-response-body {url}, img-url={icon}'
        found = check.scan_text(text, 'quantumultx/rewrite/fixture.conf', 'qx')
        assert len(found) == 2 and validate(found)[1], (url, found)
    # Header-matching scripts and echo-response bodies are fetched as well; a mutable URL must fail.
    for directive in ('url-and-header script-response-body', 'url-and-header script-request-body',
                      'url-and-header echo-response text/html echo-response'):
        found = check.scan_text(f'^https://fixture.invalid Fixture {directive} {dynamic}', 'fixture.conf', 'qx')
        assert len(found) == 1 and validate(found)[1], (directive, found)
        assert not validate(check.scan_text(f'^https://fixture.invalid Fixture {directive} {pinned}', 'fixture.conf', 'qx'))[1]
    weibo = [r for r in refs if r.source.startswith('quantumultx/rewrite/fmz200-Weibo.snippet:') and r.field == 'script']
    assert len(weibo) == 22, len(weibo)
    for url in ('https://raw.githubusercontent.com/ddgksf2013/Scripts/master/test.js',
                'https://ddgksf2013.top/scripts/test.js'):
        ref = check.Reference(url, 'qx', 'script', 'fixture', 'executable')
        exception = dict(client='qx', field='script', url=url, reason='cannot override the existing source policy')
        assert validate([ref], [exception])[1], url
    ref = check.Reference(dynamic, 'qx', 'script', 'fixture:1', 'executable')
    exception = dict(client='qx', field='script', url=dynamic, reason='exact fixture exception')
    assert not validate([ref], [exception])[1]
    for changed in (check.Reference(dynamic.replace('test.js', 'extra.js'), 'qx', 'script', 'fixture', 'executable'),
                    check.Reference(dynamic, 'qx', 'resource_parser_url', 'fixture', 'executable'),
                    check.Reference(dynamic, 'loon', 'script', 'fixture', 'executable')):
        assert validate([changed], [exception])[1]
    assert any('unused exception' in e for e in validate([], [exception])[1])
    # One URL at two fields/positions needs two independently checked references.
    dual = check.scan_text(f'[general]\nresource_parser_url = {dynamic}\n[task_local]\n0 * * * * {dynamic}', 'fixture', 'qx')
    assert len(dual) == 2 and len({r.field for r in dual}) == 2
    assert validate(dual, [exception])[1]
    assert validate(check.scan_text(f'[general]\nunknown_script = {pinned}', 'fixture', 'qx'))[1]
    assert not check.scan_text(f'# Source: {dynamic}\n^https://fixture.invalid url 302 {dynamic}', 'fixture', 'qx')
    spaced = check.scan_text(f'[Script]\nfixture = type=http-response, script-path = {dynamic}', 'fixture', 'surge')
    assert spaced and validate(spaced)[1]
    managed = check.scan_text(f'#!MANAGED-CONFIG {dynamic} interval=86400', 'fixture', 'surge')
    assert managed and validate(managed)[1]
    assert validate([check.Reference(pinned.replace(sha, sha[:7]), 'qx', 'script', 'fixture', 'executable')])[1]
    # Validate schema rather than silently interpreting wildcards as exact exceptions.
    with tempfile.TemporaryDirectory() as d:
        directory = Path(d)
        (directory / 'ordinary.yaml').write_text('domain_suffix:\n  - fixture.invalid\n')
        (directory / 'local_rules.yaml').write_text('version: 1\n')
        (directory / 'upstreams.yaml').write_text('version: 1\nexceptions: []\n')
        loaded = check_reject_conflicts.load_repo(directory)
        assert loaded == {'domain_suffix': [('ordinary.yaml', 'fixture.invalid')]}, loaded
        path = directory / 'policy.yaml'
        # Wildcards and userinfo (credentials in a public repo) are both refused.
        for bad in (dynamic.replace('test.js', '*'), dynamic.replace('test.js', '?'),
                    dynamic.replace('https://', 'https://user:secret@'), dynamic.replace('https://', 'https://token@')):
            policy = deepcopy(check.load_policy(ROOT / 'rules/upstreams.yaml'))
            policy['exceptions'] = [{**exception, 'url': bad}]
            path.write_text(yaml.safe_dump(policy))
            try:
                check.load_policy(path)
            except ValueError:
                pass
            else:
                raise AssertionError(f'invalid exception accepted: {bad}')
    # Exercise the actual command so future CI discovery cannot leave the gate unused.
    subprocess.run([sys.executable, str(ROOT / 'scripts/check_upstreams.py')], check=True)
    print('Upstream governance: field/source/version/exception/coverage negative cases passed.')


if __name__ == '__main__':
    main()
