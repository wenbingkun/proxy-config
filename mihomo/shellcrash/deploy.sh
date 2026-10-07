#!/bin/sh

set -eu
umask 077

DEFAULT_DUAL_TEMPLATE_URL='https://raw.githubusercontent.com/wenbingkun/proxy-config/main/mihomo/shellcrash/config-router.template.yaml'
DEFAULT_SINGLE_TEMPLATE_URL='https://raw.githubusercontent.com/wenbingkun/proxy-config/main/mihomo/shellcrash/config-router-single.template.yaml'
PLACEHOLDER_1='https://example.com/__SUB_URL_1__'
PLACEHOLDER_2='https://example.com/__SUB_URL_2__'

deploy_tmp_dir=''
deploy_lock_dir=''
deploy_lock_owned=0
deploy_transaction_started=0
deploy_keep_recovery=0
deploy_lock_identity=''
deploy_stage_path=''
deploy_backup_stage_path=''
shellcrash_tmp_root=''
provider_cache_backup_dir=''
provider_cache_transaction_started=0
invalidate_sub_cache=0
invalidate_sub2_cache=0
verify_sub_provider=0
verify_sub2_provider=0
sub_cache_path=''
sub2_cache_path=''

log() {
    printf '%s\n' "$*"
}

fail() {
    printf '错误：%s\n' "$*" >&2
    exit 1
}

cleanup() {
    if [ -n "$deploy_stage_path" ] && [ -f "$deploy_stage_path" ]; then
        rm -f "$deploy_stage_path"
    fi
    if [ -n "$deploy_backup_stage_path" ] && [ -f "$deploy_backup_stage_path" ]; then
        rm -f "$deploy_backup_stage_path"
    fi
    if [ "$deploy_keep_recovery" = 0 ] && [ -n "$deploy_tmp_dir" ] && [ -d "$deploy_tmp_dir" ] && [ -n "$shellcrash_tmp_root" ]; then
        case "$deploy_tmp_dir" in
            "$shellcrash_tmp_root"/proxy-config.*) rm -rf "$deploy_tmp_dir" ;;
        esac
    fi
    if [ "$deploy_lock_owned" = 1 ] && [ "$(cat "$deploy_lock_dir/owner" 2>/dev/null)" = "$deploy_lock_identity" ]; then
        rm -f "$deploy_lock_dir/owner"
        rmdir "$deploy_lock_dir" 2>/dev/null || true
    fi
}

finish() {
    deploy_exit=$?
    trap - 0
    trap '' 1 2 15
    if [ "$deploy_transaction_started" = 1 ]; then
        set +e
        if ! recover_deployment; then
            deploy_exit=1
        fi
        [ "$deploy_exit" -ne 0 ] || deploy_exit=1
    fi
    set +e
    cleanup
    exit "$deploy_exit"
}

trap finish 0
trap 'exit 129' 1
trap 'exit 130' 2
trap 'exit 143' 15

process_identity() {
    # Linux /proc starttime distinguishes PID reuse; boot_id distinguishes reboots.
    [ -r "/proc/$1/stat" ] || return 1
    process_start=$(sed 's/.*) //' "/proc/$1/stat" | awk '{print $20}')
    [ -n "$process_start" ] || return 1
    printf '%s %s %s' "$1" "$deploy_boot_id" "$process_start"
}

