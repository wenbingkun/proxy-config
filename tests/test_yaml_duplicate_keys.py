#!/usr/bin/env python3
"""Repeated YAML keys fail hygiene instead of silently keeping the last value."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import check_hygiene as hygiene


def main():
    find = hygiene.find_duplicate_keys
    assert find('domain_suffix:\n  - keep-first.example\ndomain_suffix:\n  - keep-second.example\n') == [
        ('domain_suffix', 3)
    ]
    assert find('proxy-providers:\n  Sub:\n    type: http\n    url: a\n    type: file\n') == [('type', 5)]
    assert find('rules:\n  - {name: a, name: b}\n') == [('name', 2)]
    # A merge followed by an explicit override is intentional, not a repeat.
    assert find('defaults: &d {value: 1}\nconfig: {<<: *d, value: 2}\n') == []
    # Shared and self-referencing aliases are visited once.
    assert find('a: &x {k: 1}\nb: *x\nc: *x\n') == []
    assert find('a: &x [*x]\n') == []
    assert find('') == []

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        rules = root / 'rules'
        rules.mkdir()
        (rules / 'sample.yaml').write_text('domain_suffix:\n  - a.example\ndomain_suffix:\n  - b.example\n')
        output = io.StringIO()
        with patch.object(hygiene, 'ROOT', root), patch.object(hygiene, 'RULES_DIR', rules), \
                redirect_stdout(output):
            code = hygiene.main()
        assert code == 1, output.getvalue()
        assert "rules/sample.yaml:3: duplicate key 'domain_suffix'" in output.getvalue(), output.getvalue()
    print('YAML duplicate key checks passed.')


if __name__ == '__main__':
    main()
