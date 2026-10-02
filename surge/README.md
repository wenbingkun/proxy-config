# Surge（iPhone，试点）

> 返回 [总览](../README.md)。下文仓库命令均在仓库根目录执行。

Surge 是试用中的第四个客户端，与 Loon、QX 不要同时开启 VPN。结构是**托管配置 + 本地分离配置**：

- 仓库托管 `surge/proxy-config.conf`，内含 `[General]`、`[Proxy Group]`、`[Rule]`、`[Host]`，设备会自动更新它；
- 设备上的 `bootstrap.conf` 用 `#!include` 引用这几段，节点、MitM、家庭 SSID、机场 DNS 留在本地。

因此仓库里改了策略组或规则，不用再按 QX / Loon 的同步边界手工同步。托管更新只在主 App 运行时触发，`interval` 是最短间隔，验收时用手动更新。

需要 Surge iOS 5.17.0 及以上（AnyTLS 节点）。

## 第一次配置

文件都放在 iCloud Drive/Surge：

| 文件 | 来源 | 进仓库 |
|---|---|---|
| `proxy-config.conf` | 在 Surge 中从 URL 安装：`https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/proxy-config.conf` | 是（托管） |
| `Airport.conf` | 机场提供的 Surge 完整配置，下载后改名为 `Airport.conf`；只用它的 `[Proxy]` | 否 |
| `bootstrap.conf` | 复制 `surge/bootstrap.example.conf` 后填写 | 否 |
| `airport-dns.sgmodule` | 复制 `surge/airport-dns.example.sgmodule`，把占位地址换成 `Airport.conf` 中 `[General]` 的 `encrypted-dns-server` 原值。放进这个目录后作为本地模块启用 | 否 |

1. 在 `bootstrap.conf` 中把 `HOME_SSID` 换成家里 Wi-Fi 的名称。2.4G 与 5G 名称不同时，`[SSID Setting]` 每个名称各写一行。`[SSID Setting]` 用空格分隔网络和参数，所以名称里的空格要写成 `?`（单字符通配符），两个家庭模块的 `HOME_SSID` 参数也用同样的写法；参数之间用逗号，不加空格。
2. 在 Surge 中生成并安装、信任 CA，把 `ca-p12`、`ca-passphrase` 填进本地 `bootstrap.conf`，打开 MitM。
3. 选用 `bootstrap.conf` 作为当前配置。**不要直接选用** `proxy-config.conf`，它没有节点。
4. 机场的完整配置里如果没有 `#!MANAGED-CONFIG` 行，节点不会自动更新；节点有变化时，重新下载并覆盖 `Airport.conf`。
5. 启用本地模块「机场 DNS（仅本地）」，见下节。

## 机场 DNS

机场要求用它的专用 DNS 解析节点，才能稳定连接；其他各端都已这样设置（路由器 Mihomo 的 `proxy-server-nameserver`、Loon 订阅行的 `server-dns`，地址只写在设备上）。

Surge 没有按节点指定解析器的参数，代理服务器的主机名也不会匹配 `[Host]`（官方 Local DNS Mapping 页面），所以只能改全局加密 DNS：本地模块 `airport-dns.sgmodule` 覆盖 `[General]` 的 `encrypted-dns-server`。效果是**外出时 Surge 本地发起的解析（包括节点主机名）都以机场 DNS 为上游**，这与机场自带配置的做法相同，与 Loon 只对节点生效不同。例外：`[Host]` 中 5 条 `server:system`、DoH 自身域名的引导解析，以及走代理时由代理端完成的远程解析。在家时，`bootstrap.conf` 的 `[SSID Setting]` 仍把 DNS 交给路由器。

机场 DNS 的地址、节点域名都不进仓库。启用后验收：清除 DNS 缓存，分别在蜂窝网络和家里 Wi-Fi 下，查看节点主机名的解析记录（上游、结果）和节点握手是否成功；普通网站解析成功不算通过。

## 模块

模块的启用状态按设备保存，不随 iCloud 同步。在「模块 → 安装新模块」中填写以下 URL：

| 模块 | URL | 默认 |
|---|---|---|
| 在家直连 + 拦截 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/home-direct.sgmodule` | 启用，参数 `HOME_SSID` 填家里 Wi-Fi |
| 在家直连（关闭 AdRules / Privacy 拦截） | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/home-direct-noblock.sgmodule` | 不启用；与上一个**二选一** |
| 哔哩哔哩增强（kokoryh） | `https://raw.githubusercontent.com/kokoryh/Sparkle/master/release/surge/module/bilibili.sgmodule` | 启用，参数 `空降助手=#`（关闭），`屏蔽P2P=DEST-PORT` |
| blackmatrix7 去广告 | `https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rewrite/Surge/Advertising/Advertising.sgmodule` | 启用 |

