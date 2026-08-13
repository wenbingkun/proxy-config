#!/bin/sh

set -eu
umask 077

DEFAULT_DUAL_TEMPLATE_URL='https://raw.githubusercontent.com/wenbingkun/proxy-config/main/clash/config-router.template.yaml'
DEFAULT_SINGLE_TEMPLATE_URL='https://raw.githubusercontent.com/wenbingkun/proxy-config/main/clash/config-router-single.template.yaml'
PLACEHOLDER_1='https://example.com/__SUB_URL_1__'
PLACEHOLDER_2='https://example.com/__SUB_URL_2__'

deploy_tmp_dir=''
deploy_lock_dir=''
deploy_stage_path=''
deploy_backup_stage_path=''
shellcrash_tmp_root=''
provider_cache_backup_dir=''
provider_cache_transaction_started=0
invalidate_sub_cache=0
invalidate_sub2_cache=0
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
    if [ -n "$deploy_tmp_dir" ] && [ -d "$deploy_tmp_dir" ] && [ -n "$shellcrash_tmp_root" ]; then
        case "$deploy_tmp_dir" in
            "$shellcrash_tmp_root"/proxy-config.*) rm -rf "$deploy_tmp_dir" ;;
        esac
    fi
    if [ -n "$deploy_lock_dir" ] && [ -d "$deploy_lock_dir" ]; then
        rmdir "$deploy_lock_dir" 2>/dev/null || true
    fi
}

trap cleanup 0
trap 'exit 130' 1 2 15

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

    if command -v curl >/dev/null 2>&1; then
        curl -fsSL -o "$download_path" "$download_url"
    elif command -v wget >/dev/null 2>&1; then
        wget -O "$download_path" "$download_url"
    else
        fail '未找到 curl 或 wget'
    fi
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
    rm -f "$cache_path" || return 1
}

restore_provider_caches() {
    [ "$provider_cache_transaction_started" = '1' ] || return 0

    if [ "$invalidate_sub_cache" = '1' ]; then
        rm -f "$sub_cache_path"
        if [ -f "$provider_cache_backup_dir/sub.yaml" ]; then
            mkdir -p "${sub_cache_path%/*}"
            cp -p "$provider_cache_backup_dir/sub.yaml" "$sub_cache_path"
        fi
    fi
    if [ "$invalidate_sub2_cache" = '1' ]; then
        rm -f "$sub2_cache_path"
        if [ -f "$provider_cache_backup_dir/sub2.yaml" ]; then
            mkdir -p "${sub2_cache_path%/*}"
            cp -p "$provider_cache_backup_dir/sub2.yaml" "$sub2_cache_path"
        fi
    fi
    provider_cache_transaction_started=0
}

rollback_config() {
    rollback_reason=$1
    if [ "$had_previous_config" = '1' ] && [ -s "$backup_path" ]; then
        deploy_stage_path="$target_dir/.config.yaml.rollback.$$"
        cp -p "$backup_path" "$deploy_stage_path"
        mv -f "$deploy_stage_path" "$config_path"
        deploy_stage_path=''
        restore_provider_caches
        "$start_script" start >/dev/null 2>&1 || true
        fail "$rollback_reason；已恢复上一份配置"
    fi
    restore_provider_caches
    fail "$rollback_reason；没有可恢复的旧配置，已保留通过校验的新配置供排查"
}

if [ "$#" -ne 1 ]; then
    fail "用法：$0 /path/to/providers.env"
fi

env_file=$1
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
    http://*|https://*) ;;
    *) fail 'TEMPLATE_URL 必须是 http:// 或 https:// URL' ;;
esac

startup_wait=${SHELLCRASH_STARTUP_WAIT:-10}
case "$startup_wait" in
    ''|*[!0-9]*) fail 'SHELLCRASH_STARTUP_WAIT 必须是非负整数' ;;
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
if ! mkdir "$deploy_lock_dir" 2>/dev/null; then
    fail '已有配置部署任务正在运行'
fi

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
if ! download_template "$template_url" "$downloaded_template"; then
    fail '模板下载失败，当前配置未改动'
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

if [ "$invalidate_sub_cache" = '1' ] || [ "$invalidate_sub2_cache" = '1' ]; then
    log '检测到订阅来源变化，正在失效对应 provider 缓存……'
    if [ "$had_previous_config" = '1' ]; then
        if ! "$start_script" stop; then
            "$start_script" start >/dev/null 2>&1 || true
            fail 'ShellCrash 停止命令失败，当前配置和 provider 缓存未改动'
        fi
    fi
    provider_cache_transaction_started=1
    if [ "$invalidate_sub_cache" = '1' ] && \
        ! stage_provider_cache_invalidation "$sub_cache_path" sub.yaml; then
        restore_provider_caches
        if [ "$had_previous_config" = '1' ]; then
            "$start_script" start >/dev/null 2>&1 || true
        fi
        fail '无法失效 Sub provider 缓存，当前配置未改动'
    fi
    if [ "$invalidate_sub2_cache" = '1' ] && \
        ! stage_provider_cache_invalidation "$sub2_cache_path" sub2.yaml; then
        restore_provider_caches
        if [ "$had_previous_config" = '1' ]; then
            "$start_script" start >/dev/null 2>&1 || true
        fi
        fail '无法失效 Sub2 provider 缓存，当前配置未改动'
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

provider_cache_transaction_started=0
log "ShellCrash 配置部署成功：$config_path"
