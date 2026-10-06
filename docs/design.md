# 设计说明

> 返回 [总览](../README.md)。

## 核心问题

最初的方案是将整份配置放在 GitHub，让 Quantumult X 直接下载并覆盖本地配置：

```
GitHub config.conf → QX 下载 → 覆盖本地配置
```

这带来了一个无法回避的问题：**每次远程配置更新，MitM 证书、passphrase 等本地私密信息都会被清空**，需要重新手动填写。

根本原因在于将两类性质完全不同的数据混在了同一份文件里：

| 类型 | 应该放哪里 |
|---|---|
| 路由规则、重写规则、代理分组 | GitHub（可共享，可版本管理） |
| MitM 证书、passphrase、订阅链接 | 设备本地（私密，不可共享） |

## 解决方案

**将"配置代码"和"运行状态"彻底分离。**

GitHub 只存可以公开的配置逻辑，设备本地只保存私密的运行状态，通过"远程模块"机制连接两者：

```
                       rules/*.yaml（共享规则源）
                              │
                    scripts/build_rules.py
          ┌───────────┬───────┴───────┬───────────────────┐
          ▼           ▼               ▼                   ▼
 quantumultx/filter/ loon/rules/   surge/rules/       mihomo/rules/
   repo.snippet       *.list          *.list             *.yaml
          │           │               │                   │
          ▼           ▼               ▼                   ▼
    Quantumult X     Loon            Surge       Clash Verge / ShellCrash
  （订阅、MitM 留在设备；Surge 公开配置通过本地 bootstrap include 加载）
```

## 四大设计原则

**原则一：配置代码 ≠ 本地运行状态**

GitHub 管配置逻辑，设备管运行状态。MitM 证书和订阅链接永远不进入版本控制。

**原则二：bootstrap + 远程模块（QX / Loon）**

Quantumult X 与 Loon 各自在本地持有一份 bootstrap.conf，包含本地私密信息、策略组和远程资源入口。仓库自维护的共享规则通过生成的远程 snippet 加载，第三方规则、重写和脚本通过 bootstrap 中的远程引用加载；GitHub 规则更新不会覆盖本地证书。

**原则三：rule-providers（Mihomo）**

Clash 主配置只定义代理分组和规则引用结构，具体规则内容通过 rule-providers 从 GitHub Raw 动态拉取。修改规则无需改动主配置，push 后自动生效。

**原则四：规则单源维护**

`rules/` 目录是仓库自维护共享规则的唯一编辑入口。`build_rules.py` 负责将其转换为各客户端所需的格式，确保这部分规则在 QX、Loon、Windows 和路由器之间一致；第三方规则仍由各客户端配置显式引用，并由远程资源巡检持续检查。离线依赖检查 `python3 scripts/check_upstreams.py` 按字段检查规则、PNG 图标和执行引用；`rules/upstreams.yaml` 只登记有限来源模式与精确可变例外，版本仍以配置和生成器为准。`--json` 报告全部引用位置、固定/受控/例外状态及未展开的传递依赖；固定外层容器不代表内部脚本也固定。

`remote-resources.yml` 周巡检运行 full，手动仍可选择 light/full（默认 light），时限 15 分钟。full 根据 Mihomo provider 的 behavior/format 分类实际目的 IP 条目，比较 GEOIP,CN 前不带 no-resolve 的 RULE-SET 兜底引用；缺口报错，当前无 IP 的旧兜底只提示复核，不自动删除。外部 provider 复用本次完整下载，仓库自维护的 @main 规则按当前 checkout 比较，远程可用性仍单独检查。下载、编码、解析或不支持的语法显示“not compared”并失败，不能当作无漂移。stdout 与 job summary 记录来源、SHA-256 和比较结果；不保存发布快照，也不验证实际 DNS/路由行为。

## 更新流程

```
编辑 rules/*.yaml
       │
       ▼
python3 scripts/build_rules.py
       │
       ├── 生成 mihomo/rules/*.yaml
       ├── 生成 quantumultx/filter/repo.snippet
       ├── 生成 loon/rules/*.list
       └── 生成 surge/rules/*.list
       │
       ▼
git push
       │
       ├── Mihomo 在下次刷新时拉取 mihomo/rules ──→ 规则生效
       ├── QX 在下次刷新时拉取 filter/repo.snippet ──→ 规则生效
       ├── Loon 在下次刷新时拉取 loon/rules ──→ 规则生效
       └── Surge 在下次刷新时拉取 surge/rules ──→ 规则生效
```

## Mihomo 生成链

路由器采用 **公开策略模板 + 设备本地私密注入 + ShellCrash 运行参数覆写**：

```text
mihomo/verge/config.yaml
        ↓ 生成并展开 YAML 锚点
mihomo/shellcrash/config-router*.template.yaml（按单/双订阅选择，公开、无秘密）
        ↓ 路由器本地注入订阅 URL
$CRASHDIR/yamls/config.yaml（私密）
        ↓ ShellCrash 生成最终运行配置
Mihomo
```

## 跨端策略约定

