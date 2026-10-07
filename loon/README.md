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

哔哩哔哩使用 kokoryh 的插件（Sparkle，可莉插件库也分发同一作者的版本）。它要求 Loon 3.5.1 (992) 及以上，并需要在 Loon 的 MitM 设置里打开「MitM over HTTP/2」。

- **为什么换插件**：之前用的 Biliverse ADBlock v0.6.27 在新版详情页的播放器下方留下了一个广告卡片。我们怀疑它来自 `viewunite.v1.View/AIRelateAsync`：Biliverse 不处理这个接口，而这个插件会处理。2026-09-30 真机确认：换用这个插件后广告卡片消失，页面速度没有变慢。插件还会精简底栏和「我的」页。
- **跟随上游**：插件及其脚本跟随上游 master 更新，没有固定版本；试点测试时的版本是提交 `1bc5b545a544`。远程资源巡检只检查插件入口地址能否访问，插件内部加载的脚本和 jq 文件是否正常，要看 Loon 的日志。
- **全局影响**：插件会拦截 B 站的备用 API 域名（`app/api.biliapi.com`、`app/api.biliapi.net`）。它还会拦截目标端口 4480、4483、8082、9102，用来阻断 B 站 P2P。这条端口规则不限域名，**对所有 App 都生效**；如果某个 App 需要放行，只针对它的目标主机和端口单独放行，不要放开整个端口。
- **默认开启的额外功能**：「空降助手」会用视频 ID 查询第三方服务器 `bsbsb.top`，获取可跳过的片段；弹幕请求要等这次查询完成才返回，最多等 3 秒。「优化评论区加载」会在请求阶段接管详情页和评论区的请求。两者都可以在 Loon 的插件页关闭。第一次核对详情页广告时，建议先关掉空降助手，减少干扰。
- **显示效果**：插件会把「我的」页显示为大会员样式，只改显示，不获得权益。

不要与其他 B 站改写（包括 Biliverse）同时启用。

插件图标只用小 PNG（对应 App 的 App Store 图标，几 KB），不用 GIF：上游的 560 KB 动图图标曾让插件页一直转圈，换图标后真机确认恢复。仓库托管插件的图标由 `scripts/build_loon_plugins.py` 统一设置，测试拒绝非 PNG。

当前其他插件以 `bootstrap.example.conf` 的入口和默认值为准；设备已验收的启用状态独立保留。

| 内容 | 当前实现 |
|---|---|
| 高德、YouTube、知乎、微信外链 | 可莉原生插件；保留 Loon 自身更合适的实现，不与额外高德页面净化并用 |
| 微博、闲鱼、小红书 | 仓库冻结的 fmz200 来源；小红书共享仓库脚本，首页去视频笔记与视频频道 |
| 网易邮箱、大麦、航旅、Safari | `NeteaseMail.plugin`、`DaMai.plugin`、`Umetrip.plugin`、`QSearch.plugin`；航旅共享可读二进制脚本，Safari 要求 DuckDuckGo |
| 小宇宙及其他 fmz200 按 App 补充 | 托管原生插件，小 PNG 图标；小宇宙保留 AI 总结、正常搜索、分类和推荐 |
| 阿里系 amdc | `AlibabaAmdc.plugin` 独立处理；闲鱼、大麦不再附带重复 amdc |
| 开屏补充 | `StartupSupplement.plugin`：神州、滴滴、一嗨、1688、拼多多、淘宝、得物；航旅 startup/discardrp 由航旅插件处理 |
| 微信读书、节点检测 | 固定来源的读书精简；可莉节点检测保留手动诊断，不提供旧定时通知 |

每个 App 只启用一个专用入口。专用插件与 blackmatrix7 合集可能重叠；Loon 同一阶段的匹配规则可全部执行，以真机日志核对效果。可莉资源巡检使用完整 iOS Loon UA，短 UA 曾返回 403。

旧转换插件、高德页面净化、豆瓣网页和神机重定向保留路径供兼容回滚，当前模板不加载；神机重定向的 Google 跳转已有安全重定向覆盖。不要把旧新插件同时开启。

## 后续更新

`[Remote Rule]` 引用 `loon/rules/*.list`（由 `build_rules.py` 生成）和 `loon/rules/geoip_cn.list`。`GEOIP,CN` 放在远程列表的最后，而不是本地 `[Rule]`：Loon 的本地规则优先于订阅规则，放在本地会抢先于 Privacy 等列表里的国内 IP 规则。规则内容按 Loon 的资源刷新机制更新；策略组和资源行的变更需要同步到本地 `bootstrap.conf`，同步边界与 QX 相同。

已知效果差异、微信小程序开屏残留及一嗨素材限制见 [QX 说明](../quantumultx/README.md#兼容与已知限制)。模板默认关闭的补充项须按设备验收启用，不能把通用去广告的域名覆盖等同于实际开屏去除。

2026-10-07 航旅安全修正版使用 `umetrip-safe.js`：无改动直接透传；有清理需求但含不安全整数时保留原响应并记录原因，可能保留广告。新版本待真机验收，原 `umetrip.js` 保留回滚。知乎仍使用既有可莉插件，本轮不替换。
