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
    print('Netdiag security cases passed.')


if __name__ == '__main__':
    main()