acquire_deploy_lock() {
    deploy_boot_id=$(cat /proc/sys/kernel/random/boot_id) || fail '无法读取 boot_id，不能安全获取部署锁'
    deploy_lock_identity=$(process_identity "$$") || fail '无法读取部署进程身份'
    if ! mkdir "$deploy_lock_dir" 2>/dev/null; then
        old_owner=$(cat "$deploy_lock_dir/owner" 2>/dev/null) || fail '部署锁缺少所有者信息，请先核实旧任务；不会自动删除'
        read -r owner_pid owner_boot owner_start extra <<OWNER
$old_owner
OWNER
        case "$owner_pid:$owner_start" in
            *[!0-9:]*|:*|*:) fail '部署锁身份无效，请先核实旧任务' ;;
        esac
        [ -n "$owner_boot" ] && [ -z "$extra" ] || fail '部署锁身份无效'
        if [ "$owner_boot" = "$deploy_boot_id" ]; then
            current_owner=$(process_identity "$owner_pid") || current_owner=''
            [ "$current_owner" != "$old_owner" ] || fail '已有配置部署任务正在运行'
            # Permission failures must not be interpreted as proof of death.
            if [ -d "/proc/$owner_pid" ] && [ -z "$current_owner" ]; then
                fail '无法核实部署锁所有者，保留锁'
            fi
        fi
        # Only one reclaimer can rename this dead owner's directory. Contenders
        # cannot remove a newly acquired lock after it has been replaced.
        mkdir "$deploy_lock_dir/reclaim" 2>/dev/null || fail '旧部署锁正在回收或回收曾中断，请先核实'
        if [ "$(cat "$deploy_lock_dir/owner" 2>/dev/null)" != "$old_owner" ]; then
            rmdir "$deploy_lock_dir/reclaim" 2>/dev/null || true
            fail '部署锁身份已变化'
        fi
        stale_lock="$deploy_lock_dir.stale.$$"
        [ ! -e "$stale_lock" ] || fail '旧锁隔离路径已存在，请先核实'
        mv "$deploy_lock_dir" "$stale_lock" || fail '无法隔离旧部署锁'
        rm -f "$stale_lock/owner"
        rmdir "$stale_lock/reclaim" "$stale_lock" || fail '旧部署锁含未知文件，保留供核实'
        mkdir "$deploy_lock_dir" 2>/dev/null || fail '已有配置部署任务正在运行'
    fi
    deploy_lock_owned=1
    printf '%s\n' "$deploy_lock_identity" >"$deploy_lock_dir/owner"
}

validate_subscription_url() {
    subscription_name=$1
    subscription_value=$2

    case "$subscription_value" in
        http://*|https://*) ;;
        *) fail "$subscription_name 必须是 http:// 或 https:// URL" ;;
    esac
    if printf '%s' "$subscription_value" | LC_ALL=C grep -q '[[:space:]]'; then
        fail "$subscription_name 不能包含空白字符"
    fi
    case "$subscription_value" in
        *\\*|*\"*) fail "$subscription_name 不能包含反斜杠或双引号" ;;
    esac
}

escape_sed_replacement() {
    printf '%s' "$1" | sed 's/[\\&|]/\\&/g'
}

count_fixed_occurrences() {
    occurrence_value=$1
    occurrence_file=$2
    awk -v needle="$occurrence_value" '
        {
            remaining = $0
            while ((position = index(remaining, needle)) > 0) {
                count++
                remaining = substr(remaining, position + length(needle))
            }
        }
        END { print count + 0 }
    ' "$occurrence_file"
}

download_template() {
    download_url=$1
    download_path=$2

    # curl enforces a whole-transfer limit; wget -T only bounds idle reads.
    # stderr is discarded because a custom TEMPLATE_URL may carry credentials.
    curl -fsL --proto '=https' --proto-redir '=https' --connect-timeout 15 --max-time 120 -o "$download_path" "$download_url" 2>/dev/null
}

