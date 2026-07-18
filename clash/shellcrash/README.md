# ShellCrash 路由器接入

本目录采用以下配置所有权：

- 仓库负责 `proxy-providers`、策略组、rule-provider 和路由规则。
- ShellCrash 继续负责端口、DNS、TUN、sniffer、控制器、管理密钥和防火墙。
- 真实订阅 URL 只保存在路由器本地，不进入 Git。

因此必须保持 ShellCrash 的默认“配置覆写”开启。两个 `config-router*.template.yaml`
都是策略模板，不能在禁用覆写模式下作为完整 Mihomo 配置直接运行。

## 1. 检查设备状态

在路由器执行以下只读命令：

```sh
SC="${CRASHDIR:-/data/other_vol/ShellCrash}"
printf 'CRASHDIR=%s\n' "$SC"
grep -E '^(versionsh_l|crashcore|disoverride|dns_mod|redir_mod)=' "$SC/configs/ShellCrash.cfg"
grep -E '^(TMPDIR|BINDIR)=' "$SC/configs/command.env"
```

确认：

- `crashcore=meta`，即使用 Mihomo 内核；
- 没有 `disoverride=1`；
- `CRASHDIR`、`TMPDIR` 和 `BINDIR` 指向真实安装目录；
- 已使用过的设备应能正常启动；全新安装暂时没有 `CrashCore` 和
  `yamls/config.yaml` 是正常状态。

不要把包含 `Url`、`Https`、订阅地址或管理密钥的配置输出到公开日志。

## 2. 生成公开模板

在仓库工作区执行：

```sh
python3 scripts/build_router_config.py
python3 scripts/build_router_config.py --check
```

生成两个公开模板：

- `clash/config-router-single.template.yaml`：只有 `Sub`；
- `clash/config-router.template.yaml`：包含 `Sub` 和 `Sub2`。

两个模板都：

- 来自现有 `clash/config.yaml`，不是第二份手工维护的配置；
- 展开了 YAML 锚点和合并键，避免 ShellCrash 拆分区块后引用失效；
- 只包含 ShellCrash 默认覆写流程会保留的策略区块；
- 只使用对应数量的公开占位 URL，通过 Mihomo 校验后才允许提交。

## 3. 全新安装

全新安装不需要手工安装核心，也不需要先在 ShellCrash 导入其他配置。
当设备同时没有旧配置和核心时，部署脚本会：

1. 下载并注入经过 CI 的 Mihomo 与 ShellCrash 官方覆写测试的公开模板；
2. 原子写入 `$CRASHDIR/yamls/config.yaml`；
3. 调用 ShellCrash 启动，由 ShellCrash 自行下载匹配设备架构的核心；
4. 默认等待最多 120 秒后确认 `CrashCore` 进程。

只有“没有旧配置”时才允许无设备核心引导。后续更新如果找不到核心会直接
失败并保留当前配置，避免未经设备内核校验就覆盖可用配置。

## 4. 创建路由器私密参数

把 `providers.env.example` 复制到路由器的持久化私密目录，例如：

```sh
mkdir -p "$CRASHDIR/private"
cp providers.env.example "$CRASHDIR/private/providers.env"
chmod 600 "$CRASHDIR/private/providers.env"
```

编辑 `providers.env`，设置实际的 `SHELLCRASH_DIR` 和 `SUB_URL_1`。
`SUB_URL_2` 可选：留空时脚本自动下载单订阅模板；填写后自动使用双订阅模板。
不要把该文件放回仓库或发送到日志。

后续更新会依次检查 ShellCrash 运行时的 `CrashCore`，以及持久目录中的
`CrashCore`、`CrashCore.raw` 和 `CrashCore.upx`。如果设备只保留了压缩的
`CrashCore.gz` 或 `CrashCore.tar.gz`，请先正常启动一次 ShellCrash，或通过
`MIHOMO_BIN` 指定其他可执行的 Mihomo 路径。

## 5. 首次部署

把 `scripts/deploy_shellcrash_config.sh` 复制到路由器持久化目录，然后执行：

```sh
chmod 700 /path/to/deploy_shellcrash_config.sh
/path/to/deploy_shellcrash_config.sh "$CRASHDIR/private/providers.env"
```

脚本会依次完成：

1. 加锁并下载公开模板到临时目录；
2. 检查模板与单/双订阅模式匹配，且每个所需占位符只出现一次；
3. 注入订阅 URL，但不在日志中输出它们；
4. 使用设备上的 Mihomo/CrashCore 执行 `-t`；
5. 备份并原子替换 `$CRASHDIR/yamls/config.yaml`；
6. 通过 ShellCrash 启动服务；
7. 启动失败时恢复上一份配置。

最近一次备份保存在：

```text
$CRASHDIR/yamls/config.yaml.bak.proxy-config
```

下载、占位符检查或 Mihomo 校验失败时，当前运行配置不会被修改。

## 6. 配置定时更新

在 ShellCrash 的任务管理中添加自定义命令，命令内容与首次部署相同，再按需要设置每日或每周运行。

不要同时启用 ShellCrash 内置的“更新在线订阅并重启服务”任务。该任务会直接重新下载公共模板，从而绕过本地私密注入。配置更新必须只有一个写入者。

建议先手工运行数次并完成一次路由器重启测试，再开启定时任务。

## 7. 米家与 DNS

默认覆写模式下，仓库模板里的 DNS、fake-IP、TUN 和 sniffer 参数不会成为最终运行值。相关兼容调整应放在 ShellCrash 本地配置，例如：

```text
$CRASHDIR/configs/fake_ip_filter.list
$CRASHDIR/yamls/user.yaml
```

当前核对的 ShellCrash 官方 `fake_ip_filter.list` 已包含 `Mijia Cloud` 和
`+.market.xiaomi.com`，sniffer 也默认跳过 `Mijia Cloud`。先使用这些官方
兼容项；只有日志证明仍有问题时，才向本地过滤列表或共享规则添加更具体的域名。

先通过日志区分 DNS/fake-IP、sniffer 和路由策略问题，再决定是否向共享 `rules/` 增加米家直连规则。不要一次性加入未经验证的广泛域名清单。

## 8. 更新与回滚验证

每次调整生成或部署逻辑后至少验证：

```sh
python3 scripts/test_deploy_shellcrash.py
```

- 连续执行部署两次结果一致；
- 无效模板和无效订阅不会覆盖现有配置；
- 重启路由器后 ShellCrash 能使用最后一次成功配置启动；
- Windows、Quantumult X 和路由器仍从相同的 `rules/` 源获得自维护规则；
- 日志、Git diff 和 Git 历史中没有真实订阅 URL。
