# Clash Verge Rev / Mihomo（Windows）

> 返回 [总览](../../README.md)。下文仓库命令均在仓库根目录执行。

本目录的 `config.yaml` 同时是路由器模板的唯一来源，见 [ShellCrash 接入](../shellcrash/README.md)；规则集在 [`mihomo/rules/`](../rules/)，由共享规则源生成。

Clash 采用 **rule-providers** 架构，规则文件托管在 GitHub，客户端定期自动拉取。

## 第一次配置（仅需一次）

### 第 1 步：按订阅数量下载主配置文件

- 只使用一个订阅：下载 `mihomo/verge/config-single.yaml`。
- 同时使用两个订阅：下载 `mihomo/verge/config.yaml`。

两份文件来自同一策略源；`config-single.yaml` 由脚本生成，不包含 `Sub2` 或第二个订阅占位符。

### 第 2 步：填写机场订阅链接

打开下载的文件，找到 `proxy-providers` 部分，将占位符替换为你的真实订阅。单订阅文件只填写 `Sub.url`：

```yaml
proxy-providers:
  Sub:
    url: "https://your-subscription-url.com/subscription.yaml?token=your-token"
```

双订阅文件还需填写 `Sub2.url`；如果只有一个订阅，不要把 `Sub2` 留空或复制同一链接，请直接选择 `config-single.yaml`。

> 此文件保存在本地，不要将真实订阅链接提交到 Git。

### 第 3 步：在 Clash Verge Rev 中导入本地配置

