#!/bin/sh
# 提交前检查与 CI 共用的一份清单：任一步失败即以非零状态退出。
# tests/test_shellcrash_override.py 需要固定版本的核心与 mmdb，只在 CI 单独运行。
set -eu
cd "$(dirname "$0")/.."

run() {
  echo "==> $*"
  "$@"
}

run python3 scripts/build_rules.py --check
run python3 scripts/build_router_config.py --check
run python3 scripts/build_surge_modules.py --check
run python3 scripts/build_loon_plugins.py --check
run python3 scripts/check_hygiene.py
run python3 scripts/check_upstreams.py
run python3 scripts/check_acceptance.py
for t in tests/test_*.py; do
  [ "$t" = tests/test_shellcrash_override.py ] || run python3 "$t"
done
run python3 scripts/check_reject_conflicts.py --self-test
run sh -n mihomo/shellcrash/deploy.sh
echo "all checks passed"