Apple 相关服务统一归入 `🍎 苹果服务`：Apple Intelligence / Private Cloud Compute 托管在第三方 CDN 上的中继（`apple-relay.cloudflare.com`、`apple-relay.fastly-edge.com`、`apple-relay.akamaized.net`、`cp4.cloudflare.com`）不在 blackmatrix7 Apple 列表中，由仓库的 `AppleExtra`（`rules/apple_extra.yaml`）补齐；三端原先单独加载的墨鱼 AppleIntelligence 清单已于 2026-10-05 并入同一文件，四端共用一个来源，并排在 Cloudflare 与 ProxyLite 之前，避免被送往开发服务或全球加速。

IPv6 在各端统一开启（QX 不设 `no-ipv6`；Windows 配置开启 IPv6；路由器本已开启）。QX 放行除 443 以外的全部 UDP 端口，并丢弃 UDP 443（QUIC），让 App 回退到 TCP，MitM 重写才能生效；2026-09-30 实测放开 QUIC 时 B 站去广告失效且关注页、热门页加载缓慢，恢复屏蔽后正常。QX 固定 `fallback_udp_policy = reject`，节点不支持 UDP 转发时拒绝而不直连。**Mihomo（Windows 与路由器）目前不提供同等保证**：v1.19.31 遇到不支持 UDP 的节点会跳过该规则继续匹配，可能命中后续直连规则或最终回落 DIRECT；当前机场节点均声明支持 UDP，如以后出现不支持 UDP 的节点，需要另行设计。

节点地区组按以下边界维护：香港、台湾、日本、韩国、新加坡和美国保留独立组；其余收敛为东南亚、亚洲其他、欧洲、美洲、大洋洲和非洲。南亚、中东、中亚、蒙古与澳门均属于“亚洲其他”；加拿大、墨西哥、中美洲、加勒比和南美洲均属于“美洲”，已独立的美国不会重复命中。澳大利亚、新西兰和太平洋岛国统一归入“大洋洲”。除美国节点组保留手动固定选择外，其余地区组均按健康检查延迟自动优选；故障转移组仍按可用性切换。不单设南极组，未命中地区的节点仍可从手动切换、自动选择和故障转移组使用。

Steam 不再单设策略组：客户端进程、平台域名和 21 条下载 CDN 补充规则均进入 `🎮 游戏平台`。平时可选择合适地区代理改善商店和社区访问；下载时临时切换为 `DIRECT`，完成后再切回。这会同时改变 Epic、Xbox、PlayStation 等其他游戏平台的出口。在 Windows 上，三个 `PROCESS-NAME` 规则只对已经进入 Mihomo 的流量生效；ShellCrash 看不到局域网客户端进程名，但会通过 `Game` 聚合规则和 Steam CDN 补充规则提供域名覆盖。Microsoft、Visual Studio、Office、winget 与 npm 下载仍始终保持 `DIRECT`，不受游戏平台组影响。

## Surge 的分层与有意差异

Surge 支持托管配置和分离配置，所以不再需要"本地 bootstrap 持有策略组"：仓库托管 `surge/proxy-config.conf` 的 `[General]`、`[Proxy Group]`、`[Rule]`、`[Host]`，设备上的 `bootstrap.conf` 用 `#!include` 引用这几段，节点（机场的完整 Surge 配置）、MitM、家庭 SSID 和机场 DNS（本地模块覆盖全局 `encrypted-dns-server`，Surge 没有按节点指定解析器的办法）留在本地。仓库改策略组后，设备随托管更新生效，没有 QX / Loon 的同步边界。

家庭 / 外出切换由 `surge/modules/home-direct.sgmodule` 中的一条 `SUBNET` 规则完成，取代 16 个包装组。AdRules 与 Privacy 两份拒绝列表放在同一个模块里、排在 SUBNET 之前，并带 `pre-matching`：DNS 与 TCP 在预匹配阶段被拒绝，UDP 按模块内的书写顺序先遇到拒绝。代价是拒绝列表排到了仓库规则之前：仓库列表里的部分子域（2026-10-02 快照中为 151 处，多为统计、追踪子域）在 Surge 上会被拦截，而 QX / Loon 会放行。银行与券商两个列表覆盖的域名通过 `reject_allow.list` 排除。误拦截时，换用只含 SUBNET 的 noblock 模块整体放行。

## QX 与路由器的分工

Quantumult X 可继续留在 iPhone / iPad 上承担 MitM、rewrite、脚本和内容层去广告；无需再维护第二份 QX 完整配置。由路由器负责外网分流时，QX 的常规代理出口应保持直连，让请求交给默认网关上的 ShellCrash，再由路由器决定直连或代理。这一切换与 Loon 相同，由 16 个「· 自动」ssid 策略自动完成：家庭 SSID 下为 DIRECT，其他 Wi-Fi 与蜂窝下为同名基础组；QX 全程保持规则分流，`🛡️ 安全防护` 不参与切换。包装组清单在 `scripts/build_rules.py` 的 `HOME_AUTO_GROUPS` 中维护，生成 `repo.snippet` 时直接使用，两端模板由测试校验与之一致。只有确实需要 QX 本机能力的流量才由 QX 处理，避免形成“QX 代理到节点后又经过路由器代理”的嵌套链路。即使设备走路由器，ShellCrash 的域名级广告拦截仍然生效；QX 专属的 MitM、rewrite、脚本和页面净化则只有 QX 保持运行并接管相应请求时才生效。
