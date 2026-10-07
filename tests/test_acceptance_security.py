#!/usr/bin/env python3
"""Credential scanning fails closed and includes documentation and scripts."""
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import check_acceptance as check


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
                return subprocess.CompletedProcess(args, 0, stdout=name if '--cached' in args else '')
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
    print('Acceptance security negative cases passed.')


if __name__ == '__main__':
    main()
