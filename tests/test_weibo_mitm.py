#!/usr/bin/env python3
"""Prove finite coverage of the dedicated Weibo MitM host lists."""
from pathlib import Path
from re import _parser as parser, _constants as c
import re

ROOT = Path(__file__).resolve().parents[1]
QX_HOSTS = {
    'api.weibo.cn', 'api.weibo.com', 'mapi.weibo.cn', 'mapi.weibo.com',
    'bootpreload.uve.weibo.com', 'bootrealtime.uve.weibo.com',
    'sdkapp.uve.weibo.com', 'wbapp.uve.weibo.com',
    'new.vip.weibo.cn', 'new.vip.weibo.com', 'weibointl.api.weibo.cn',
}
OTHER_HOSTS = QX_HOSTS | {'api-cloudim.api.weibo.com', 'card.weibo.com',
                         'weibo.com', 'weibointl.api.weibo.com'}


def expand(tokens):
    """Expand only finite literal host regexes; fail on wildcards or unknown syntax."""
    result = ['']
    for op, arg in tokens:
        if op == c.LITERAL:
            parts = [chr(arg)]
        elif op == c.SUBPATTERN:
            parts = expand(arg[-1])
        elif op == c.BRANCH:
            parts = [p for branch in arg[1] for p in expand(branch)]
        elif op == c.MAX_REPEAT and arg[:2] == (0, 1):
            parts = [''] + expand(arg[2])
        else:
            raise ValueError(f'host coverage cannot be proven: {op} {arg}')
        result = [a + b for a in result for b in parts]
    return result


def check_coverage(text, expected):
    declared = set()
    covered = set()
    count = 0
    for line in text.splitlines():
        if line.startswith('#'):
            continue
        if line.startswith('hostname ='):
            declared.update(h.strip() for h in line.split('=', 1)[1].replace('%APPEND%', '').split(','))
        if '^http' not in line:
            continue
        match = re.search(r'\^https?[^:]*:', line)
        assert match, f'unhandled URL rule: {line}'
        pattern = line[match.start():].split(' ', 1)[0].rstrip('",').replace(r'\/', '/')
        host = pattern.split('/', 3)[2].removesuffix('(')
        covered.update(expand(parser.parse(host, 0)))
        count += 1
    assert count > 20
    assert covered == expected, (covered, expected)
    assert declared == covered, (declared, covered)
    assert not any('*' in h or '?' in h for h in declared)
    assert not {'passport.weibo.cn', 'passport.weibo.com', 'login.weibo.cn'} & declared


def main():
    for file, expected in (
        ('quantumultx/rewrite/fmz200-Weibo.snippet', QX_HOSTS),
        ('loon/plugins/Weibo.plugin', OTHER_HOSTS),
        ('surge/modules/converted/Weibo.sgmodule', OTHER_HOSTS),
        ('surge/modules/rewrite/weibo.sgmodule', OTHER_HOSTS),
    ):
        text = (ROOT / file).read_text()
        check_coverage(text, expected)
        for broken in (re.sub(r'(?m)^(hostname =.*)api\.weibo\.cn, ', r'\1', text, count=1),
                       re.sub(r'(?m)^hostname =', 'hostname = *.weibo.cn,', text, count=1),
                       text + '\n^https:\\/\\/passport\\.weibo\\.cn/login url reject\n'):
            try:
                check_coverage(broken, expected)
            except (AssertionError, ValueError):
                continue
            raise AssertionError(f'coverage mutation accepted: {file}')
    try:
        expand(parser.parse(r'.*\.weibo\.cn', 0))
    except ValueError:
        pass
    else:
        raise AssertionError('unbounded host regex accepted')
    print('Weibo MitM coverage and negative mutations passed.')


if __name__ == '__main__':
    main()
