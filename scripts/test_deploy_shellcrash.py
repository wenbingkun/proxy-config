#!/usr/bin/env python3
"""Exercise the ShellCrash deployment transaction without a router or network."""

from __future__ import annotations

import os
import shlex
import signal
import stat
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_shellcrash_config.sh"
DEPLOY_TIMEOUT = 60
SYSTEM_BIN_DIRS = ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin")

TEST_PROVIDER_URL_1 = "https://provider.test/sub?auth=fixture-one&mode=clash|meta"
TEST_PROVIDER_URL_2 = "https://provider.test/sub?auth=fixture-two&mode=clash|meta"
TEST_PROVIDER_URL_1_CHANGED = (
    "https://provider.test/sub?auth=fixture-one-changed&mode=clash|meta"
)

TEMPLATE = """\
proxy-providers:
  Sub:
    type: http
    url: "https://example.com/__SUB_URL_1__"
  Sub2:
    type: http
    url: "https://example.com/__SUB_URL_2__"
proxy-groups:
  - name: PROXY
    type: select
    use:
      - Sub
      - Sub2
rules:
  - MATCH,DIRECT
"""

SINGLE_TEMPLATE = """\
proxy-providers:
  Sub:
    type: http
    url: "https://example.com/__SUB_URL_1__"
proxy-groups:
  - name: PROXY
    type: select
    use:
      - Sub
rules:
  - MATCH,DIRECT
"""


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def write_env(
    path: Path,
    shellcrash_dir: Path,
    template_name: str,
    provider_url_1: str = TEST_PROVIDER_URL_1,
    provider_url_2: str = TEST_PROVIDER_URL_2,
    mihomo_bin: Path | None = None,
    template_url: str | None = None,
) -> None:
    values = {
        "SHELLCRASH_DIR": str(shellcrash_dir),
        "TEMPLATE_URL": template_url or f"https://fixture.invalid/{template_name}",
        "SUB_URL_1": provider_url_1,
        "SUB_URL_2": provider_url_2,
        "SHELLCRASH_STARTUP_WAIT": "0",
        "SHELLCRASH_PROVIDER_WAIT": "0",
        "SHELLCRASH_SKIP_PROCESS_CHECK": "1",
    }
    if mihomo_bin is not None:
        values["MIHOMO_BIN"] = str(mihomo_bin)
    path.write_text(
        "\n".join(f"{key}={shlex.quote(value)}" for key, value in values.items())
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def run_deploy(
    env_path: Path, process_env: dict[str, str], should_succeed: bool
) -> str:
    # Run in its own process group so a hung deployment and every child it
    # spawned (curl, sleep, ...) are killed together on timeout.
    process = subprocess.Popen(
        ["sh", str(DEPLOY_SCRIPT), str(env_path)],
        cwd=ROOT,
        env=process_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=DEPLOY_TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise AssertionError(f"deployment did not finish within {DEPLOY_TIMEOUT} seconds")
    combined = stdout + stderr
    provider_urls = (
        TEST_PROVIDER_URL_1,
        TEST_PROVIDER_URL_2,
        TEST_PROVIDER_URL_1_CHANGED,
    )
    if any(provider_url in combined for provider_url in provider_urls):
        raise AssertionError("deployment output exposed a provider URL")
    if (process.returncode == 0) != should_succeed:
        raise AssertionError(
            f"unexpected deployment exit code {process.returncode}\n{combined}"
        )
    return combined


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="proxy-config-deploy-test-") as temp:
        base = Path(temp)
        shellcrash_dir = base / "ShellCrash"
        yamls_dir = shellcrash_dir / "yamls"
        bin_dir = shellcrash_dir / "bin"
        configs_dir = shellcrash_dir / "configs"
        fixtures_dir = base / "fixtures"
        fake_path = base / "fake-path"
        provider_cache_dir = shellcrash_dir / "cache" / "proxy-providers"
        for directory in (
            yamls_dir,
            bin_dir,
            configs_dir,
            fixtures_dir,
            fake_path,
            provider_cache_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        config_path = yamls_dir / "config.yaml"
        backup_path = yamls_dir / "config.yaml.bak.proxy-config"
        env_path = base / "providers.env"

        (configs_dir / "command.env").write_text(
            f"TMPDIR={shlex.quote(str(bin_dir))}\n"
            f"BINDIR={shlex.quote(str(shellcrash_dir))}\n",
            encoding="utf-8",
        )
        shellcrash_cfg = configs_dir / "ShellCrash.cfg"
        shellcrash_cfg.write_text("disoverride=0\n", encoding="utf-8")
        write_executable(
            fake_path / "curl",
            """#!/bin/sh
set -eu
output=''
url=''
connect_timeout=''
max_time=''
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o)
            output=$2
            shift 2
            ;;
        --connect-timeout)
            connect_timeout=$2
            shift 2
            ;;
        --max-time)
            max_time=$2
            shift 2
            ;;
        -*)
            shift
            ;;
        *)
            url=$1
            shift
            ;;
    esac
done
[ -n "$output" ] && [ -n "$url" ]
# A real curl without both limits can block forever on an unreachable host.
for limit in "$connect_timeout" "$max_time"; do
    case "$limit" in
        ''|*[!0-9]*) limit=0 ;;
    esac
    if [ "$limit" -le 0 ]; then
        printf 'fake curl: missing or non-positive timeout\\n' >&2
        exit 97
    fi
done
printf '%s %s\\n' "$connect_timeout" "$max_time" >>"$FAKE_HTTP_ROOT/curl_limits"
name=${url%%\\?*}
name=${name##*/}
case "$name" in
    unreachable.yaml)
        printf 'curl: (28) Failed to connect to %s\\n' "$url" >&2
        exit 28
        ;;
    partial.yaml)
        printf 'payload:\\n  - partial' >"$output"
        printf 'curl: (18) transfer closed for %s\\n' "$url" >&2
        exit 18
        ;;
esac
cp "$FAKE_HTTP_ROOT/$name" "$output"
""",
        )
        core_path = shellcrash_dir / "CrashCore.raw"
        core_script = """#!/bin/sh
set -eu
config=''
while [ "$#" -gt 0 ]; do
    if [ "$1" = '-f' ]; then
        config=$2
        shift 2
    else
        shift
    fi
done
[ -n "$config" ]
! grep -q 'BROKEN_YAML' "$config"
"""
        write_executable(
            shellcrash_dir / "start.sh",
            """#!/bin/sh
set -eu
action=${1:-}
root=$(dirname "$0")
printf '%s\n' "$action" >>"$root/start_calls"
if [ "$action" = 'start' ] && [ -f "$root/start_should_fail" ]; then
    exit 1
fi
if [ "$action" = 'start' ] && [ ! -f "$root/provider_fetch_should_fail" ]; then
    cache_dir="$root/cache/proxy-providers"
    config="$root/yamls/config.yaml"
    mkdir -p "$cache_dir"
    if grep -q '^  Sub:$' "$config" && [ ! -f "$cache_dir/sub.yaml" ]; then
        if [ -f "$root/provider_fetch_empty" ]; then
            printf '%s\n' 'proxies: []' >"$cache_dir/sub.yaml"
        else
            printf '%s\n' 'proxies:' '  - name: fixture-sub-node' >"$cache_dir/sub.yaml"
        fi
    fi
    if grep -q '^  Sub2:$' "$config" && [ ! -f "$cache_dir/sub2.yaml" ]; then
        if [ -f "$root/provider_fetch_empty" ]; then
            printf '%s\n' 'proxies: []' >"$cache_dir/sub2.yaml"
        else
            printf '%s\n' 'proxies:' '  - name: fixture-sub2-node' >"$cache_dir/sub2.yaml"
        fi
    fi
fi
exit 0
""",
        )

        (fixtures_dir / "good.yaml").write_text(TEMPLATE, encoding="utf-8")
        (fixtures_dir / "single.yaml").write_text(
            SINGLE_TEMPLATE, encoding="utf-8"
        )
        (fixtures_dir / "changed.yaml").write_text(
            "# changed template\n" + TEMPLATE, encoding="utf-8"
        )
        (fixtures_dir / "broken.yaml").write_text(
            "BROKEN_YAML\n" + TEMPLATE, encoding="utf-8"
        )
        (fixtures_dir / "duplicate-placeholder.yaml").write_text(
            TEMPLATE.replace(
                'url: "https://example.com/__SUB_URL_1__"',
                'url: "https://example.com/__SUB_URL_1__https://example.com/__SUB_URL_1__"',
            ),
            encoding="utf-8",
        )

        original = b"# original config\nrules:\n  - MATCH,DIRECT\n"

        process_env = os.environ.copy()
        process_env["PATH"] = f"{fake_path}{os.pathsep}{process_env['PATH']}"
        process_env["FAKE_HTTP_ROOT"] = str(fixtures_dir)

        # A brand-new install has neither config nor core. ShellCrash owns the
        # first core download, so deployment must still be able to bootstrap.
        write_env(
            env_path,
            shellcrash_dir,
            "single.yaml",
            provider_url_2="",
        )
        run_deploy(env_path, process_env, should_succeed=True)
        bootstrap_config = config_path.read_bytes()
        assert TEST_PROVIDER_URL_1.encode() in bootstrap_config
        assert TEST_PROVIDER_URL_2.encode() not in bootstrap_config
        assert b"Sub2" not in bootstrap_config
        assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
        assert not backup_path.exists()

        # Once a config exists, absence of a core must never permit overwrite.
        config_path.write_bytes(b"# existing without core\n")
        write_env(env_path, shellcrash_dir, "changed.yaml")
        run_deploy(env_path, process_env, should_succeed=False)
        assert config_path.read_bytes() == b"# existing without core\n"

        write_executable(core_path, core_script)
        config_path.write_bytes(original)
        config_path.chmod(0o640)
        write_env(
            env_path,
            shellcrash_dir,
            "single.yaml",
            provider_url_2="",
        )
        run_deploy(env_path, process_env, should_succeed=True)
        single_deployed = config_path.read_bytes()
        assert TEST_PROVIDER_URL_1.encode() in single_deployed
        assert TEST_PROVIDER_URL_2.encode() not in single_deployed
        assert b"Sub2" not in single_deployed
        assert backup_path.read_bytes() == original

        config_path.write_bytes(original)
        config_path.chmod(0o640)
        write_env(env_path, shellcrash_dir, "good.yaml")
        run_deploy(env_path, process_env, should_succeed=True)
        deployed = config_path.read_bytes()
        assert TEST_PROVIDER_URL_1.encode() in deployed
        assert TEST_PROVIDER_URL_2.encode() in deployed
        assert b"__SUB_URL_" not in deployed
        assert backup_path.read_bytes() == original
        assert stat.S_IMODE(config_path.stat().st_mode) == 0o640

        run_deploy(env_path, process_env, should_succeed=True)
        assert config_path.read_bytes() == deployed

        # Unchanged provider URLs must preserve both caches and avoid an
        # additional stop/start cycle solely for cache invalidation.
        sub_cache = provider_cache_dir / "sub.yaml"
        sub2_cache = provider_cache_dir / "sub2.yaml"
        start_calls = shellcrash_dir / "start_calls"
        sub_cache.write_bytes(b"proxies:\n  - name: sub-current\n")
        sub2_cache.write_bytes(b"proxies:\n  - name: sub2-current\n")
        start_calls.write_text("", encoding="utf-8")
        run_deploy(env_path, process_env, should_succeed=True)
        assert sub_cache.read_bytes() == b"proxies:\n  - name: sub-current\n"
        assert sub2_cache.read_bytes() == b"proxies:\n  - name: sub2-current\n"
        assert start_calls.read_text(encoding="utf-8").splitlines() == ["start"]

        # Dual -> single keeps Sub unchanged and invalidates only stale Sub2.
        start_calls.write_text("", encoding="utf-8")
        write_env(env_path, shellcrash_dir, "single.yaml", provider_url_2="")
        run_deploy(env_path, process_env, should_succeed=True)
        assert sub_cache.read_bytes() == b"proxies:\n  - name: sub-current\n"
        assert not sub2_cache.exists()
        assert start_calls.read_text(encoding="utf-8").splitlines() == [
            "stop",
            "start",
        ]

        # Single -> dual keeps Sub unchanged and invalidates a stale Sub2 cache.
        sub2_cache.write_bytes(b"proxies:\n  - name: sub2-stale\n")
        start_calls.write_text("", encoding="utf-8")
        write_env(env_path, shellcrash_dir, "good.yaml")
        run_deploy(env_path, process_env, should_succeed=True)
        assert sub_cache.read_bytes() == b"proxies:\n  - name: sub-current\n"
        assert b"fixture-sub2-node" in sub2_cache.read_bytes()
        assert start_calls.read_text(encoding="utf-8").splitlines() == [
            "stop",
            "start",
        ]

        # Provider A -> B invalidates only Sub because Sub2's URL is unchanged.
        sub_cache.write_bytes(b"proxies:\n  - name: sub-a\n")
        sub2_cache.write_bytes(b"proxies:\n  - name: sub2-current\n")
        start_calls.write_text("", encoding="utf-8")
        write_env(
            env_path,
            shellcrash_dir,
            "good.yaml",
            provider_url_1=TEST_PROVIDER_URL_1_CHANGED,
        )
        run_deploy(env_path, process_env, should_succeed=True)
        deployed = config_path.read_bytes()
        assert TEST_PROVIDER_URL_1_CHANGED.encode() in deployed
        assert b"fixture-sub-node" in sub_cache.read_bytes()
        assert sub2_cache.read_bytes() == b"proxies:\n  - name: sub2-current\n"
        assert start_calls.read_text(encoding="utf-8").splitlines() == [
            "stop",
            "start",
        ]

        write_env(env_path, shellcrash_dir, "broken.yaml")
        run_deploy(env_path, process_env, should_succeed=False)
        assert config_path.read_bytes() == deployed

        # Every download must pass positive curl limits (15s connect, 120s total).
        curl_limits = fixtures_dir / "curl_limits"
        assert set(curl_limits.read_text(encoding="utf-8").splitlines()) == {"15 120"}

        # Download failures (unreachable host, partial transfer) must leave the
        # config, provider caches, lock and temp dir untouched, never restart
        # ShellCrash, and never echo a credential-bearing TEMPLATE_URL.
        secret_parts = ("fake-user", "fake-pass", "secret-path-7f3a", "placeholder-9c1e")
        tmp_root = base / "deploy-tmp"
        tmp_root.mkdir()
        process_env["SHELLCRASH_TMP_ROOT"] = str(tmp_root)
        lock_dir = configs_dir / ".proxy-config-deploy.lock"
        caches_before = {p.name: p.read_bytes() for p in provider_cache_dir.iterdir()}
        for template_name, status in (("unreachable.yaml", 28), ("partial.yaml", 18)):
            start_calls.write_text("", encoding="utf-8")
            write_env(
                env_path,
                shellcrash_dir,
                template_name,
                template_url=(
                    "https://fake-user:fake-pass@fixture.invalid/secret-path-7f3a/"
                    f"{template_name}?token=placeholder-9c1e"
                ),
            )
            output = run_deploy(env_path, process_env, should_succeed=False)
            assert f"curl 退出码 {status}" in output, output
            assert not any(part in output for part in secret_parts), output
            assert config_path.read_bytes() == deployed
            assert {p.name: p.read_bytes() for p in provider_cache_dir.iterdir()} == caches_before
            assert start_calls.read_text(encoding="utf-8") == ""
            assert not lock_dir.exists()
            assert list(tmp_root.iterdir()) == []

        # The lock was released: the next run acquires it and proceeds to
        # template validation, which rejects this fixture later on.
        write_env(env_path, shellcrash_dir, "broken.yaml")
        output = run_deploy(env_path, process_env, should_succeed=False)
        assert "已有配置部署任务正在运行" not in output, output
        assert "curl 退出码" not in output, output
        assert config_path.read_bytes() == deployed
        assert not lock_dir.exists()
        assert list(tmp_root.iterdir()) == []
        del process_env["SHELLCRASH_TMP_ROOT"]

        # Without curl (and with no wget fallback) the script stops before
        # locking or touching anything.
        no_curl_path = base / "no-curl-path"
        no_curl_path.mkdir()
        for directory in SYSTEM_BIN_DIRS:
            if not os.path.isdir(directory):
                continue
            for entry in os.scandir(directory):
                link = no_curl_path / entry.name
                if entry.name in ("curl", "wget") or link.exists() or link.is_symlink():
                    continue
                if entry.is_file() and os.access(entry.path, os.X_OK):
                    link.symlink_to(entry.path)
        no_curl_env = dict(process_env, PATH=str(no_curl_path))
        output = run_deploy(env_path, no_curl_env, should_succeed=False)
        assert "未找到 curl" in output, output
        assert config_path.read_bytes() == deployed
        assert not lock_dir.exists()

        write_env(env_path, shellcrash_dir, "duplicate-placeholder.yaml")
        run_deploy(env_path, process_env, should_succeed=False)
        assert config_path.read_bytes() == deployed

        write_env(
            env_path,
            shellcrash_dir,
            "good.yaml",
            provider_url_1="ftp://invalid.test/sub",
        )
        run_deploy(env_path, process_env, should_succeed=False)
        assert config_path.read_bytes() == deployed

        # A failed start restores both the previous config and the invalidated
        # provider cache before attempting to restart the old configuration.
        sub_cache.write_bytes(b"proxies:\n  - name: sub-before-failed-deploy\n")
        (shellcrash_dir / "start_should_fail").touch()
        write_env(env_path, shellcrash_dir, "changed.yaml")
        run_deploy(env_path, process_env, should_succeed=False)
        assert config_path.read_bytes() == deployed
        assert sub_cache.read_bytes() == (
            b"proxies:\n  - name: sub-before-failed-deploy\n"
        )

        (shellcrash_dir / "start_should_fail").unlink()

        # A running core is not enough: a changed provider that cannot produce
        # a non-empty proxies list must fail with a useful, secret-free hint and
        # restore the previous config and provider cache.
        previous_config = config_path.read_bytes()
        previous_cache = sub_cache.read_bytes()
        (shellcrash_dir / "provider_fetch_should_fail").touch()
        write_env(
            env_path,
            shellcrash_dir,
            "good.yaml",
            provider_url_1=TEST_PROVIDER_URL_1,
        )
        output = run_deploy(env_path, process_env, should_succeed=False)
        assert "Sub 订阅获取失败" in output
        assert "订阅导入或客户端导入开关" in output
        assert config_path.read_bytes() == previous_config
        assert sub_cache.read_bytes() == previous_cache
        (shellcrash_dir / "provider_fetch_should_fail").unlink()

        # A fetched provider file with an empty proxies list is also a failed
        # import, not a successful deployment.
        (shellcrash_dir / "provider_fetch_empty").touch()
        write_env(
            env_path,
            shellcrash_dir,
            "good.yaml",
            provider_url_1=TEST_PROVIDER_URL_1,
        )
        output = run_deploy(env_path, process_env, should_succeed=False)
        assert "Sub 订阅获取失败" in output
        assert config_path.read_bytes() == previous_config
        assert sub_cache.read_bytes() == previous_cache
        (shellcrash_dir / "provider_fetch_empty").unlink()

        shellcrash_cfg.write_text("disoverride=1\n", encoding="utf-8")
        write_env(env_path, shellcrash_dir, "good.yaml")
        run_deploy(env_path, process_env, should_succeed=False)
        assert config_path.read_bytes() == deployed

        shellcrash_cfg.write_text("disoverride=0\n", encoding="utf-8")
        write_env(env_path, shellcrash_dir, "good.yaml")
        with env_path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"SHELLCRASH_CONFIG_PATH={shlex.quote(str(yamls_dir / '..' / 'escaped.yaml'))}\n"
            )
        run_deploy(env_path, process_env, should_succeed=False)
        assert config_path.read_bytes() == deployed

    print("ShellCrash deployment transaction tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