provider_url_from_config() {
    provider_name=$1
    provider_config=$2

    [ -f "$provider_config" ] || return 0
    awk -v provider="$provider_name" '
        $0 == "proxy-providers:" {
            in_providers = 1
            next
        }
        in_providers && /^[^[:space:]]/ { exit }
        in_providers && $0 == "  " provider ":" {
            in_provider = 1
            next
        }
        in_provider && /^  [^[:space:]][^:]*:/ { exit }
        in_provider && index($0, "    url: \"") == 1 {
            value = $0
            sub(/^    url: \"/, "", value)
            sub(/\"[[:space:]]*$/, "", value)
            print value
            exit
        }
    ' "$provider_config"
}

stage_provider_cache_invalidation() {
    cache_path=$1
    cache_name=$2

    [ -e "$cache_path" ] || [ -L "$cache_path" ] || return 0
    [ -f "$cache_path" ] || return 1
    mkdir -p "$provider_cache_backup_dir" || return 1
    cp -p "$cache_path" "$provider_cache_backup_dir/$cache_name" || return 1
}

restore_provider_caches() {
    [ "$provider_cache_transaction_started" = '1' ] || return 0

    if [ "$invalidate_sub_cache" = '1' ]; then
        rm -f "$sub_cache_path" || return 1
        if [ -f "$provider_cache_backup_dir/sub.yaml" ]; then
            mkdir -p "${sub_cache_path%/*}" || return 1
            cp -p "$provider_cache_backup_dir/sub.yaml" "$sub_cache_path" || return 1
        fi
    fi
    if [ "$invalidate_sub2_cache" = '1' ]; then
        rm -f "$sub2_cache_path" || return 1
        if [ -f "$provider_cache_backup_dir/sub2.yaml" ]; then
            mkdir -p "${sub2_cache_path%/*}" || return 1
            cp -p "$provider_cache_backup_dir/sub2.yaml" "$sub2_cache_path" || return 1
        fi
    fi
    provider_cache_transaction_started=0
}

provider_cache_has_nodes() {
    cache_path=$1

    [ -s "$cache_path" ] || return 1
    awk '
        /^[[:space:]]*proxies:[[:space:]]*/ {
            value = $0
            sub(/^[[:space:]]*proxies:[[:space:]]*/, "", value)
            if (value != "") {
                if (value ~ /^\[/ && value !~ /^\[[[:space:]]*\]$/) {
                    found = 1
                }
                exit
            }
            in_proxies = 1
            next
        }
        in_proxies && /^[^[:space:]]/ { exit }
        in_proxies && /^[[:space:]]*-[[:space:]]+/ {
            found = 1
            exit
        }
        END { exit found ? 0 : 1 }
    ' "$cache_path"
}

changed_provider_caches_ready() {
    if [ "$verify_sub_provider" = '1' ] && ! provider_cache_has_nodes "$sub_cache_path"; then
        return 1
    fi
    if [ "$verify_sub2_provider" = '1' ] && ! provider_cache_has_nodes "$sub2_cache_path"; then
        return 1
    fi
    return 0
}

wait_for_changed_provider_caches() {
    provider_elapsed=0
    while ! changed_provider_caches_ready; do
        [ "$provider_elapsed" -lt "$provider_wait" ] || return 1
        sleep 1
        provider_elapsed=$((provider_elapsed + 1))
    done
}

failed_provider_names() {
    failed_names=''
    if [ "$verify_sub_provider" = '1' ] && ! provider_cache_has_nodes "$sub_cache_path"; then
        failed_names='Sub'
    fi
    if [ "$verify_sub2_provider" = '1' ] && ! provider_cache_has_nodes "$sub2_cache_path"; then
        if [ -n "$failed_names" ]; then
            failed_names="$failed_names、Sub2"
        else
            failed_names='Sub2'
        fi
    fi
    printf '%s' "$failed_names"
}

recover_deployment() {
    deploy_transaction_started=0
    if ! "$start_script" stop >/dev/null 2>&1; then
        deploy_keep_recovery=1
        log "回滚停止核心失败，未恢复配置或缓存；保留恢复材料：$deploy_tmp_dir；服务状态未确认" >&2
        return 1
    fi
    if [ "$had_previous_config" = '1' ]; then
        deploy_stage_path="$target_dir/.config.yaml.rollback.$$"
        if ! cp -p "$backup_path" "$deploy_stage_path" || ! mv -f "$deploy_stage_path" "$config_path" || ! restore_provider_caches; then
            deploy_keep_recovery=1
            log "回滚文件恢复失败；保留恢复材料：$deploy_tmp_dir；服务可能未运行" >&2
            return 1
        fi
        deploy_stage_path=''
        if "$start_script" start >/dev/null 2>&1; then
            log '已恢复上一份配置和 provider 缓存，旧配置的启动命令已成功执行' >&2
            return 0
        fi
        deploy_keep_recovery=1
        log "旧配置启动失败，服务可能未运行；保留恢复材料：$deploy_tmp_dir" >&2
        return 1
    fi
    if ! restore_provider_caches; then
        deploy_keep_recovery=1
        log "首次部署的缓存恢复失败；保留恢复材料：$deploy_tmp_dir" >&2
        return 1
    fi
    log '没有可恢复的旧配置；核心已停止，保留新配置供排查，需要人工完成首次启动' >&2
    return 1
}

rollback_config() {
    fail "$1"
}

if [ "$#" -ne 1 ]; then
    fail "用法：$0 /path/to/providers.env"
fi

env_file=$1
case "$env_file" in
    */*) ;;
    *) env_file="./$env_file" ;;
esac
[ -r "$env_file" ] || fail "无法读取私密参数文件：$env_file"

# providers.env is a trusted, root-owned shell fragment. Keep it mode 600.
# shellcheck disable=SC1090
. "$env_file"

: "${SHELLCRASH_DIR:?providers.env 中必须设置 SHELLCRASH_DIR}"
: "${SUB_URL_1:?providers.env 中必须设置 SUB_URL_1}"
SUB_URL_2=${SUB_URL_2:-}

shellcrash_dir=${SHELLCRASH_DIR%/}
case "$shellcrash_dir" in
    /*) ;;
    *) fail 'SHELLCRASH_DIR 必须是绝对路径' ;;
esac
[ "$shellcrash_dir" != '/' ] || fail 'SHELLCRASH_DIR 不能是根目录'
[ -d "$shellcrash_dir" ] || fail "ShellCrash 目录不存在：$shellcrash_dir"

validate_subscription_url SUB_URL_1 "$SUB_URL_1"
if [ -n "$SUB_URL_2" ]; then
    validate_subscription_url SUB_URL_2 "$SUB_URL_2"
    subscription_count=2
    default_template_url=$DEFAULT_DUAL_TEMPLATE_URL
else
    subscription_count=1
    default_template_url=$DEFAULT_SINGLE_TEMPLATE_URL
fi

template_url=${TEMPLATE_URL:-$default_template_url}
case "$template_url" in
    https://*) ;;
    *) fail 'TEMPLATE_URL 必须是 https:// URL' ;;
esac
command -v curl >/dev/null 2>&1 || fail '未找到 curl；模板下载需要 curl 的整次超时，当前配置未改动'

startup_wait=${SHELLCRASH_STARTUP_WAIT:-10}
case "$startup_wait" in
    ''|*[!0-9]*) fail 'SHELLCRASH_STARTUP_WAIT 必须是非负整数' ;;
esac

provider_wait=${SHELLCRASH_PROVIDER_WAIT:-60}
case "$provider_wait" in
    ''|*[!0-9]*) fail 'SHELLCRASH_PROVIDER_WAIT 必须是非负整数' ;;
esac

config_path=${SHELLCRASH_CONFIG_PATH:-$shellcrash_dir/yamls/config.yaml}
case "$config_path" in
    "$shellcrash_dir"/yamls/*) ;;
    *) fail 'SHELLCRASH_CONFIG_PATH 必须位于 SHELLCRASH_DIR/yamls/ 下' ;;
esac
case "$config_path" in
    */../*|*/./*|*/..|*/.) fail 'SHELLCRASH_CONFIG_PATH 不能包含 . 或 .. 路径段' ;;
esac

target_dir=${config_path%/*}
[ -d "$target_dir" ] || fail "配置目录不存在：$target_dir"

backup_path="$config_path.bak.proxy-config"
had_previous_config=0
[ -f "$config_path" ] && had_previous_config=1

shellcrash_cfg="$shellcrash_dir/configs/ShellCrash.cfg"
command_env="$shellcrash_dir/configs/command.env"
start_script="$shellcrash_dir/start.sh"
[ -r "$shellcrash_cfg" ] || fail "找不到 ShellCrash 配置：$shellcrash_cfg"
[ -r "$command_env" ] || fail "找不到 ShellCrash 命令环境：$command_env"
[ -x "$start_script" ] || fail "ShellCrash 启动脚本不可执行：$start_script"

if grep -Eq '^disoverride=1[[:space:]]*$' "$shellcrash_cfg"; then
    fail '当前已禁用 ShellCrash 配置覆写；策略模板不能在该模式下直接运行'
fi

deploy_lock_dir="$shellcrash_dir/configs/.proxy-config-deploy.lock"
acquire_deploy_lock

shellcrash_tmp_root=${SHELLCRASH_TMP_ROOT:-/tmp}
shellcrash_tmp_root=${shellcrash_tmp_root%/}
case "$shellcrash_tmp_root" in
    /*) ;;
    *) fail 'SHELLCRASH_TMP_ROOT 必须是绝对路径' ;;
esac
[ -n "$shellcrash_tmp_root" ] && [ "$shellcrash_tmp_root" != '/' ] || fail 'SHELLCRASH_TMP_ROOT 不能是根目录'
[ -d "$shellcrash_tmp_root" ] || fail "临时目录不存在：$shellcrash_tmp_root"
deploy_tmp_dir=$(mktemp -d "$shellcrash_tmp_root/proxy-config.XXXXXX") || fail '无法创建临时目录'

downloaded_template="$deploy_tmp_dir/config-router.template.yaml"
rendered_config="$deploy_tmp_dir/config-router.yaml"
replacement_script="$deploy_tmp_dir/replace.sed"

log '正在下载公开路由器策略模板……'
download_status=0
download_template "$template_url" "$downloaded_template" || download_status=$?
if [ "$download_status" -ne 0 ]; then
    fail "模板下载失败（curl 退出码 $download_status，28 为超时），当前配置未改动；若路由器无法访问模板地址，可临时设置 TEMPLATE_URL，见 mihomo/shellcrash/README.md"
fi
[ -s "$downloaded_template" ] || fail '下载到的模板为空，当前配置未改动'

placeholder_1_count=$(count_fixed_occurrences "$PLACEHOLDER_1" "$downloaded_template")
placeholder_2_count=$(count_fixed_occurrences "$PLACEHOLDER_2" "$downloaded_template")
[ "$placeholder_1_count" = '1' ] || fail '模板中的 SUB_URL_1 占位符数量不是 1'
if [ "$subscription_count" = '2' ]; then
    [ "$placeholder_2_count" = '1' ] || fail '双订阅模板中的 SUB_URL_2 占位符数量不是 1'
else
    [ "$placeholder_2_count" = '0' ] || fail '单订阅模板不应包含 SUB_URL_2 占位符'
fi

escaped_sub_url_1=$(escape_sed_replacement "$SUB_URL_1")
{
    printf 's|%s|%s|g\n' "$PLACEHOLDER_1" "$escaped_sub_url_1"
    if [ "$subscription_count" = '2' ]; then
        escaped_sub_url_2=$(escape_sed_replacement "$SUB_URL_2")
        printf 's|%s|%s|g\n' "$PLACEHOLDER_2" "$escaped_sub_url_2"
    fi
} >"$replacement_script"

sed -f "$replacement_script" "$downloaded_template" >"$rendered_config"
[ -s "$rendered_config" ] || fail '私密参数注入后配置为空'
if grep -Eq '__SUB_URL_[0-9]+__' "$rendered_config"; then
    fail '私密参数注入后仍有订阅占位符残留'
fi

old_sub_url=$(provider_url_from_config Sub "$config_path")
new_sub_url=$(provider_url_from_config Sub "$rendered_config")
old_sub2_url=$(provider_url_from_config Sub2 "$config_path")
new_sub2_url=$(provider_url_from_config Sub2 "$rendered_config")
[ "$old_sub_url" = "$new_sub_url" ] || invalidate_sub_cache=1
[ "$old_sub2_url" = "$new_sub2_url" ] || invalidate_sub2_cache=1
if [ "$invalidate_sub_cache" = '1' ] && [ -n "$new_sub_url" ]; then
    verify_sub_provider=1
fi
if [ "$invalidate_sub2_cache" = '1' ] && [ -n "$new_sub2_url" ]; then
    verify_sub2_provider=1
fi

# Load ShellCrash's runtime and data directories without executing COMMAND.
# shellcheck disable=SC1090
. "$command_env"
shellcrash_runtime_dir=${TMPDIR:-/tmp/ShellCrash}
shellcrash_bind_dir=${BINDIR:-$shellcrash_dir}
provider_cache_dir="$shellcrash_bind_dir/cache/proxy-providers"
provider_cache_backup_dir="$deploy_tmp_dir/provider-cache-backup"
sub_cache_path="$provider_cache_dir/sub.yaml"
sub2_cache_path="$provider_cache_dir/sub2.yaml"
mihomo_bin=''
bootstrap_without_core=0

if [ -n "${MIHOMO_BIN:-}" ]; then
    mihomo_bin=$MIHOMO_BIN
    [ -x "$mihomo_bin" ] || fail "Mihomo/CrashCore 不可执行：$mihomo_bin"
elif [ -x "$shellcrash_runtime_dir/CrashCore" ]; then
    mihomo_bin="$shellcrash_runtime_dir/CrashCore"
elif [ -x "$shellcrash_bind_dir/CrashCore" ]; then
    mihomo_bin="$shellcrash_bind_dir/CrashCore"
elif [ -x "$shellcrash_bind_dir/CrashCore.raw" ]; then
    mihomo_bin="$shellcrash_bind_dir/CrashCore.raw"
elif [ -x "$shellcrash_bind_dir/CrashCore.upx" ]; then
    mihomo_bin="$shellcrash_bind_dir/CrashCore.upx"
elif command -v mihomo >/dev/null 2>&1; then
    mihomo_bin=$(command -v mihomo)
elif [ "$had_previous_config" = '0' ]; then
    bootstrap_without_core=1
    if [ -z "${SHELLCRASH_STARTUP_WAIT:-}" ]; then
        startup_wait=120
    fi
    log '全新安装未发现核心：使用已验证模板引导，核心将由 ShellCrash 首次启动时下载。'
else
    fail '已有配置但找不到可执行的 Mihomo/CrashCore；当前配置未改动'
fi

if [ "$bootstrap_without_core" = '0' ]; then
    log '正在使用设备上的 Mihomo 校验临时配置……'
    if ! "$mihomo_bin" -t -d "$shellcrash_bind_dir" -f "$rendered_config"; then
        fail 'Mihomo 配置校验失败，当前配置未改动'
    fi
fi

if [ -f "$config_path" ]; then
    deploy_backup_stage_path="$target_dir/.config.yaml.backup.$$"
    cp -p "$config_path" "$deploy_backup_stage_path"
    mv -f "$deploy_backup_stage_path" "$backup_path"
    deploy_backup_stage_path=''
fi

deploy_stage_path="$target_dir/.config.yaml.new.$$"
if [ "$had_previous_config" = '1' ]; then
    cp -p "$config_path" "$deploy_stage_path"
    cat "$rendered_config" >"$deploy_stage_path"
else
    cp "$rendered_config" "$deploy_stage_path"
    chmod 600 "$deploy_stage_path"
fi

# From the first service mutation onward, every non-successful exit restores
# the old state. Cache snapshots are completed before any cache is removed.
deploy_transaction_started=1
if [ "$invalidate_sub_cache" = '1' ] || [ "$invalidate_sub2_cache" = '1' ]; then
    log '检测到订阅来源变化，正在失效对应 provider 缓存……'
    if [ "$had_previous_config" = '1' ]; then
        "$start_script" stop || fail 'ShellCrash 停止命令失败'
    fi
    if [ "$invalidate_sub_cache" = '1' ]; then
        stage_provider_cache_invalidation "$sub_cache_path" sub.yaml || fail '无法备份 Sub provider 缓存'
    fi
    if [ "$invalidate_sub2_cache" = '1' ]; then
        stage_provider_cache_invalidation "$sub2_cache_path" sub2.yaml || fail '无法备份 Sub2 provider 缓存'
    fi
    provider_cache_transaction_started=1
    if [ "$invalidate_sub_cache" = '1' ]; then
        rm -f "$sub_cache_path"
    fi
    if [ "$invalidate_sub2_cache" = '1' ]; then
        rm -f "$sub2_cache_path"
    fi
fi

mv -f "$deploy_stage_path" "$config_path"
deploy_stage_path=''

log '配置已原子替换，正在通过 ShellCrash 启动服务……'
if ! "$start_script" start; then
    rollback_config 'ShellCrash 启动命令失败'
fi

[ "$startup_wait" -eq 0 ] || sleep "$startup_wait"

if [ "${SHELLCRASH_SKIP_PROCESS_CHECK:-0}" != '1' ] && command -v pidof >/dev/null 2>&1; then
    if ! pidof CrashCore >/dev/null 2>&1; then
        rollback_config 'ShellCrash 启动后未检测到 CrashCore 进程'
    fi
fi

if [ "$verify_sub_provider" = '1' ] || [ "$verify_sub2_provider" = '1' ]; then
    log '正在确认变更后的订阅已生成有效 provider 节点缓存……'
    if ! wait_for_changed_provider_caches; then
        provider_failures=$(failed_provider_names)
        rollback_config "$provider_failures 订阅获取失败（未生成包含有效节点的 provider 缓存）；请检查机场后台是否已开启订阅导入或客户端导入开关、订阅链接是否有效，以及路由器能否访问机场订阅地址"
    fi
fi

deploy_transaction_started=0
provider_cache_transaction_started=0
log "ShellCrash 配置部署成功：$config_path"
