# Proxy Config

统一管理 **Quantumult X**（iPhone / iPad）、**Loon**（iPhone，试点中）、**Clash / Mihomo**（Windows）和 **ShellCrash / Mihomo**（路由器）代理配置的 Git 仓库。

修改已被客户端引用的共享规则后，先生成并提交客户端产物，再合入设备资源 URL 指向的分支（本仓库示例为 `main`）；设备在后续成功刷新时加载更新。策略组、资源引用及其他主配置的同步方式因客户端而异，见[日常维护](#日常维护)。

---

## 目录

- [快速上手](#快速上手)
  - [Quantumult X（iPhone / iPad）](#quantumult-xiphone--ipad)
  - [Loon（iPhone，试点）](#looniphone试点)
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

哔哩哔哩：使用仓库内冻结的旧版规则 `quantumultx/rewrite/bilibili_ad.conf`（deezertidal 转载的墨鱼 `biliad.conf`，最后更新 2023-06-08），其失效的 `bilibili_json.js` 已替换为仓库内 `quantumultx/scripts/bilibili_json.js`（墨鱼 GitHub 删除前的最后一版，2025-03-31）。上游均已停更，此版本不会再更新；它会解密 `app.bilibili.com` 与 `grpc.biliapi.net`，Quantumult X 下历史记录与评论区加载可能偏慢。不要与墨鱼自建站 `BiliBiliAds.conf` 或 Biliverse ADBlock 同时启用。

冻结版的规则、`bilibili_json.js` 和两个外部脚本（app2smile `bilibili-proto.js`、yjqiang `bilibili_dynamic.js`，链接固定到 2026-09-30 的提交）都不会随上游变化。它自带 hostname，使用 `opt-parser=true` 与原版一致，不需要 `#outhn=*`，也不依赖「仓库自定义重写」。脚本会把「我的」页会员字段改成大会员样式，这只影响客户端显示，不会获得服务端会员权益。冻结版中动态相关的两条规则原文都含 `DynAll`，其中 app2smile 那条同时处理视频页 `View/View`，因此若要排除它们，把 bootstrap 中的链接写成 `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/quantumultx/rewrite/bilibili_ad.conf#out=DynAll`（链接原本没有 `#` 参数，首个参数用 `#`，之后的参数才用 `&` 连接），代价是同时失去视频页广告过滤；此做法未经真机验证。

---

**后续更新（已引用规则内容自动刷新）**

仓库中的规则文件（`quantumultx/filter_remote.snippet`）已在 bootstrap 中配置为远程资源：

```ini
[filter_remote]
https://raw.githubusercontent.com/wenbingkun/proxy-config/main/quantumultx/filter_remote.snippet, tag=仓库自定义规则, update-interval=86400, enabled=true
```

修改 `rules/` 下的规则后，按[日常维护](#日常维护)生成 `filter_remote.snippet` 并合入 `main`，QX 会在后续按配置刷新（示例间隔 24 小时）成功加载时取得新规则；也可手动触发「更新资源」。这只更新 snippet 的内容；本地 `bootstrap.conf` 的策略组和资源行不会随之改变，见[日常维护](#日常维护)末尾的同步边界说明。

---

### Loon（iPhone，试点）

Loon 目前是试点客户端，用来验证 HTTP/2 MitM 下的哔哩哔哩去广告；QX 保留为回滚方案，两者不要同时开启 VPN。与 QX 相同，采用**本地 bootstrap + 远程规则**：模板 `loon/bootstrap.example.conf` 进 Git，填好私密信息的 `loon/bootstrap.conf` 只留在本地。

**第一次配置**

1. 下载 `loon/bootstrap.example.conf`，重命名为 `bootstrap.conf`：

   ```
   https://raw.githubusercontent.com/wenbingkun/proxy-config/main/loon/bootstrap.example.conf
   ```

2. 本地填写：
   - `[Remote Proxy]`：去掉注释，换成真实订阅。机场要求用专用 DNS 解析节点时，在这一行追加 `server-dns="..."`（需 Loon 3.5.2 以上）。
   - `HOME_SSID`：全部换成家里 Wi-Fi 的名称，包括 `[Proxy Group]` 中的每个 ssid 组和 `[Host]` 中的 `ssid:HOME_SSID`。2.4G 与 5G 名称不同时，每个 ssid 组各追加一项 `"<SSID>" = DIRECT`，`[Host]` 追加 `ssid:<SSID> = server:system`。
   - `[MitM]`：在 Loon 中生成并安装、信任 CA，再把 `ca-p12` 和 `ca-passphrase` 填到本地文件。
3. 导入 Loon，模式保持「规则」，开启 MitM 和「MitM over HTTP/2」。

**家庭 / 外出自动切换**

配置不使用 `ssid-trigger`，全程保持规则模式。策略页上方的 32 个组与 QX 同名同序，在这些组里选择节点。规则和插件实际引用的是排在最后的 16 个「· 自动」ssid 组（如 🤖 人工智能 · 自动、🐟 兜底分流 · 自动、🇭🇰 香港节点 · 自动）：连上家里 Wi-Fi 时走 DIRECT，由路由器负责代理；其他网络时走同名的 QX 组。「· 自动」组里没有需要选择的内容。Loon 不能隐藏策略组，所以把它们放在最后。这样在家时，规则层的广告拦截（🛡️ 安全防护）、插件改写和 MitM 仍然生效，而路由器已经不再拦截广告。配置的目标是在家时 DNS 通过 `ssid:HOME_SSID = server:system` 交给路由器；它与按域名指定的 DNS 映射谁优先，官方文档没有说明，要以请求记录中的实际上游为准。

**试点验收（日常使用前）**

1. SSID 切换：在家里 Wi-Fi 和蜂窝网络下分别访问一个会走代理的网站。请求记录中，「· 自动」组在家应为 DIRECT，在外应为同名 QX 组所选的节点。
2. 规则加载：`[Remote Rule]` 中每个列表的条数都不为 0，`geoip_cn.list` 应为 1 条。
3. GEOIP 命中：在蜂窝网络下访问 `http://114.114.114.114`（属于 CN，不在任何列表中）。请求记录应显示命中 `geoip_cn.list`，走 🇨🇳 国内服务。
4. 家里 DNS：查看请求或 DNS 记录，确认没有专门映射的域名由路由器（system）解析。

**插件**

试点阶段只启用 kokoryh 的哔哩哔哩插件（Sparkle，可莉插件库也分发同一作者的版本）。它要求 Loon 3.5.1 (992) 及以上，并需要在 Loon 的 MitM 设置里打开「MitM over HTTP/2」。

- **为什么换插件**：之前用的 Biliverse ADBlock v0.6.27 在新版详情页的播放器下方留下了一个广告卡片。我们怀疑它来自 `viewunite.v1.View/AIRelateAsync`：Biliverse 不处理这个接口，而这个插件会处理。2026-09-30 真机确认：换用这个插件后广告卡片消失，页面速度没有变慢。插件还会精简底栏和「我的」页。
- **跟随上游**：插件及其脚本跟随上游 master 更新，没有固定版本；试点测试时的版本是提交 `1bc5b545a544`。远程资源巡检只检查插件入口地址能否访问，插件内部加载的脚本和 jq 文件是否正常，要看 Loon 的日志。
- **全局影响**：插件会拦截 B 站的备用 API 域名（`app/api.biliapi.com`、`app/api.biliapi.net`）。它还会拦截目标端口 4480、4483、8082、9102，用来阻断 B 站 P2P。这条端口规则不限域名，**对所有 App 都生效**；如果某个 App 需要放行，只针对它的目标主机和端口单独放行，不要放开整个端口。
- **默认开启的额外功能**：「空降助手」会用视频 ID 查询第三方服务器 `bsbsb.top`，获取可跳过的片段；弹幕请求要等这次查询完成才返回，最多等 3 秒。「优化评论区加载」会在请求阶段接管详情页和评论区的请求。两者都可以在 Loon 的插件页关闭。第一次核对详情页广告时，建议先关掉空降助手，减少干扰。
- **显示效果**：插件会把「我的」页显示为大会员样式，只改显示，不获得权益。

不要与其他 B 站改写（包括 Biliverse）同时启用。

其他插件按来源分为以下几类。本次新增的插件都默认关闭，逐个开启、在对应 App 上验证后，再改为默认开启：

| QX 中的来源 | Loon 中的做法 |
|---|---|
| 墨鱼：YouTube、小红书、高德、知乎、彩云天气、喜马拉雅、网易云、百度网盘、微信外链解锁 | 可莉（kelee.one）的原生插件。kelee.one 只响应完整的 iOS Loon UA，远程资源巡检因此使用 `Loon/3.5.2 (996) CFNetwork/3826 Darwin/25.0.0` |
| 墨鱼：微博、闲鱼、豆瓣网页、Safari 超级搜索、神机重定向 | 闲鱼等四项没有 Loon 版本；微博的可莉原生版真机效果不如 QX 干净（2026-10-01），也改用墨鱼原版转换。仓库在 `loon/plugins/` 托管冻结的转换结果（Script-Hub `6b4fb62` 转换），脚本地址固定到审核过的上游提交；文件头注释写明来源地址、原文件哈希和重新生成的方法 |
| 墨鱼：开屏、微信小程序 | 不单独移植。依赖默认开启的 blackmatrix7 去广告合集（它覆盖了墨鱼开屏 437 个解密域名中的 373 个、小程序 35 个中的 34 个）。这是条件覆盖，发现漏网再补 |
| 墨鱼：专属 VIP（ForOwnUse） | 不移植（只剩一条付费内容解锁规则） |
| KOP-XIAO 三个定时任务 | 可莉「节点检测工具」提供手动诊断：在节点上长按，可查询入口/落地、地理位置、流媒体解锁；**不再有定时通知** |

需要注意的跨 App 影响：
- **闲鱼插件的 AMDC 脚本**：不只作用于闲鱼。它按 UA 匹配高德、菜鸟、天猫、飞猪、盒马等阿里系 App，改写它们通过明文 HTTP 发出的 `/amdc/mobileDispatch` 调度请求（QX 中墨鱼的闲鱼和高德规则也有同样的处理）。开启后要一并检查这些 App。
- **Safari 超级搜索**：作用在 DuckDuckGo 的搜索地址上，需要把 Safari 的默认搜索引擎设为 DuckDuckGo。
- **与合集规则重叠**：专用插件排在 blackmatrix7 合集之前，两者匹配同一个请求时，先执行专用插件的脚本。少数接口两边都有拦截规则，但拦截方式可能不同（直接断开，或返回空内容 / `{}` / `[]`），以真机上的实际命中和页面表现为准。

**后续更新**

`[Remote Rule]` 引用 `loon/rules/*.list`（由 `build_rules.py` 生成）和 `loon/geoip_cn.list`。`GEOIP,CN` 放在远程列表的最后，而不是本地 `[Rule]`：Loon 的本地规则优先于订阅规则，放在本地会抢先于 Privacy 等列表里的国内 IP 规则。规则内容按 Loon 的资源刷新机制更新；策略组和资源行的变更需要同步到本地 `bootstrap.conf`，同步边界与 QX 相同。

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
| IPv6 | 开启 | 配置使用 `ipv6: true` 和 `dns.ipv6: true`，与已开启 IPv6 的家庭网络和路由器保持一致 |
| 统一延迟 | 开启 | 与配置的 `unified-delay: true` 保持一致 |

不要用 Verge 开关把这些值反向覆盖。控制器仅监听 `127.0.0.1:9090`；如无额外鉴权，不应改成局域网地址。WSL2 镜像网络环境可直接使用 Windows 回环代理，例如 `http://127.0.0.1:7897`，无需开启“局域网连接”；若 WSL 使用其他网络模式，则应先确认宿主机可达地址和防火墙边界，再决定是否单独开放。

---

**后续更新（已引用规则内容自动刷新）**

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

修改 `rules/` 下的规则后，按[日常维护](#日常维护)生成 `clash/rulesets/*.yaml` 并合入 `main`，Clash 会在后续按配置刷新（示例间隔 24 小时）成功加载时取得新规则集；也可在面板中手动触发 Provider 刷新。这只更新已引用规则集的内容；Verge 中配置副本的策略组、`rule-providers` 与 `rules` 定义不会随之改变，见[日常维护](#日常维护)末尾的同步边界说明。

第三方规则的名称和格式以其实际语义为准：Loyalsoldier 的 `private.txt` 是私有网络域名清单，在配置中命名为 `PrivateDomain` 并优先直连，不属于隐私或广告拦截；该项目的 `*.txt` 规则包含 YAML `payload`，因此 provider 使用 `format: yaml`。恶意域名由 URLhaus 域名列表提供并交给 `🛡️ 安全防护` 策略处理。

AI 分流使用 MetaCubeX 的 OpenAI、Anthropic、GitHub Copilot、Google Gemini 独立域名集，再由仓库的 `AIExtra` 补充其他服务（包括 JetBrains AI / Grazie 和沉浸式翻译），避免把整个支付、CDN 或通用云域名送入 AI 策略。Windows 和路由器端的 Steam、Epic、PlayStation、Xbox、Nintendo 和 Battle.net 统一由 `Game` 聚合规则送入 `🎮 游戏平台`，不再重复加载覆盖不完整的 Steam 独立 provider；Just Dance 新版主机服务（`just-dance.com`）和 Just Dance Now（`justdancenow.com`）由 `GameExtra` 补齐，Ubisoft 登录、`cdn.ubi.com` 内容和 Nintendo 平台域名继续由 `Game` 接管。QX 没有使用该 Clash 聚合 provider，仍保留各游戏平台的独立远程规则，并通过生成的本地补充规则覆盖 Just Dance。V2EX（含 `v2ex.co`、`v2ex.pro` 静态资源）与 Linux.do（含 `ldstatic.com` 静态资源）均归入 `👨‍💻 开发服务`；Imgur（`imgur.com`、`imgur.io`、`imgurinc.com`）与 `redditspace.com` 统一归入 `🌐 社交平台`。

Clash 的 DAZN、Cloudflare 和 Amazon provider 只使用域名规则，不加载第三方 IP 段。这样仍可按服务域名分流，同时避免 Akamai、CloudFront、Cloudflare 等共享 CDN IP 地址把 JetBrains AI、RevenueCat、Sentry、Intercom、Let's Encrypt CRL 或 Bing 等无关请求误判为流媒体、电商或开发服务；未被专用域名规则命中的共享基础设施请求继续交给后续通用规则处理。纯域名版本使用独立缓存文件名，升级后不会误用原 classical 缓存。

不使用 GlobalMedia 聚合 provider：本轮 10 分钟路由器监看样本中，其命中的连接全部是误归类（Cloudflare Challenge、微软 Akamai 图片 CDN 等）或 `+.cloudfront.net`、`+.akamaized.net`、`+.llnwd.net` 一类的宽泛 CDN 通配，而 Netflix、Disney+、YouTube、HBO、Hulu、Prime Video、巴哈姆特、DAZN 等常用媒体均有独立 provider 覆盖。移除后 Cloudflare Challenge 由 Cloudflare 规则归入 `👨‍💻 开发服务`，微软 CDN 由 Microsoft 规则接管；仓库的 `MicrosoftExtra` 同时固定覆盖实测的微软 Akamai 图片域名，并补充上游缺失的 `msftstatic.com`。没有独立规则的冷门媒体服务会落入通用代理规则而非 `🎬 流媒体`，这是有意的取舍。

Apple 相关服务统一归入 `🍎 苹果服务`：QX 的 Apple Intelligence 远程清单改为强制该组；Apple Intelligence / Private Cloud Compute 托管在第三方 CDN 上的中继（`apple-relay.cloudflare.com`、`apple-relay.fastly-edge.com`、`apple-relay.akamaized.net`、`cp4.cloudflare.com`）不在 blackmatrix7 Apple 列表中，由仓库的 `AppleExtra` 补齐，并排在 Cloudflare 与 ProxyLite 之前，避免被送往开发服务或全球加速。

IPv6 在各端统一开启（QX 不设 `no-ipv6`；Windows 配置开启 IPv6；路由器本已开启）。QX 放行除 443 以外的全部 UDP 端口，并丢弃 UDP 443（QUIC），让 App 回退到 TCP，MitM 重写才能生效；2026-09-30 实测放开 QUIC 时 B 站去广告失效且关注页、热门页加载缓慢，恢复屏蔽后正常。QX 固定 `fallback_udp_policy = reject`，节点不支持 UDP 转发时拒绝而不直连。**Mihomo（Windows 与路由器）目前不提供同等保证**：v1.19.31 遇到不支持 UDP 的节点会跳过该规则继续匹配，可能命中后续直连规则或最终回落 DIRECT；当前机场节点均声明支持 UDP，如以后出现不支持 UDP 的节点，需要另行设计。

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
| Quantumult X | 自动拉取 `filter_remote.snippet` | 按配置刷新（示例间隔 24h），成功加载后生效；也可手动触发「更新资源」 |
| Loon | 自动拉取 `loon/rules/*.list` | 按 Loon 的资源刷新生效；也可在 App 中手动更新 |
| Clash / Mihomo | 自动拉取 `clash/rulesets/*.yaml` | 按配置刷新（示例间隔 24h），成功加载后生效；也可手动触发 Provider 刷新 |
| ShellCrash / Mihomo | 路由器本地部署任务拉取并注入 `config-router.template.yaml` | 按本地任务计划，或手动运行部署脚本 |

**主配置与远程资源内容的同步边界**

上表说明已配置资源或部署任务的更新方式，不代表推送任意文件都会更新设备。按本仓库的本地导入方式使用 QX、Clash Verge Rev 时，还需区分：

| 改动类型 | Quantumult X | Clash Verge Rev（Windows） | ShellCrash（路由器） |
|---|---|---|---|
| 已引用的远程规则内容 | 资源成功刷新后加载；引用的策略组须已存在 | 对应 rule-provider 成功刷新后加载 | 对应 rule-provider 成功刷新后加载 |
| 策略组名称、候选、类型和测速参数 | 更新本地配置的 `[policy]` | 编辑 Verge 中的配置副本，或重新导入对应完整配置 | 由已配置的部署任务成功应用策略模板；否则手动执行部署流程 |
| 增删资源引用、调整主配置中的规则映射 | 更新 `[filter_remote]` 资源行及 `force-policy`，或相应 `[filter_local]` | 更新配置副本中的 `rule-providers`、`rules` 等定义 | 同上，仅限策略模板管理的部分 |
| 其他本地主配置 | `[general]`、`[dns]`、`[rewrite_remote]`、`[task_local]` 等条目需按修改内容手动同步；已有远程内容仍按各自机制更新 | 主配置运行参数及 Verge 本地覆写分别按实际来源修改 | ShellCrash 本地 DNS、TUN、sniffer、任务及覆写设置按各自部署说明处理，不由策略模板统一覆盖 |

手动同步时只改对应条目，保留 QX 的订阅、MitM 私密材料及其他本地设置；Windows 重新导入前也需保留或重新填写本地订阅及必要设置。设备已保存的组选择不保证随候选顺序变化而重置；原候选被删除时，需检查客户端实际回退到哪一项。

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
- `loon/rules/my_service.list` — Loon 规则列表，不含策略；还需在 `loon/bootstrap.example.conf` 的 `[Remote Rule]` 中加一行，`policy=` 与 `qx_policy` 相同

Loon 不支持 `domain_regex`，规则源里出现该类型时，生成器会直接报错。

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
├── loon/                           # Loon 客户端层（试点）
│   ├── bootstrap.example.conf      # bootstrap 模板（提交到 Git）
│   ├── bootstrap.conf              # 本地实际配置（gitignore，含私密信息）
│   ├── geoip_cn.list               # GEOIP,CN，放在远程规则最后
│   ├── plugins/                    # 冻结托管的墨鱼 QX 重写的 Loon 转换版
│   └── rules/                      # 由 build_rules.py 生成的 Loon 规则列表
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
│   ├── test_loon_config.py         # Loon 家庭出口、与 QX 分组对齐及规则映射检查
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
       ├── 生成 quantumultx/filter_remote.snippet
       └── 生成 loon/rules/*.list
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
| `loon/bootstrap.conf` | 含真实订阅链接、家庭 SSID 和 MitM 信息 |
| `*.p12` / `*.pem` / `*.crt` / `*.key` | MitM 私钥和证书 |
| 任何真实的订阅 token | 机场账号安全 |
| Cookie、API Key | 个人隐私 |

以上均已在 `.gitignore` 中排除。仓库中只保留：

- `bootstrap.example.conf`：去除所有私密信息的模板，用于首次配置参考
- `config.yaml`：订阅链接以 `https://example.com/...?token=replace-me` 占位
- `config-router*.template.yaml`：ShellCrash 单/双订阅公开策略模板，订阅地址仍为占位符
- `providers.env.example`：只含示例值；真实 `providers.env` 仅保存在路由器本地并设置为 `600`

在新设备上首次配置时，只需基于模板填写本地私密信息，后续规则更新完全自动化，无需再次操作。
