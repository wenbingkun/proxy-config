# Loon（iPhone，试点）

> 返回 [总览](../README.md)。下文仓库命令均在仓库根目录执行。

Loon 目前是试点客户端，用来验证 HTTP/2 MitM 下的哔哩哔哩去广告；QX 保留为回滚方案，两者不要同时开启 VPN。与 QX 相同，采用**本地 bootstrap + 远程规则**：模板 `loon/bootstrap.example.conf` 进 Git，填好私密信息的 `loon/bootstrap.conf` 只留在本地。

## 第一次配置

1. 下载 `loon/bootstrap.example.conf`，重命名为 `bootstrap.conf`：

   ```
   https://raw.githubusercontent.com/wenbingkun/proxy-config/main/loon/bootstrap.example.conf
   ```

2. 本地填写：
   - `[Remote Proxy]`：去掉注释，换成真实订阅。机场要求用专用 DNS 解析节点时，在这一行追加 `server-dns="..."`（需 Loon 3.5.2 以上）。
   - `HOME_SSID`：全部换成家里 Wi-Fi 的名称，包括 `[Proxy Group]` 中的每个 ssid 组和 `[Host]` 中的 `ssid:HOME_SSID`。2.4G 与 5G 名称不同时，每个 ssid 组各追加一项 `"<SSID>" = DIRECT`，`[Host]` 追加 `ssid:<SSID> = server:system`。
   - `[MitM]`：在 Loon 中生成并安装、信任 CA，再把 `ca-p12` 和 `ca-passphrase` 填到本地文件。
3. 导入 Loon，模式保持「规则」，开启 MitM 和「MitM over HTTP/2」。

## 家庭 / 外出自动切换

配置不使用 `ssid-trigger`，全程保持规则模式。策略页上方的 32 个组与 QX 同名同序，在这些组里选择节点。规则和插件实际引用的是排在最后的 16 个「· 自动」ssid 组（如 🤖 人工智能 · 自动、🐟 兜底分流 · 自动、🇭🇰 香港节点 · 自动）：连上家里 Wi-Fi 时走 DIRECT，由路由器负责代理；其他网络时走同名的 QX 组。「· 自动」组里没有需要选择的内容。Loon 不能隐藏策略组，所以把它们放在最后。这样在家时，规则层的广告拦截（🛡️ 安全防护）、插件改写和 MitM 仍然生效，而路由器已经不再拦截广告。配置的目标是在家时 DNS 通过 `ssid:HOME_SSID = server:system` 交给路由器；它与按域名指定的 DNS 映射谁优先，官方文档没有说明，要以请求记录中的实际上游为准。

## 试点验收（日常使用前）

1. SSID 切换：在家里 Wi-Fi 和蜂窝网络下分别访问一个会走代理的网站。请求记录中，「· 自动」组在家应为 DIRECT，在外应为同名 QX 组所选的节点。
2. 规则加载：`[Remote Rule]` 中每个列表的条数都不为 0，`geoip_cn.list` 应为 1 条。
3. GEOIP 命中：在蜂窝网络下访问 `http://114.114.114.114`（属于 CN，不在任何列表中）。请求记录应显示命中 `geoip_cn.list`，走 🇨🇳 国内服务。
4. 家里 DNS：查看请求或 DNS 记录，确认没有专门映射的域名由路由器（system）解析。

## 插件

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
| 墨鱼：YouTube、小红书、高德、知乎、微信外链解锁 | 可莉（kelee.one）的原生插件。kelee.one 只响应完整的 iOS Loon UA，远程资源巡检因此使用 `Loon/3.5.2 (996) CFNetwork/3826 Darwin/25.0.0` |
| 墨鱼：微博、闲鱼、豆瓣网页、Safari 超级搜索、神机重定向 | 闲鱼等四项没有 Loon 版本；微博的可莉原生版真机效果不如 QX 干净（2026-10-01），也改用墨鱼原版转换。仓库在 `loon/plugins/` 托管冻结的转换结果（Script-Hub `6b4fb62` 转换），脚本地址固定到审核过的上游提交；文件头注释写明来源地址、原文件哈希和重新生成的方法 |
| 2026-10-05 新增：墨鱼的网易邮箱大师、小宇宙、大麦、航旅纵横、12306，Maasea 的微信读书精简（Surge 模块） | 为三端用同一来源，统一用 Script-Hub `6b4fb62` 冻结转换，托管在 `loon/plugins/`，默认关闭；没有和可莉的版本比较。航旅纵横的脚本处理二进制响应体，已加 `binary-body-mode=true`；它和大麦的来源在 ddgksf2013.top 上，没有提交可固定，脚本副本托管在 `quantumultx/scripts/`，并修正了上游没有接上的 JSON 清理入口（`tests/test_umetrip_script.py`） |
| 墨鱼：开屏、微信小程序 | 不单独移植。依赖默认开启的 blackmatrix7 去广告合集（它覆盖了墨鱼开屏 437 个解密域名中的 373 个、小程序 35 个中的 34 个）。这是条件覆盖，发现漏网再补 |
| 墨鱼：专属 VIP（ForOwnUse） | 不移植（只剩一条付费内容解锁规则） |
| KOP-XIAO 三个定时任务 | 可莉「节点检测工具」提供手动诊断：在节点上长按，可查询入口/落地、地理位置、流媒体解锁；**不再有定时通知** |

需要注意的跨 App 影响：
- **闲鱼插件的 AMDC 脚本**：不只作用于闲鱼。它按 UA 匹配高德、菜鸟、天猫、飞猪、盒马等阿里系 App，改写它们通过明文 HTTP 发出的 `/amdc/mobileDispatch` 调度请求（QX 中墨鱼的闲鱼和高德规则也有同样的处理）。开启后要一并检查这些 App。
- **Safari 超级搜索**：作用在 DuckDuckGo 的搜索地址上，需要把 Safari 的默认搜索引擎设为 DuckDuckGo。
- **与合集规则重叠**：专用插件排在 blackmatrix7 合集之前，两者匹配同一个请求时，先执行专用插件的脚本。少数接口两边都有拦截规则，但拦截方式可能不同（直接断开，或返回空内容 / `{}` / `[]`），以真机上的实际命中和页面表现为准。

## 后续更新

`[Remote Rule]` 引用 `loon/rules/*.list`（由 `build_rules.py` 生成）和 `loon/rules/geoip_cn.list`。`GEOIP,CN` 放在远程列表的最后，而不是本地 `[Rule]`：Loon 的本地规则优先于订阅规则，放在本地会抢先于 Privacy 等列表里的国内 IP 规则。规则内容按 Loon 的资源刷新机制更新；策略组和资源行的变更需要同步到本地 `bootstrap.conf`，同步边界与 QX 相同。
