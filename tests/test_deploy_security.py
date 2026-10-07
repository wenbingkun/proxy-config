#!/usr/bin/env python3
"""Concurrent lock and signal/command failure recovery against temporary ShellCrash."""
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

from test_deploy_shellcrash import DEPLOY_SCRIPT, TEMPLATE, TEST_PROVIDER_URL_1_CHANGED, write_env, write_executable


def wait_for(path, process):
    deadline = time.monotonic() + 10
    while not path.exists():
        if process.poll() is not None or time.monotonic() >= deadline:
            raise AssertionError('deployment did not reach fault boundary')
        time.sleep(.02)


def main():
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        root = base / 'ShellCrash'
        for part in ('configs', 'yamls', 'bin', 'cache/proxy-providers'):
            (root / part).mkdir(parents=True)
        fake = base / 'fake'; fake.mkdir()
        (root / 'configs/ShellCrash.cfg').write_text('disoverride=0\n')
        (root / 'configs/command.env').write_text(f'BINDIR={root}\nTMPDIR={root}/bin\n')
        template = base / 'template'; template.write_text(TEMPLATE)
        write_executable(fake / 'curl', '''#!/bin/sh
while [ "$#" -gt 0 ]; do
    if [ "$1" = -o ]; then output=$2; shift 2; else shift; fi
done
cp "$FIXTURE_TEMPLATE" "$output"
''')
        write_executable(root / 'bin/CrashCore', '#!/bin/sh\nexit 0\n')
        write_executable(root / 'start.sh', '''#!/bin/sh
set -eu
root=${0%/*}
printf '%s\\n' "$1" >>"$root/calls"
if [ "$1" = stop ] && [ -f "$root/interrupt-stop" ]; then
    rm "$root/interrupt-stop"
    kill -TERM "$PPID"
fi
if [ "$1" = start ]; then
    touch "$root/started"
fi
''')
        config = root / 'yamls/config.yaml'
        cache = root / 'cache/proxy-providers/sub.yaml'
        cache2 = root / 'cache/proxy-providers/sub2.yaml'
        old = TEMPLATE.replace('https://example.com/__SUB_URL_1__', 'https://old.test/sub').replace('https://example.com/__SUB_URL_2__', 'https://old.test/sub2').encode()
        env = base / 'providers.env'
        write_env(env, root, 'good.yaml', provider_url_1=TEST_PROVIDER_URL_1_CHANGED)
        process_env = dict(os.environ, PATH=str(fake) + os.pathsep + os.environ['PATH'], FIXTURE_TEMPLATE=str(template))
        lock = root / 'configs/.proxy-config-deploy.lock'

        def reset():
            config.write_bytes(old); cache.write_bytes(b'proxies:\n  - name: old-one\n'); cache2.write_bytes(b'proxies:\n  - name: old-two\n')
            (root / 'calls').write_text('')
            (root / 'started').unlink(missing_ok=True)

        def launch():
            return subprocess.Popen(['sh', str(DEPLOY_SCRIPT), str(env)], env=process_env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)

        # A live lock is unchanged by any number of contenders.
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        start = Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(') ', 1)[1].split()[19]
        lock.mkdir(); owner = f'{os.getpid()} {boot} {start}\n'; (lock / 'owner').write_text(owner)
        for _ in range(2):
            p = launch(); p.communicate(timeout=10)
            assert p.returncode and (lock / 'owner').read_text() == owner
        (lock / 'owner').unlink(); lock.rmdir()

        # An interrupted stop recovers service before any cache mutation.
        reset(); (root / 'interrupt-stop').touch()
        p = launch(); output = p.communicate(timeout=10)
        assert p.returncode == 143, output
        assert config.read_bytes() == old and b'old-one' in cache.read_bytes()
        assert (root / 'calls').read_text().splitlines() == ['stop', 'stop', 'start']
        assert not lock.exists()

        # SIGTERM during the actual provider-confirmation loop restores both caches.
        reset()
        with env.open('a') as f: f.write('SHELLCRASH_PROVIDER_WAIT=30\n')
        p = launch()
        try:
            wait_for(root / 'started', p)
            # The new config is installed but no subscription nodes are fetched.
            assert config.read_bytes() != old and not cache.exists()
            p.send_signal(signal.SIGTERM); output = p.communicate(timeout=10)
            assert p.returncode == 143, output
            assert config.read_bytes() == old and b'old-one' in cache.read_bytes() and b'old-two' in cache2.read_bytes()
            assert (root / 'calls').read_text().splitlines() == ['stop', 'start', 'stop', 'start']
            assert not lock.exists()
        finally:
            if p.poll() is None: os.killpg(p.pid, signal.SIGKILL); p.communicate()

        # An ordinary atomic-install command failure triggers the same recovery.
        reset()
        write_executable(fake / 'mv', '\n'.join([
            '#!/bin/sh', 'for arg do', 'case "$arg" in',
            '*/.config.yaml.new.*) exit 73 ;;', 'esac', 'done', 'exec /bin/mv "$@"', '']))
        p = launch(); output = p.communicate(timeout=10)
        assert p.returncode and config.read_bytes() == old and b'old-one' in cache.read_bytes() and b'old-two' in cache2.read_bytes(), output
        assert (root / 'calls').read_text().splitlines() == ['stop', 'stop', 'start']
        (fake / 'mv').unlink()

        # When rollback cannot stop the new core, preserve the original snapshot
        # and report failure rather than claiming that file/service recovery succeeded.
        reset()
        write_executable(root / 'start.sh', '\n'.join([
            '#!/bin/sh', 'root=${0%/*}',
            'if [ "$1" = stop ] && [ -f "$root/started" ]; then exit 1; fi',
            'if [ "$1" = start ]; then touch "$root/started"; fi', 'exit 0', '']))
        with env.open('a') as f: f.write('SHELLCRASH_PROVIDER_WAIT=0\n')
        p = launch(); output = p.communicate(timeout=10)
        assert p.returncode and '回滚停止核心失败'.encode() in output[1], output
        assert config.read_bytes() != old
        message = output[1].decode()
        recovery = Path(message.split('保留恢复材料：', 1)[1].split('；', 1)[0])
        assert (recovery / 'provider-cache-backup/sub.yaml').read_bytes() == b'proxies:\n  - name: old-one\n'
        assert (root / 'yamls/config.yaml.bak.proxy-config').read_bytes() == old
        # This temporary test owns the preserved directory, never real device files.
        import shutil
        shutil.rmtree(recovery)
        write_executable(root / 'start.sh', '#!/bin/sh\nexit 0\n')

        # Proven-dead boot identity is reclaimed; unknown legacy ownership is retained.
        reset(); lock.mkdir(); (lock / 'owner').write_text('123 old-boot 1\n')
        with env.open('a') as f: f.write('SHELLCRASH_PROVIDER_WAIT=0\n')
        p = launch(); p.communicate(timeout=10)
        assert p.returncode and not lock.exists() and config.read_bytes() == old
        lock.mkdir()
        p = launch(); output = p.communicate(timeout=10)
        assert p.returncode and lock.exists() and b'old-one' in cache.read_bytes()
    print('Deployment security concurrency and signal cases passed.')


if __name__ == '__main__':
    main()