进入左侧“订阅”页面，使用“新建”选择填写完成的 YAML 文件，或直接把文件拖入该页面，然后选中新增的配置卡片。Clash Verge Rev 会把所选文件复制到自己的 `profiles` 目录，之后移动原文件不会影响已导入副本；需要更换订阅 URL 时，应编辑 Verge 中的配置副本或重新导入。菜单行为以 [Clash Verge Rev 官方“本地配置”说明](https://www.clashverge.dev/guide/profile.html#本地配置) 为准。

### 第 4 步：设置 Clash Verge Rev 开关

主配置是运行参数的唯一来源，Verge 中可能覆写配置的开关建议如下：

| 开关 | 建议 | 原因 |
|---|---:|---|
| 系统代理 | 开启 | 让遵循系统代理的 Windows 应用进入 Mihomo |
| 开机自启、静默启动 | 按需开启 | 不改变分流语义 |
| 虚拟网卡 / TUN | 关闭 | 当前配置明确使用系统代理，不启用 TUN |
| 局域网连接 | 关闭 | 配置使用 `allow-lan: false`，避免向局域网暴露代理端口 |
| DNS 覆写 | 关闭 | 保留 YAML 中的 fake-ip、上游 DNS 和分流设置 |
| IPv6 | 开启 | 配置使用 `ipv6: true` 和 `dns.ipv6: true`，与已开启 IPv6 的家庭网络和路由器保持一致 |
| 统一延迟 | 开启 | 与配置的 `unified-delay: true` 保持一致 |

不要用 Verge 开关把这些值反向覆盖。控制器仅监听 `127.0.0.1:9090`；如无额外鉴权，不应改成局域网地址。WSL2 镜像网络环境可直接使用 Windows 回环代理，例如 `http://127.0.0.1:7897`，无需开启“局域网连接”；若 WSL 使用其他网络模式，则应先确认宿主机可达地址和防火墙边界，再决定是否单独开放。

## 后续更新（已引用规则内容自动刷新）

`config.yaml` 中所有自维护的规则集都通过 `rule-providers` 引用 GitHub Raw 地址：

```yaml
rule-providers:
  AIExtra:
    type: http
    behavior: domain
    url: "https://raw.githubusercontent.com/wenbingkun/proxy-config/main/mihomo/rules/ai_extra.yaml"
    interval: 86400
    # ...
```

修改 `rules/` 下的规则后，按[日常维护](../../rules/README.md#日常维护)生成 `mihomo/rules/*.yaml` 并合入 `main`，Clash 会在后续按配置刷新（示例间隔 24 小时）成功加载时取得新规则集；也可在面板中手动触发 Provider 刷新。这只更新已引用规则集的内容；Verge 中配置副本的策略组、`rule-providers` 与 `rules` 定义不会随之改变，见[日常维护](../../rules/README.md#日常维护)末尾的同步边界说明。

## Mihomo 规则取舍（Windows 与路由器共用）

第三方规则的名称和格式以其实际语义为准：Loyalsoldier 的 `private.txt` 是私有网络域名清单，在配置中命名为 `PrivateDomain` 并优先直连，不属于隐私或广告拦截；该项目的 `*.txt` 规则包含 YAML `payload`，因此 provider 使用 `format: yaml`。恶意域名由 URLhaus 域名列表提供并交给 `🛡️ 安全防护` 策略处理。

AI 分流使用 MetaCubeX 的 OpenAI、Anthropic、GitHub Copilot、Google Gemini 独立域名集，再由仓库的 `AIExtra` 补充其他服务（包括 JetBrains AI / Grazie 和沉浸式翻译），避免把整个支付、CDN 或通用云域名送入 AI 策略。Windows 和路由器端的 Steam、Epic、PlayStation、Xbox、Nintendo 和 Battle.net 统一由 `Game` 聚合规则送入 `🎮 游戏平台`，不再重复加载覆盖不完整的 Steam 独立 provider；Just Dance 新版主机服务（`just-dance.com`）和 Just Dance Now（`justdancenow.com`）由 `GameExtra` 补齐，Ubisoft 登录、`cdn.ubi.com` 内容和 Nintendo 平台域名继续由 `Game` 接管。QX 没有使用该 Clash 聚合 provider，仍保留各游戏平台的独立远程规则，并通过生成的本地补充规则覆盖 Just Dance。V2EX（含 `v2ex.co`、`v2ex.pro` 静态资源）与 Linux.do（含 `ldstatic.com` 静态资源）均归入 `👨‍💻 开发服务`；Imgur（`imgur.com`、`imgur.io`、`imgurinc.com`）与 `redditspace.com` 统一归入 `🌐 社交平台`。

Clash 的 DAZN、Cloudflare 和 Amazon provider 只使用域名规则，不加载第三方 IP 段。这样仍可按服务域名分流，同时避免 Akamai、CloudFront、Cloudflare 等共享 CDN IP 地址把 JetBrains AI、RevenueCat、Sentry、Intercom、Let's Encrypt CRL 或 Bing 等无关请求误判为流媒体、电商或开发服务；未被专用域名规则命中的共享基础设施请求继续交给后续通用规则处理。纯域名版本使用独立缓存文件名，升级后不会误用原 classical 缓存。

不使用 GlobalMedia 聚合 provider：本轮 10 分钟路由器监看样本中，其命中的连接全部是误归类（Cloudflare Challenge、微软 Akamai 图片 CDN 等）或 `+.cloudfront.net`、`+.akamaized.net`、`+.llnwd.net` 一类的宽泛 CDN 通配，而 Netflix、Disney+、YouTube、HBO、Hulu、Prime Video、巴哈姆特、DAZN 等常用媒体均有独立 provider 覆盖。移除后 Cloudflare Challenge 由 Cloudflare 规则归入 `👨‍💻 开发服务`，微软 CDN 由 Microsoft 规则接管；仓库的 `MicrosoftExtra` 同时固定覆盖实测的微软 Akamai 图片域名，并补充上游缺失的 `msftstatic.com`。没有独立规则的冷门媒体服务会落入通用代理规则而非 `🎬 流媒体`，这是有意的取舍。

跨端一致的策略约定（Apple 服务、IPv6 / UDP、地区组、Steam）见 [设计说明](../../docs/design.md#跨端策略约定)。

## 去广告能力边界

Mihomo 与 ShellCrash 的广告域名规则可以在 DNS / 域名层阻断已知广告和跟踪请求，但不能替代 Quantumult X 的 HTTPS MitM、rewrite 和脚本，也不能处理网页元素隐藏等内容层逻辑。Windows 建议组合使用本配置的域名分流和浏览器内容拦截扩展；不要把路由器规则等同于 QX 的完整去广告能力。

## 关于 Clash 主配置远程更新

公共 `config.yaml` 包含订阅占位符，不能在没有本地覆写或私密注入的情况下作为完整远程订阅直接运行：

```
https://raw.githubusercontent.com/wenbingkun/proxy-config/main/mihomo/verge/config.yaml
```

Windows 当前仍采用“主配置保存在本地、rule-provider 自动更新”的方式。不要直接启用公共主配置自动覆盖，否则本地订阅 URL 会被占位符替换。
