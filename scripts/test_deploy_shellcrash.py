#!/usr/bin/env python3
"""Exercise the ShellCrash deployment transaction without a router or network."""

from __future__ import annotations

import os
import shlex
import stat
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_shellcrash_config.sh"

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
) -> None:
    values = {
        "SHELLCRASH_DIR": str(shellcrash_dir),
        "TEMPLATE_URL": f"https://fixture.invalid/{template_name}",
        "SUB_URL_1": provider_url_1,
        "SUB_URL_2": provider_url_2,
        "SHELLCRASH_STARTUP_WAIT": "0",
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


def run_deploy(env_path: Path, process_env: dict[str, str], should_succeed: bool) -> None:
    result = subprocess.run(
        ["sh", str(DEPLOY_SCRIPT), str(env_path)],
        cwd=ROOT,
        env=process_env,
        text=True,
        capture_output=True,
        check=False,
    )
    combined = result.stdout + result.stderr
    provider_urls = (
        TEST_PROVIDER_URL_1,
        TEST_PROVIDER_URL_2,
        TEST_PROVIDER_URL_1_CHANGED,
    )
    if any(provider_url in combined for provider_url in provider_urls):
        raise AssertionError("deployment output exposed a provider URL")
    if (result.returncode == 0) != should_succeed:
        raise AssertionError(
            f"unexpected deployment exit code {result.returncode}\n{combined}"
        )


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
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o)
            output=$2
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
cp "$FAKE_HTTP_ROOT/${url##*/}" "$output"
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
printf '%s\n' "$action" >>"$(dirname "$0")/start_calls"
if [ "$action" = 'start' ] && [ -f "$(dirname "$0")/start_should_fail" ]; then
    exit 1
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
        sub_cache.write_bytes(b"sub-current\n")
        sub2_cache.write_bytes(b"sub2-current\n")
        start_calls.write_text("", encoding="utf-8")
        run_deploy(env_path, process_env, should_succeed=True)
        assert sub_cache.read_bytes() == b"sub-current\n"
        assert sub2_cache.read_bytes() == b"sub2-current\n"
        assert start_calls.read_text(encoding="utf-8").splitlines() == ["start"]

        # Dual -> single keeps Sub unchanged and invalidates only stale Sub2.
        start_calls.write_text("", encoding="utf-8")
        write_env(env_path, shellcrash_dir, "single.yaml", provider_url_2="")
        run_deploy(env_path, process_env, should_succeed=True)
        assert sub_cache.read_bytes() == b"sub-current\n"
        assert not sub2_cache.exists()
        assert start_calls.read_text(encoding="utf-8").splitlines() == [
            "stop",
            "start",
        ]

        # Single -> dual keeps Sub unchanged and invalidates a stale Sub2 cache.
        sub2_cache.write_bytes(b"sub2-stale\n")
        start_calls.write_text("", encoding="utf-8")
        write_env(env_path, shellcrash_dir, "good.yaml")
        run_deploy(env_path, process_env, should_succeed=True)
        assert sub_cache.read_bytes() == b"sub-current\n"
        assert not sub2_cache.exists()
        assert start_calls.read_text(encoding="utf-8").splitlines() == [
            "stop",
            "start",
        ]

        # Provider A -> B invalidates only Sub because Sub2's URL is unchanged.
        sub_cache.write_bytes(b"sub-a\n")
        sub2_cache.write_bytes(b"sub2-current\n")
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
        assert not sub_cache.exists()
        assert sub2_cache.read_bytes() == b"sub2-current\n"
        assert start_calls.read_text(encoding="utf-8").splitlines() == [
            "stop",
            "start",
        ]

        write_env(env_path, shellcrash_dir, "broken.yaml")
        run_deploy(env_path, process_env, should_succeed=False)
        assert config_path.read_bytes() == deployed

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
        sub_cache.write_bytes(b"sub-before-failed-deploy\n")
        (shellcrash_dir / "start_should_fail").touch()
        write_env(env_path, shellcrash_dir, "changed.yaml")
        run_deploy(env_path, process_env, should_succeed=False)
        assert config_path.read_bytes() == deployed
        assert sub_cache.read_bytes() == b"sub-before-failed-deploy\n"

        (shellcrash_dir / "start_should_fail").unlink()
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
