#!/usr/bin/env python3
"""Verify the actual pinned QX parser, without hosting upstream code in this repo.

CI downloads the fixed CDN URL. Local checks may supply the verified artifact with
PROXY_CONFIG_QX_PARSER or --parser; byte validation is identical in either case.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
COMMIT = '38a6fe02eb7cc67efd26a8f3c618bd031f1885b4'
URL = f'https://cdn.jsdelivr.net/gh/KOP-XIAO/QuantumultX@{COMMIT}/Scripts/resource-parser.js'
DIGEST = '30d4b7953a901f688723536eb7cd143a82e0ae2d0817fafd1b76fc246335d0a5'


def validate_url(url: str) -> None:
    if url != URL:
        raise ValueError('parser URL must match the reviewed full commit and original CDN path')


def validate_bytes(body: bytes) -> None:
    if len(body) != 265787 or hashlib.sha256(body).hexdigest() != DIGEST:
        raise ValueError('parser bytes differ from the reviewed version')


def fetch(url: str, opener=urllib.request.urlopen) -> bytes:
    validate_url(url)
    with opener(url, timeout=30) as response:
        if response.status != 200 or response.geturl() != URL:
            raise ValueError('fixed parser URL failed or redirected to a different version')
        body = response.read()
    validate_bytes(body)
    return body


def negatives() -> None:
    for bad in (URL.replace(COMMIT, 'master'), URL.replace(COMMIT, COMMIT[:7]), URL.replace(COMMIT, '0' * 40)):
        try:
            validate_url(bad)
        except ValueError:
            pass
        else:
            raise AssertionError('unreviewed parser URL accepted')
    for body in (b'', b'error page', b'x' * 265787):
        try:
            validate_bytes(body)
        except ValueError:
            pass
        else:
            raise AssertionError('unreviewed bytes accepted')
    def failed(*args, **kwargs):
        raise urllib.error.HTTPError(URL, 404, 'fixture missing', {}, None)
    try:
        fetch(URL, failed)
    except urllib.error.HTTPError:
        pass
    else:
        raise AssertionError('failed URL accepted')


def main() -> None:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('--parser', type=Path, default=os.environ.get('PROXY_CONFIG_QX_PARSER'))
    options = args.parse_args()
    text = (ROOT / 'quantumultx/bootstrap.example.conf').read_text()
    urls = re.findall(r'^resource_parser_url\s*=\s*(\S+)\s*$', text, re.M)
    assert urls == [URL], 'template parser must use the exact reviewed revision'
    assert '#regout=^(USER-AGENT|IP6-CIDR)%2C&ntf=0' in text
    negatives()
    body = Path(options.parser).read_bytes() if options.parser else fetch(urls[0])
    validate_bytes(body)
    with tempfile.TemporaryDirectory() as d:
        parser = Path(d) / 'resource-parser.js'
        parser.write_bytes(body)
        subprocess.run(['node', str(ROOT / 'tests/fixtures/resource_parser_cases.js'), str(parser), str(ROOT)], check=True)
    print('Parser full-SHA URL, reviewed bytes and failed/changed-version negatives passed.')


if __name__ == '__main__':
    main()