**模块顺序**：「在家直连 + 拦截」要排在所有含 REJECT 规则的模块（目前是 B 站模块）**之后**。UDP 没有预匹配阶段，只按主规则顺序匹配；如果家庭模块排在前面，家里发往 B 站 P2P 端口的 UDP 会先命中 SUBNET 而直连。在 B 站模块页面确认 `屏蔽P2P=DEST-PORT` 后，在家播放视频，到请求记录里筛选目标端口 4480 / 4483 / 8082 / 9102，应全部为 REJECT；出现 DIRECT 就调整模块顺序。

## 家庭 / 外出

不用包装组，也不用 `suspend`，全程规则模式：

- 「在家直连 + 拦截」先执行 AdRules 和 Privacy 两份拒绝列表（`pre-matching`，在 DNS 与 TCP 握手阶段就拒绝），然后用 `SUBNET,SSID:<家里 Wi-Fi>,DIRECT` 把家里其余流量交给路由器；
- 外出时继续往下匹配托管配置的规则，走对应服务组；
- 在家时 DNS 由 `bootstrap.conf` 的 `[SSID Setting]` 交给系统（路由器）。

拒绝列表排在仓库规则**之前**，这一点与 QX / Loon 不同：Loon 里仓库列表命中的子域会放行，Surge 里会被拦截。例外：`rules/hk_banks.yaml` 与 `rules/intl_brokers.yaml` 覆盖的域名不拦截，排除清单 `surge/rules/reject_allow.list` 由 `build_rules.py` 从这两个文件生成。其余重叠（大多是统计、追踪子域）以 Surge 的拦截为准，当前清单可用 `python3 scripts/check_remote_resources.py --mode full` 查看（只告警）。

**误拦截时**：在请求记录里加普通 DIRECT 规则**无效**（预匹配优先于普通规则）。改为停用「在家直连 + 拦截」、启用「在家直连（关闭 AdRules / Privacy 拦截）」，两者的 `HOME_SSID` 要填同一个值。这只停掉这两份列表；B 站模块的拦截、bm7 改写照常工作。外出时，原本被拦截的请求会按服务规则走，可能经过代理，不像 Loon 的 `🛡️ 安全防护 = DIRECT` 那样直连。切换后确认只有一个家庭模块处于启用状态。

## 与 Loon 的有意差异

| 项 | Surge | 原因 |
|---|---|---|
| 「· 自动」包装组 | 没有 | 家庭模块的一条 SUBNET 规则替代 |
| `🛡️ 安全防护` | 没有（31 组） | 预匹配只接受 REJECT 本身；整体放行用 noblock 模块 |
| 地区组 | `smart`（美国组仍是 `select`） | Smart 组按握手延迟、丢包和站点表现选线；正则与 Loon 一致 |
| `GEOIP,CN` | 写在本地规则末尾 | Surge 按书写顺序匹配，不需要 Loon 的远程列表做法 |
| 按域名指定 DNS | 只保留 5 条 `server:system` | 其余映射在家会绕过路由器 DNS |
| QUIC | `block-quic = per-policy` | Surge 自动拒绝 MitM 主机名的 QUIC；若出现 QX 那次的症状（B 站去广告失效、动态页慢），改为 `all` |
| 空降助手 | 关闭 | 模块规则只能用 DIRECT / REJECT，不能像 Loon 那样走 `🐟 兜底分流` |

敏感服务的默认链路：💰 加密货币默认走 🇰🇷 韩国节点（Smart），国际券商走 🇭🇰 香港节点（Smart）。需要固定出口时，在 💰 中手动选择 🇺🇸 美国节点或具体节点。

## 合并前试验

合并前 `main` 上还没有 `surge/` 下的文件，按 URL 安装会失败。试验时用固定到分支提交的本地副本，**去掉托管行**，模块也用本地文件：

```sh
set -e
S=$(git rev-parse HEAD)            # 已推送的分支提交
T=.local/repo/2026-10-02-surge-design/trial
mkdir -p "$T" && chmod 700 "$T"
pin() { sed -e '/^#!MANAGED-CONFIG/d' \
            -e "s#raw.githubusercontent.com/wenbingkun/proxy-config/main/#raw.githubusercontent.com/wenbingkun/proxy-config/$S/#g" "$1" > "$2"; }
pin surge/proxy-config.conf "$T/proxy-config.conf"
pin surge/modules/home-direct.sgmodule "$T/home-direct.sgmodule"
pin surge/modules/home-direct-noblock.sgmodule "$T/home-direct-noblock.sgmodule"
if grep -n 'wenbingkun/proxy-config/main/' "$T"/*; then echo "unpinned repo URL" >&2; exit 1; fi
find "$T" -type f -exec chmod 600 {} +
```

把三个文件复制到 iCloud Drive/Surge；`.sgmodule` 放在配置目录里，就会作为本地模块出现。合并后改回按 URL 安装 `proxy-config.conf` 和两个模块，并删除本地副本。

## 回滚

关闭 Surge VPN，打开 Loon 或 QX，它们的配置不受影响。Surge 的本地文件都是新增的，不会覆盖其他客户端的文件。
