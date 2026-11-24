#!/usr/bin/env python3
"""API output, fallback, transport and recorder failure security contracts."""
import io
import json
from pathlib import Path
import sys
import tempfile
import urllib.error
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import netdiag as n

KEY = 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4'


def main():
    for key in ('', 'examplekey', 'set-your-secret', 'a' * 40, 'REPLACE_WITH_RANDOM_KEY', 'REPLACE_WITH_RANDOM_KEY_0123456789'):
        try: n.surge_headers({'SURGE_KEY': key})
        except ValueError: pass
        else: raise AssertionError('weak key accepted')
    assert n.surge_headers({'SURGE_KEY': KEY}) == {'X-Key': KEY}
    cfg = {'SURGE_API': 'http://lan', 'SURGE_API_USB': 'http://usb', 'SURGE_KEY': KEY}
    for code in (401, 403, 502, 503):
        n._surge_last.clear()
        calls = []
        def get(url, *_args):
            calls.append(url)
            if url.startswith('http://lan'):
                raise urllib.error.HTTPError(url, code, 'synthetic', {}, None)
            return {'ok': True}
        with patch.object(n, 'http_get', side_effect=get):
            try: result = n.api_get(cfg, 'surge', '/v1/rules')
            except urllib.error.HTTPError:
                assert code in (401, 403) and len(calls) == 1
            else: assert code in (502, 503) and result['ok'] and len(calls) == 2
    raw = {'rules': ['SUBNET,SSID:"Synthetic Home, WiFi",DIRECT', {'notes': ['SUBNET SSID:Synthetic Home (module)']}], 'number': 3, 'flag': True, 'key': KEY}
    out = io.StringIO()
    with patch.object(n, 'load_config', return_value=cfg), patch.object(n, 'api_get', return_value=raw), redirect_stdout(out):
        n.cmd_get(Mock(source='surge', path='/v1/rules'))
    parsed = json.loads(out.getvalue())
    assert parsed['number'] == 3 and parsed['flag'] is True and 'Synthetic Home' not in out.getvalue() and KEY not in out.getvalue()
    # Inspect the real opener's proxy map; setting an environment proxy cannot change it.
    captured = []
    original = n.urllib.request.build_opener
    def opener(*handlers):
        captured.extend(handlers)
        return original(*handlers)
    with patch.dict(n.os.environ, {'http_proxy': 'http://proxy.invalid:9999', 'HTTP_PROXY': 'http://proxy.invalid:9999'}), \
            patch.object(n.urllib.request, 'build_opener', side_effect=opener), \
            patch.object(n.urllib.request.OpenerDirector, 'open', side_effect=RuntimeError('stop before network')):
        try: n.http_get('http://lan/v1/rules', {'X-Key': KEY})
        except RuntimeError: pass
    assert any(isinstance(h, n.urllib.request.ProxyHandler) and h.proxies == {} for h in captured)
    # A worker that returns instead of recording cannot leave the service apparently alive.
    with tempfile.TemporaryDirectory() as directory, patch.object(n, 'STATE', Path(directory)), \
            patch.object(n, 'load_config', return_value=cfg), patch.object(n, 'record_surge', return_value=None):
        try: n.cmd_record(None)
        except RuntimeError as exc: assert 'worker exited' in str(exc)
        else: raise AssertionError('dead worker hidden')

    # Secret config file must be owner-only (0600); group/world permissions must be refused.
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        env_file = base / 'env'
        env_file.write_text('ROUTER_API=http://192.0.2.1:9999\n', encoding='utf-8')
        for bad_mode in (0o644, 0o640, 0o604):
            env_file.chmod(bad_mode)
            with patch.object(n, 'CONFIG', env_file):
                try:
                    n.load_config()
                except PermissionError as exc:
                    assert 'chmod 600' in str(exc), exc
                else:
                    raise AssertionError(f'overly open config mode {oct(bad_mode)} accepted')
        env_file.chmod(0o600)
        with patch.object(n, 'CONFIG', env_file):
            assert n.load_config()['ROUTER_API'] == 'http://192.0.2.1:9999'

        # Non-regular config file (e.g. FIFO with no writer) must fail immediately without blocking.
        fifo_path = base / 'env.fifo'
        n.os.mkfifo(fifo_path, 0o600)
        with patch.object(n, 'CONFIG', fifo_path):
            try:
                n.load_config()
            except PermissionError as exc:
                assert 'regular file' in str(exc), exc
            else:
                raise AssertionError('FIFO config path accepted')

        # PYMOBILEDEVICE3 validation must reject injection/non-executable paths before writing any unit.
        fake_home = base / 'home'
        fake_home.mkdir()
        good_bin = base / 'pymobiledevice3'
        good_bin.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        good_bin.chmod(0o755)
        non_exec = base / 'non_exec'
        non_exec.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        non_exec.chmod(0o644)
        bad_paths = (
            'relative/pymobiledevice3',
            f'{good_bin} --extra',
            f'{good_bin}\nExecStartPost=/bin/sh',
            f'{base}/%n/pymobiledevice3',
            f'{base}/$HOME/pymobiledevice3',
            f'{base}/back\\slash',
            f'{base}/single\'quote',
            f'{base}/double"quote',
            f'{base}/semi;colon',
            str(base / 'missing'),
            str(non_exec),
        )
        for bad in bad_paths:
            bad_cfg = {'SURGE_API_USB': 'http://127.0.0.1:16171', 'PYMOBILEDEVICE3': bad}
            with patch.object(n, 'load_config', return_value=bad_cfg), \
                    patch.object(Path, 'home', return_value=fake_home), \
                    patch.object(n.subprocess, 'run') as mock_run:
                try:
                    n.cmd_install_service(None)
                except ValueError:
                    pass
                else:
                    raise AssertionError(f'unsafe PYMOBILEDEVICE3 accepted: {bad!r}')
                assert not (fake_home / '.config/systemd/user' / n.SERVICE).exists(), bad
                assert not (fake_home / '.config/systemd/user' / n.USB_SERVICE).exists(), bad
                assert mock_run.call_count == 0, bad

        # Valid symlinked executable passes validation and writes both units.
        symlink_bin = base / 'pymd-link'
        symlink_bin.symlink_to(good_bin)
        good_cfg = {'SURGE_API_USB': 'http://127.0.0.1:16171', 'PYMOBILEDEVICE3': str(symlink_bin)}
        with patch.object(n, 'load_config', return_value=good_cfg), \
                patch.object(Path, 'home', return_value=fake_home), \
                patch.object(n.subprocess, 'run') as mock_run:
            assert n.cmd_install_service(None) == 0
            assert (fake_home / '.config/systemd/user' / n.SERVICE).is_file()
            usb_text = (fake_home / '.config/systemd/user' / n.USB_SERVICE).read_text(encoding='utf-8')
            assert f'ExecStart={symlink_bin} usbmux forward 16171 6171' in usb_text
            assert mock_run.call_count == 5

    print('Netdiag security cases passed.')


if __name__ == '__main__':
    main()
