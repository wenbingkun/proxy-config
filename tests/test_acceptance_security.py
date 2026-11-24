#!/usr/bin/env python3
"""Credential scanning fails closed and includes documentation and scripts."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import check_acceptance as check


def check_full_cli_with_real_git():
    """Run the whole audit against real Git output: special file names must be
    scanned, and the failure output must not repeat the token."""
    listing = subprocess.run(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
        cwd=ROOT, capture_output=True, check=True,
    ).stdout
    secret = 'Fixture' + 'Only7Kq2Zx9Lm4Np'
    with tempfile.TemporaryDirectory() as directory:
        repo = Path(directory)
        for raw in listing.split(b'\0'):
            if not raw:
                continue
            rel = os.fsdecode(raw)
            source = ROOT / rel
            if source.is_file():
                target = repo / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        subprocess.run(['git', 'init', '-q'], cwd=repo, check=True)
        subprocess.run(['git', 'add', '-A'], cwd=repo, check=True)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')

        def audit():
            result = subprocess.run(
                [sys.executable, 'scripts/check_acceptance.py'],
                cwd=repo, env=env, capture_output=True, text=True,
            )
            return result.returncode, result.stdout + result.stderr

        code, output = audit()
        assert code == 0, output
        # The other credential checks must keep naming the file without echoing values.
        values = ('Pass' + 'Fixture5Rt8', 'P12' + 'Fixture3Wy6', 'Sub' + 'Fixture9Hd2')
        (repo / 'fixture-mitm.conf').write_text(
            f'passphrase = {values[0]}\nca-p12 = {values[1]}\n'
            f'SUB_URL_1=https://service.invalid/{values[2]}\n'
        )
        code, output = audit()
        assert code == 1, output
        for message in ("'passphrase' has a non-empty value", "'p12' has a non-empty value",
                        'possible real subscription URL in SUB_URL_1'):
            assert f'fixture-mitm.conf: {message}' in output, (message, output)
        assert not any(value in output for value in values)
        (repo / 'fixture-mitm.conf').unlink()
        for name in ('fixture-plain.md', '凭据样例.md', 'fixture\tname.md', 'fixture"name.md'):
            path = repo / name
            path.write_text('https://service.invalid/sub?' + 'token' + '=' + secret + '\n')
            for staged in (False, True):
                if staged:
                    subprocess.run(['git', 'add', '--', name], cwd=repo, check=True)
                code, output = audit()
                assert code == 1, (name, staged, output)
                assert f'{name}:1: possible real subscription token' in output, (name, staged, output)
                assert secret not in output, (name, staged)
            path.write_text('no credentials here\n')
            code, output = audit()
            assert code == 0, (name, output)
            subprocess.run(['git', 'rm', '-q', '-f', '--cached', '--', name], cwd=repo, check=True)
            path.unlink()

        # New credential detectors: PEM private keys, Bearer tokens (case-insensitive & JSON),
        # local secrets (including export and quoted # values), http-api, extra query params
        # (including percent-encoded and placeholder-substring bypasses).
        extra_secrets = (
            'Bearer' + 'Fixture8Km3Qx7Lp',
            'Standalone' + 'Jwt9Ab2Cd4Ef6Gh',
            'Short' + 'Lower9',
            'Json' + 'Short7',
            'Router' + 'Secret5Vn2Wz8Jr',
            'Export' + 'Router3Qx9Km',
            '#Hash' + 'Quoted4Tn8Wp',
            '#UnquotedHash' + 'Key9Zx2Mn4Pq7Rs',
            'placeholder#Fixture' + '8Km',
            'Surge' + 'Key4Ty9Mn6Bc',
            'HttpApi' + 'Key7Df2Gh5Jk',
            'QueryKey' + '9Pl4Mk1Nj',
            '%41Encoded' + 'Key6Zr2',
            'QuerySec' + '3Bv6Cx8Za',
            'real_placeholder_' + 'leak99',
            'placeholder-fixture-' + 'value8',
            'QueryAuth' + '2Qw5Er8Ty',
        )
        pem_marker = '-----' + 'BEGIN RSA PRIVATE KEY' + '-----'
        extra_file = repo / 'fixture-extra-secrets.conf'
        extra_file.write_text(
            '\n'.join((
                pem_marker,
                f'Authorization: Bearer {extra_secrets[0]}',
                f'header = Bearer {extra_secrets[1]}',
                f'authorization: bearer {extra_secrets[2]}',
                f'{{"Authorization": "Bearer {extra_secrets[3]}"}}',
                f'ROUTER_SECRET={extra_secrets[4]}',
                f'export ROUTER_SECRET={extra_secrets[5]}',
                f'ROUTER_SECRET="{extra_secrets[6]}"',
                f'ROUTER_SECRET={extra_secrets[7]}',
                f'ROUTER_SECRET={extra_secrets[8]}',
                f'SURGE_KEY="{extra_secrets[9]}"',
                f'http-api = {extra_secrets[10]}@0.0.0.0:6171',
                f'https://service.invalid/a?key={extra_secrets[11]}',
                f'https://service.invalid/a2?key={extra_secrets[12]}',
                f'https://service.invalid/b?secret={extra_secrets[13]}',
                f'https://service.invalid/b2?secret={extra_secrets[14]}',
                f'https://service.invalid/b3?secret={extra_secrets[15]}',
                f'https://service.invalid/c?foo=1&auth={extra_secrets[16]}',
            )) + '\n',
            encoding='utf-8',
        )
        code, output = audit()
        assert code == 1, output
        assert output.count('possible real Bearer token') == 4, output
        assert output.count('possible real secret in ROUTER_SECRET') == 5, output
        assert output.count('possible real query credential: key=<redacted>') == 2, output
        assert output.count('possible real query credential: secret=<redacted>') == 3, output
        for expected in (
            'PEM private key header detected',
            'possible real secret in SURGE_KEY',
            'possible real key in http-api',
            'possible real query credential: auth=<redacted>',
        ):
            assert expected in output, (expected, output)
        assert not any(val in output for val in extra_secrets), output
        # Template/placeholder values must remain allowed without false positives.
        extra_file.write_text(
            '\n'.join((
                "Authorization: Bearer {cfg['ROUTER_SECRET']}",
                'ROUTER_SECRET=<router-secret>',
                'SURGE_KEY=REPLACE_WITH_RANDOM_KEY',
                'http-api = REPLACE_WITH_RANDOM_KEY@0.0.0.0:6171',
                'https://service.invalid/search?key=$1',
                'https://service.invalid/sub?auth=replace-me-one&secret=%s',
            )) + '\n',
            encoding='utf-8',
        )
        code, output = audit()
        assert code == 0, output
        extra_file.unlink()

        # Force-tracked files matching .gitignore patterns (exact names and globs) must fail.
        for ignored_rel in (
            'mihomo/shellcrash/providers.env',
            'mihomo/shellcrash/fixture.local.env',
            'quantumultx/fixture.p12',
        ):
            ignored_path = repo / ignored_rel
            ignored_path.parent.mkdir(parents=True, exist_ok=True)
            ignored_path.write_text('placeholder\n', encoding='utf-8')
            subprocess.run(['git', 'add', '-f', '--', ignored_rel], cwd=repo, check=True)
            code, output = audit()
            assert code == 1, (ignored_rel, output)
            assert ignored_rel in output, (ignored_rel, output)
            subprocess.run(['git', 'rm', '-q', '-f', '--', ignored_rel], cwd=repo, check=True)
        code, output = audit()
        assert code == 0, output


def main():
    failed = subprocess.CompletedProcess([], 128, stdout='', stderr='synthetic failure')
    with patch.object(check.subprocess, 'run', return_value=failed):
        errors = []
        check.check_security(errors)
        assert any('credential scan incomplete' in e for e in errors), errors
        assert any('ignored-file tracking check incomplete' in e for e in errors), errors
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        # Construct the synthetic secret so this fixture does not itself resemble a leaked token.
        for suffix in ('md', 'js', 'snippet', 'plugin'):
            name = f'fixture.{suffix}'
            (root / name).write_text('https://example.invalid/?' + 'token' + '=synthetic-secret')
            def listing(args, **kwargs):
                if '-ci' in args:
                    return subprocess.CompletedProcess(args, 0, stdout=b'')
                if '--cached' in args:
                    return subprocess.CompletedProcess(args, 0, stdout=f'{name}\0'.encode())
                return subprocess.CompletedProcess(args, 0, stdout='')
            with patch.object(check, 'ROOT', root), patch.object(check.subprocess, 'run', side_effect=listing):
                errors = []
                check.check_security(errors)
                assert any(name in e for e in errors), errors
        (root / 'fixture.js').write_text('nothing')
        with patch.object(check, 'ROOT', root), patch.object(check.subprocess, 'run', side_effect=listing), \
                patch.object(Path, 'read_text', side_effect=OSError('synthetic read failure')):
            errors = []
            check.check_security(errors)
            assert any('unable to read' in e for e in errors), errors
    check_full_cli_with_real_git()
    print('Acceptance security negative cases passed.')


if __name__ == '__main__':
    main()
