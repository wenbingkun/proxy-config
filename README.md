# Proxy Config

统一管理 **Quantumult X**（iPhone / iPad）、**Clash / Mihomo**（Windows）和 **ShellCrash / Mihomo**（路由器）代理配置的 Git 仓库。

修改规则后只需 `git push`，所有设备在下次刷新周期内自动同步，无需手动操作。

---

## 目录

- [快速上手](#快速上手)
  - [Quantumult X（iPhone / iPad）](#quantumult-xiphone--ipad)
  - [Clash / Mihomo（Windows）](#clash--mihomowindows)
  - [ShellCrash / Mihomo（路由器）](#shellcrash--mihomo路由器)
- [日常维护](#日常维护)
- [添加自定义规则](#添加自定义规则)
- [目录结构](#目录结构)
- [设计思路](#设计思路)
- [安全说明](#安全说明)

---

## 快速上手

### Quantumult X（iPhone / iPad）

Quantumult X 采用 **本地 bootstrap + 远程 snippet** 架构，保证 MitM 证书等本地私密信息永远不会被远程配置覆盖。

**第一次配置（仅需一次）**

**第 1 步：获取 bootstrap 模板**

将仓库中的 `quantumultx/bootstrap.example.conf` 下载到本机，重命名为 `bootstrap.conf`。

你可以通过以下方式获取文件内容：

```
https://raw.githubusercontent.com/wenbingkun/proxy-config/main/quantumultx/bootstrap.example.conf
```

**第 2 步：填写本地私密信息**

用文本编辑器打开 `bootstrap.conf`，找到以下位置并填写：

```ini
[server_remote]
# 将下面这行注释去掉，替换为你的真实机场订阅链接
# https://your-subscription-url.com/api/v1/client/subscribe?token=your-token, tag=主机场, update-interval=86400, opt-parser=true, enabled=true

[mitm]
passphrase = 你的MitM密码短语
p12 =        你的p12证书（base64）
hostname =   需要解密的域名列表（如 *.example.com）
```

> MitM 信息可以从 Quantumult X 的"MitM"设置页面导出，或者生成新证书后复制过来。

**第 3 步：导入 Quantumult X**

在 Quantumult X 中，进入 **「配置文件」→「从文件导入」**，选择刚才编辑好的 `bootstrap.conf`。

导入完成后，bootstrap 中已预配置的远程资源（规则、重写、脚本等）会在 QX 首次刷新时自动拉取。

墨鱼规则同时使用其公开 GitHub 仓库和自建域名资源。`StartUpAds.conf`、`XiaoHongShuAds.conf`、`zhihu.ads.js` 和 `bdpan.ads.js` 会根据客户端 User-Agent 返回不同内容：Quantumult X 请求可获取有效规则或脚本，普通浏览器请求则可能返回 HTML 页面。仓库的远程资源检查会对 QX 资源模拟 Quantumult X 请求。

---

**后续更新（自动，无需操作）**

仓库中的规则文件（`quantumultx/filter_remote.snippet`）已在 bootstrap 中配置为远程资源：

```ini
[filter_remote]
https://raw.githubusercontent.com/wenbingkun/proxy-config/main/quantumultx/filter_remote.snippet, tag=仓库自定义规则, update-interval=86400, enabled=true
```

当你修改 `rules/` 下的规则并 `git push` 后，QX 会在下次自动更新周期（每 24 小时）拉取最新规则。你也可以在 QX 中手动触发「更新资源」强制立即刷新。

---

### Clash / Mihomo（Windows）

Clash 采用 **rule-providers** 架构，规则文件托管在 GitHub，客户端定期自动拉取。

**第一次配置（仅需一次）**

**第 1 步：按订阅数量下载主配置文件**

- 只使用一个订阅：下载 `clash/config-single.yaml`。
- 同时使用两个订阅：下载 `clash/config.yaml`。

两份文件来自同一策略源；`config-single.yaml` 由脚本生成，不包含 `Sub2` 或第二个订阅占位符。

**第 2 步：填写机场订阅链接**

打开下载的文件，找到 `proxy-providers` 部分，将占位符替换为你的真实订阅。单订阅文件只填写 `Sub.url`：

```yaml
proxy-providers:
  Sub:
    url: "https://your-subscription-url.com/subscription.yaml?token=your-token"
```

双订阅文件还需填写 `Sub2.url`；如果只有一个订阅，不要把 `Sub2` 留空或复制同一链接，请直接选择 `config-single.yaml`。

> 此文件保存在本地，不要将真实订阅链接提交到 Git。

**第 3 步：在 Clash Verge Rev 中导入本地配置**

进入左侧“订阅”页面，使用“新建”选择填写完成的 YAML 文件，或直接把文件拖入该页面，然后选中新增的配置卡片。Clash Verge Rev 会把所选文件复制到自己的 `profiles` 目录，之后移动原文件不会影响已导入副本；需要更换订阅 URL 时，应编辑 Verge 中的配置副本或重新导入。菜单行为以 [Clash Verge Rev 官方“本地配置”说明](https://www.clashverge.dev/guide/profile.html#本地配置) 为准。

**第 4 步：设置 Clash Verge Rev 开关**

主配置是运行参数的唯一来源，Verge 中可能覆写配置的开关建议如下：

| 开关 | 建议 | 原因 |
|---|---:|---|
| 系统代理 | 开启 | 让遵循系统代理的 Windows 应用进入 Mihomo |
| 开机自启、静默启动 | 按需开启 | 不改变分流语义 |
| 虚拟网卡 / TUN | 关闭 | 当前配置明确使用系统代理，不启用 TUN |
| 局域网连接 | 关闭 | 配置使用 `allow-lan: false`，避免向局域网暴露代理端口 |
| DNS 覆写 | 关闭 | 保留 YAML 中的 fake-ip、上游 DNS 和分流设置 |
| IPv6 | 关闭 | 配置统一使用 `ipv6: false` 和 `dns.ipv6: false`，避免旁路与泄漏 |
| 统一延迟 | 开启 | 与配置的 `unified-delay: true` 保持一致 |

不要用 Verge 开关把这些值反向覆盖。控制器仅监听 `127.0.0.1:9090`；如无额外鉴权，不应改成局域网地址。WSL2 镜像网络环境可直接使用 Windows 回环代理，例如 `http://127.0.0.1:7897`，无需开启“局域网连接”；若 WSL 使用其他网络模式，则应先确认宿主机可达地址和防火墙边界，再决定是否单独开放。

---

**后续更新（自动，无需操作）**

`config.yaml` 中所有自维护的规则集都通过 `rule-providers` 引用 GitHub Raw 地址：

```yaml
rule-providers:
  AIExtra:
    type: http
    behavior: domain
    url: "https://raw.githubusercontent.com/wenbingkun/proxy-config/main/clash/rulesets/ai_extra.yaml"
    interval: 86400
    # ...
```

当你修改 `rules/` 下的规则并 `git push` 后，Clash 会在下次自动更新周期（每 24 小时）拉取最新规则集。你也可以在 Clash 面板中手动触发 Provider 刷新。

第三方规则的名称和格式以其实际语义为准：Loyalsoldier 的 `private.txt` 是私有网络域名清单，在配置中命名为 `PrivateDomain` 并优先直连，不属于隐私或广告拦截；该项目的 `*.txt` 规则包含 YAML `payload`，因此 provider 使用 `format: yaml`。恶意域名由 URLhaus 域名列表提供并交给 `🛡️ 安全防护` 策略处理。

AI 分流使用 MetaCubeX 的 OpenAI、Anthropic、GitHub Copilot、Google Gemini 独立域名集，再由仓库的 `AIExtra` 补充其他服务（包括 JetBrains AI / Grazie 和沉浸式翻译），避免把整个支付、CDN 或通用云域名送入 AI 策略。Windows 和路由器端的 Steam、Epic、PlayStation、Xbox、Nintendo 和 Battle.net 统一由 `Game` 聚合规则送入 `🎮 游戏平台`，不再重复加载覆盖不完整的 Steam 独立 provider；QX 没有使用该 Clash 聚合 provider，仍保留各游戏平台的独立远程规则。V2EX（含 `v2ex.co`、`v2ex.pro` 静态资源）与 Linux.do（含 `ldstatic.com` 静态资源）均归入 `👨‍💻 开发服务`；Imgur（`imgur.com`、`imgur.io`、`imgurinc.com`）与 `redditspace.com` 统一归入 `🌐 社交平台`。

Clash 的 DAZN、Cloudflare 和 Amazon provider 只使用域名规则，不加载第三方 IP 段。这样仍可按服务域名分流，同时避免 Akamai、CloudFront、Cloudflare 等共享 CDN IP 地址把 JetBrains AI、RevenueCat、Sentry、Intercom、Let's Encrypt CRL 或 Bing 等无关请求误判为流媒体、电商或开发服务；未被专用域名规则命中的共享基础设施请求继续交给后续通用规则处理。纯域名版本使用独立缓存文件名，升级后不会误用原 classical 缓存。

不使用 GlobalMedia 聚合 provider：本轮 10 分钟路由器监看样本中，其命中的连接全部是误归类（Cloudflare Challenge、微软 Akamai 图片 CDN 等）或 `+.cloudfront.net`、`+.akamaized.net`、`+.llnwd.net` 一类的宽泛 CDN 通配，而 Netflix、Disney+、YouTube、HBO、Hulu、Prime Video、巴哈姆特、DAZN 等常用媒体均有独立 provider 覆盖。移除后 Cloudflare Challenge 由 Cloudflare 规则归入 `👨‍💻 开发服务`，微软 CDN 由 Microsoft 规则接管；仓库的 `MicrosoftExtra` 同时固定覆盖实测的微软 Akamai 图片域名，并补充上游缺失的 `msftstatic.com`。没有独立规则的冷门媒体服务会落入通用代理规则而非 `🎬 流媒体`，这是有意的取舍。

节点地区组按以下边界维护：香港、台湾、日本、韩国、新加坡和美国保留独立组；其余收敛为东南亚、亚洲其他、欧洲、美洲、大洋洲和非洲。南亚、中东、中亚、蒙古与澳门均属于“亚洲其他”；加拿大、墨西哥、中美洲、加勒比和南美洲均属于“美洲”，已独立的美国不会重复命中。澳大利亚、新西兰和太平洋岛国统一归入“大洋洲”。除美国节点组保留手动固定选择外，其余地区组均按健康检查延迟自动优选；故障转移组仍按可用性切换。不单设南极组，未命中地区的节点仍可从手动切换、自动选择和故障转移组使用。

Steam 不再单设策略组：客户端进程、平台域名和 21 条下载 CDN 补充规则均进入 `🎮 游戏平台`。平时可选择合适地区代理改善商店和社区访问；下载时临时切换为 `DIRECT`，完成后再切回。这会同时改变 Epic、Xbox、PlayStation 等其他游戏平台的出口。在 Windows 上，三个 `PROCESS-NAME` 规则只对已经进入 Mihomo 的流量生效；ShellCrash 看不到局域网客户端进程名，但会通过 `Game` 聚合规则和 Steam CDN 补充规则提供域名覆盖。Microsoft、Visual Studio、Office、winget 与 npm 下载仍始终保持 `DIRECT`，不受游戏平台组影响。

**去广告能力边界**

Mihomo 与 ShellCrash 的广告域名规则可以在 DNS / 域名层阻断已知广告和跟踪请求，但不能替代 Quantumult X 的 HTTPS MitM、rewrite 和脚本，也不能处理网页元素隐藏等内容层逻辑。Windows 建议组合使用本配置的域名分流和浏览器内容拦截扩展；不要把路由器规则等同于 QX 的完整去广告能力。

---

**关于 Clash 主配置远程更新**

公共 `config.yaml` 包含订阅占位符，不能在没有本地覆写或私密注入的情况下作为完整远程订阅直接运行：

```
https://raw.githubusercontent.com/wenbingkun/proxy-config/main/clash/config.yaml
```

Windows 当前仍采用“主配置保存在本地、rule-provider 自动更新”的方式。不要直接启用公共主配置自动覆盖，否则本地订阅 URL 会被占位符替换。

---

### ShellCrash / Mihomo（路由器）

路由器采用 **公开策略模板 + 设备本地私密注入 + ShellCrash 运行参数覆写**：

```text
clash/config.yaml
        ↓ 生成并展开 YAML 锚点
clash/config-router*.template.yaml（按单/双订阅选择，公开、无秘密）
        ↓ 路由器本地注入订阅 URL
$CRASHDIR/yamls/config.yaml（私密）
        ↓ ShellCrash 生成最终运行配置
Mihomo
```

仓库不接管路由器的端口、DNS、TUN、sniffer、控制器或防火墙，这些继续由 ShellCrash 管理。完整安装、首次部署、定时更新和回滚说明见 [`clash/shellcrash/README.md`](clash/shellcrash/README.md)。

Quantumult X 可继续留在 iPhone / iPad 上承担 MitM、rewrite、脚本和内容层去广告；无需再维护第二份 QX 完整配置。由路由器负责外网分流时，QX 的常规代理出口应保持直连，让请求交给默认网关上的 ShellCrash，再由路由器决定直连或代理。只有确实需要 QX 本机能力的流量才由 QX 处理，避免形成“QX 代理到节点后又经过路由器代理”的嵌套链路。即使设备走路由器，ShellCrash 的域名级广告拦截仍然生效；QX 专属的 MitM、rewrite、脚本和页面净化则只有 QX 保持运行并接管相应请求时才生效。

---

## 日常维护

日常使用中，你的操作只有以下几步：

```bash
# 1. 编辑规则文件（见下节"添加自定义规则"）
vim rules/ai_extra.yaml

# 2. 重新生成客户端专用文件
python3 scripts/build_rules.py

# 3. 生成 ShellCrash 路由器公开策略模板
python3 scripts/build_router_config.py

# 4. 可选：校验生成结果是否正确（返回 0 表示通过）
python3 scripts/build_rules.py --check
python3 scripts/build_router_config.py --check
python3 scripts/test_deploy_shellcrash.py
python3 scripts/test_rule_provider_scope.py

# 轻量检查外部规则、QX 脚本和图标，不会保存下载内容
python3 scripts/check_remote_resources.py --mode light

# 5. 推送到 GitHub
git add .
git commit -m "feat: 添加 xxx 规则"
git push
```

推送完成后：

| 客户端 | 同步方式 | 生效时间 |
|---|---|---|
| Quantumult X | 自动拉取 `filter_remote.snippet` | 下次刷新（最长 24h），或手动触发「更新资源」 |
| Clash / Mihomo | 自动拉取 `clash/rulesets/*.yaml` | 下次刷新（最长 24h），或手动触发 Provider 刷新 |
| ShellCrash / Mihomo | 路由器本地部署任务拉取并注入 `config-router.template.yaml` | 按本地任务计划，或手动运行部署脚本 |

远程资源轻量巡检每周由 GitHub Actions 自动执行：它只检查实际配置依赖，使用有限并发、超时和每项前 4 KiB 内容识别 404、HTML 错误页及错误图标类型，不 clone 上游仓库，也不把响应写入 Git。手工触发 `Remote Resources` workflow 时可选择 `full`，对 Clash 规则执行完整 YAML / 文本结构检查。失败日志会隐藏 URL 查询参数，避免泄漏可能存在的 token。

---

## 添加自定义规则

所有规则统一维护在 `rules/` 目录。

### 第 1 步：编辑或新建规则文件

规则文件为 YAML 格式，支持以下规则类型：

```yaml
# rules/my_service.yaml

domain_suffix:         # 匹配域名后缀（最常用）
  - example.com
  - api.example.com

domain:                # 精确匹配域名
  - exact.example.com

domain_keyword:        # 域名关键词匹配
  - example

domain_regex:          # 域名正则匹配
  - "^example\\..*"

ip_cidr:               # IPv4 CIDR
  - 1.2.3.0/24

ip_cidr6:              # IPv6 CIDR
  - 2001:db8::/32
```

### 第 2 步：在清单文件中注册

编辑 `rules/local_rules.yaml`，添加新规则集的映射：

```yaml
rule_sets:
  # ... 已有条目 ...

  - id: my_service          # 唯一 ID，用于生成文件名
    title: 我的服务规则       # 可读标题，用于注释
    source: my_service.yaml  # 对应的规则源文件名
    clash_policy: 🚀 手动切换 # Clash 代理策略组名称
    qx_policy: 🚀 手动切换   # Quantumult X 策略名称
```

> `clash_policy` 和 `qx_policy` 的值必须与你的客户端配置中的策略组名称完全一致。

### 第 3 步：生成并推送

```bash
python3 scripts/build_rules.py
git add .
git commit -m "feat: 添加 my_service 规则"
git push
```

脚本会自动生成：
- `clash/rulesets/my_service.yaml` — Clash rule-provider 格式
- `quantumultx/filter_remote.snippet` — QX filter 格式（整个文件重新生成）

---

## 目录结构

```
proxy-config/
│
├── rules/                          # 共享规则源（客户端无关）
│   ├── local_rules.yaml            # 规则清单：ID、策略名映射
│   ├── ai_extra.yaml               # AI 服务补充规则
│   ├── crypto_extra.yaml           # 加密货币补充规则
│   ├── ecommerce_extra.yaml        # 电商支付补充规则
│   ├── collaboration_extra.yaml    # 商务协作补充规则
│   ├── zoom.yaml                   # Zoom 规则
│   ├── social_media.yaml           # 社交平台补充规则
│   ├── crunchyroll.yaml            # Crunchyroll 规则
│   ├── dev_extra.yaml              # 开发服务补充规则
│   ├── stack_overflow.yaml         # Stack Exchange 规则
│   ├── speedtest.yaml              # 网络测速规则
│   ├── game_extra.yaml             # 游戏平台补充规则
│   ├── steam_download.yaml         # Steam 下载 CDN 专用规则
│   └── local_network.yaml          # 局域网 / 本地直连规则
│
├── quantumultx/                    # Quantumult X 客户端层
│   ├── bootstrap.example.conf      # bootstrap 模板（提交到 Git）
│   ├── bootstrap.conf              # 本地实际配置（gitignore，含私密信息）
│   ├── filter_remote.snippet       # 由 build_rules.py 生成，QX filter 格式
│   └── rewrite_remote.snippet      # QX 自定义 rewrite 规则片段
│
├── clash/                          # Clash / Mihomo 客户端层
│   ├── config.yaml                 # Clash 主配置（含 rule-providers 引用）
│   ├── config-single.yaml          # 由脚本生成的 Windows 单订阅完整配置
│   ├── config-router.template.yaml # 生成的 ShellCrash 公开策略模板
│   ├── config-router-single.template.yaml # 单订阅 ShellCrash 策略模板
│   ├── shellcrash/                  # ShellCrash 接入说明和私密参数示例
│   └── rulesets/                   # 由 build_rules.py 生成的 rule-provider 文件
│       ├── ai_extra.yaml
│       ├── crypto_extra.yaml
│       └── ...（其余同 rules/ 中的规则集）
│
├── scripts/
│   ├── build_rules.py              # 规则构建脚本
│   ├── build_router_config.py      # Windows 单订阅配置与路由器策略模板生成脚本
│   ├── check_remote_resources.py   # 外部规则、脚本和图标轻量/完整巡检
│   ├── deploy_shellcrash_config.sh # 路由器本地私密注入与部署脚本
│   ├── test_deploy_shellcrash.py   # 部署事务与回滚测试
│   ├── test_remote_resources.py    # 远程资源提取、脱敏和类型离线测试
│   ├── test_rule_provider_scope.py # 共享 CDN 规则误捕与专用域名覆盖回归测试
│   ├── test_shellcrash_override.py # ShellCrash 官方覆写流程集成测试
│   ├── test_region_groups.py       # Clash/QX 地区正则与地理边界回归测试
│   └── test_steam_policy.py        # Steam 与非 Steam 下载策略回归测试
│
├── .gitignore                      # 排除本地私密文件
├── AGENTS.md                       # AI 代理操作规范
└── README.md                       # 本文档
```

---

## 设计思路

### 核心问题

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

### 解决方案

**将"配置代码"和"运行状态"彻底分离。**

GitHub 只存可以公开的配置逻辑，设备本地只保存私密的运行状态，通过"远程模块"机制连接两者：

```
                    GitHub Repo
                         │
         ┌───────────────┼───────────────┐
         │               │               │
       rules/        quantumultx/      clash/
     共享规则源      QX 适配层         Clash 适配层
         │               │               │
         └───────┬────── ┘               │
                 │                       │
          build_rules.py                 │
                 │                       │
    ┌────────────┴──────┐    ┌───────────┴────────────┐
    │ filter_remote     │    │ rulesets/*.yaml         │
    │ .snippet          │    │ (rule-providers)        │
    └────────┬──────────┘    └───────────┬────────────┘
             │                           │
             ▼                           ▼
     Quantumult X                  Clash / Mihomo
    （bootstrap 本地持有，            （config.yaml 本地，
      snippet 远程拉取）               rulesets 远程拉取）
```

### 四大设计原则

**原则一：配置代码 ≠ 本地运行状态**

GitHub 管配置逻辑，设备管运行状态。MitM 证书和订阅链接永远不进入版本控制。

**原则二：bootstrap + 远程模块（QX）**

Quantumult X 本地持有一份 bootstrap.conf，包含本地私密信息、策略组和远程资源入口。仓库自维护的共享规则通过生成的远程 snippet 加载，第三方规则、重写和脚本通过 bootstrap 中的远程引用加载；GitHub 规则更新不会覆盖本地证书。

**原则三：rule-providers（Clash）**

Clash 主配置只定义代理分组和规则引用结构，具体规则内容通过 rule-providers 从 GitHub Raw 动态拉取。修改规则无需改动主配置，push 后自动生效。

**原则四：规则单源维护**

`rules/` 目录是仓库自维护共享规则的唯一编辑入口。`build_rules.py` 负责将其转换为各客户端所需的格式，确保这部分规则在 QX、Windows 和路由器之间一致；第三方规则仍由各客户端配置显式引用，并由远程资源巡检持续检查。

### 更新流程

```
编辑 rules/*.yaml
       │
       ▼
python3 scripts/build_rules.py
       │
       ├── 生成 clash/rulesets/*.yaml
       └── 生成 quantumultx/filter_remote.snippet
       │
       ▼
git push
       │
       ├── Clash 在下次刷新时拉取新 rulesets ──→ 规则生效
       └── QX 在下次刷新时拉取新 filter_remote ──→ 规则生效
```

---

## 安全说明

以下内容**绝对不能提交到 Git**：

| 内容 | 原因 |
|---|---|
| `quantumultx/bootstrap.conf` | 含真实订阅链接和 MitM 信息 |
| `*.p12` / `*.pem` / `*.crt` / `*.key` | MitM 私钥和证书 |
| 任何真实的订阅 token | 机场账号安全 |
| Cookie、API Key | 个人隐私 |

以上均已在 `.gitignore` 中排除。仓库中只保留：

- `bootstrap.example.conf`：去除所有私密信息的模板，用于首次配置参考
- `config.yaml`：订阅链接以 `https://example.com/...?token=replace-me` 占位
- `config-router*.template.yaml`：ShellCrash 单/双订阅公开策略模板，订阅地址仍为占位符
- `providers.env.example`：只含示例值；真实 `providers.env` 仅保存在路由器本地并设置为 `600`

在新设备上首次配置时，只需基于模板填写本地私密信息，后续规则更新完全自动化，无需再次操作。
