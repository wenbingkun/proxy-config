#!/bin/sh
# 提交前检查与 CI 共用的一份清单：任一步失败即以非零状态退出。
# tests/test_shellcrash_override.py 需要固定版本的核心与 mmdb，只在 CI 单独运行。
set -eu

offline=0
if [ "$#" -eq 0 ]; then
  :
elif [ "$#" -eq 1 ] && [ "${1-}" = "--offline" ]; then
  offline=1
else
  echo "Usage: sh scripts/check_all.sh [--offline]" >&2
  exit 2
fi

cd "$(dirname "$0")/.."

run() {
  echo "==> $*"
  "$@"
}

run python3 scripts/build_rules.py --check
run python3 scripts/build_router_config.py --check
if [ "$offline" -eq 1 ]; then
  echo "==> SKIP python3 scripts/build_surge_modules.py --check (network required)"
  echo "==> SKIP python3 scripts/build_loon_plugins.py --check (network required)"
else
  run python3 scripts/build_surge_modules.py --check
  run python3 scripts/build_loon_plugins.py --check
fi
run python3 scripts/check_hygiene.py
run python3 scripts/check_upstreams.py
run python3 scripts/check_acceptance.py
for t in tests/test_*.py; do
  if [ "$t" = tests/test_shellcrash_override.py ]; then
    continue
  fi
  if [ "$offline" -eq 1 ] && [ "$t" = tests/test_resource_parser.py ] && [ -z "${PROXY_CONFIG_QX_PARSER-}" ]; then
    echo "==> SKIP python3 tests/test_resource_parser.py (network required unless PROXY_CONFIG_QX_PARSER is set)"
    continue
  fi
  run python3 "$t"
done
run python3 scripts/check_reject_conflicts.py --self-test
run sh -n mihomo/shellcrash/deploy.sh
if [ "$offline" -eq 1 ]; then
  echo "all offline checks passed (skipped network checks; run sh scripts/check_all.sh before commit)"
else
  echo "all checks passed"
fi
