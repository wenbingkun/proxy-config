#!/usr/bin/env python3
"""Verify the router template through ShellCrash's real override implementation."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE_PATH = ROOT / "clash" / "config-router.template.yaml"
POLICY_SECTIONS = (
    "proxies",
    "proxy-providers",
    "proxy-groups",
    "rule-providers",
    "sub-rules",
    "rules",
    "listeners",
)
RUNTIME_SECTIONS = {
    "mixed-port",
    "dns",
    "tun",
    "external-controller",
}


def load_yaml(path: Path) -> dict[str, object]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a top-level mapping")
    return data


def run_checked(command: list[str], env: dict[str, str] | None = None) -> None:
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        output = result.stdout + result.stderr
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}\n{output}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test the router template with a pinned ShellCrash override script."
    )
    parser.add_argument("--modify-script", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--mmdb", required=True, type=Path)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE_PATH)
    args = parser.parse_args()

    modify_script = args.modify_script.resolve()
    core = args.core.resolve()
    mmdb = args.mmdb.resolve()
    template_path = args.template.resolve()
    for path in (modify_script, core, mmdb, template_path):
        if not path.is_file():
            parser.error(f"required file does not exist: {path}")
    if not os.access(core, os.X_OK):
        parser.error(f"core is not executable: {core}")

    template = load_yaml(template_path)

    with tempfile.TemporaryDirectory(prefix="proxy-config-shellcrash-override-") as temp:
        base = Path(temp)
        install_dir = base / "install"
        yamls_dir = install_dir / "yamls"
        runtime_dir = base / "runtime"
        data_dir = base / "data"
        for directory in (yamls_dir, runtime_dir, data_dir / "ruleset"):
            directory.mkdir(parents=True, exist_ok=True)

        config_path = yamls_dir / "config.yaml"
        shutil.copyfile(template_path, config_path)
        (runtime_dir / "CrashCore").symlink_to(core)
        shutil.copyfile(mmdb, data_dir / "Country.mmdb")

        shell_env = os.environ.copy()
        shell_env.update(
            {
                "CRASHDIR": str(install_dir),
                "TMPDIR": str(runtime_dir),
                "BINDIR": str(data_dir),
                "core_config": str(config_path),
                "SHELLCRASH_MODIFY_SCRIPT": str(modify_script),
                "ipv6_dns": "OFF",
                "dns_mod": "redir_host",
                "redir_mod": "Redir",
                "crashcore": "meta",
                "db_port": "9999",
                "dns_port": "1053",
                "mix_port": "7890",
                "redir_port": "7892",
                "tproxy_port": "7893",
                "authentication": "fixture:fixture",
                "external_ui_url": "",
                "secret": "fixture-secret",
                "routing_mark": "7777",
                "dns_resolver": "223.5.5.5",
                "dns_nameserver": "223.5.5.5",
                "dns_fallback": "8.8.8.8",
                "dns_proxy_server": "8.8.8.8",
                "hosts_opt": "OFF",
                "sniffer": "OFF",
                "skip_cert": "OFF",
                "proxies_bypass": "OFF",
                "vms_service": "OFF",
                "sss_service": "OFF",
            }
        )
        run_checked(
            ["sh", "-c", '. "$SHELLCRASH_MODIFY_SCRIPT"; modify_yaml'],
            env=shell_env,
        )

        error_path = runtime_dir / "error.yaml"
        if error_path.exists():
            raise RuntimeError("ShellCrash rejected the customized config and used fallback")

        merged_path = runtime_dir / "config.yaml"
        merged = load_yaml(merged_path)
        missing_runtime = sorted(RUNTIME_SECTIONS - set(merged))
        if missing_runtime:
            raise ValueError(
                "ShellCrash merged config is missing runtime sections: "
                + ", ".join(missing_runtime)
            )
        for section in POLICY_SECTIONS:
            if section in template and merged.get(section) != template[section]:
                raise ValueError(
                    f"ShellCrash changed or lost the template policy section {section!r}"
                )

        run_checked(
            [str(core), "-t", "-d", str(data_dir), "-f", str(merged_path)]
        )

    print(
        "ShellCrash official override integration test passed: "
        f"{template_path.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
