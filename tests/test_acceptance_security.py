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


def main():
    failed = subprocess.CompletedProcess([], 128, stdout='', stderr='synthetic failure')
    with patch.object(check.subprocess, 'run', return_value=failed):
        errors = []
        check.check_security(errors)
        assert any('credential scan incomplete' in e for e in errors), errors
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        # Construct the synthetic secret so this fixture does not itself resemble a leaked token.
        for suffix in ('md', 'js', 'snippet', 'plugin'):
            name = f'fixture.{suffix}'
            (root / name).write_text('https://example.invalid/?' + 'token' + '=synthetic-secret')
            def listing(args, **kwargs):
                return subprocess.CompletedProcess(args, 0, stdout=f'{name}\0'.encode() if '--cached' in args else '')
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
