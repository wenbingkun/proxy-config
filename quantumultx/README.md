# Quantumult X（iPhone / iPad）

> 返回 [总览](../README.md)。下文仓库命令均在仓库根目录执行。

Quantumult X 采用 **本地 bootstrap + 远程 snippet** 架构，保证 MitM 证书等本地私密信息永远不会被远程配置覆盖。

## 第一次配置（仅需一次）

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
passphrase = # 在设备本地填写 MitM 密码短语
p12 = # 在设备本地填写 p12 证书（base64）
hostname =   需要解密的域名列表（如 *.example.com）
```

> MitM 信息可以从 Quantumult X 的"MitM"设置页面导出，或者生成新证书后复制过来。

再把 `[policy]` 末尾 16 行 `ssid=… · 自动, …, HOME_SSID:DIRECT` 中的 `HOME_SSID` 全部换成家里 Wi-Fi 的名称（见下文[家庭 / 外出自动切换](#家庭--外出自动切换)）。2.4G 与 5G 名称不同时，每行末尾各追加一项 `, <SSID>:DIRECT`。同时替换 `[dns]` 全局 `doh-server` 行的 `excluded_ssids=HOME_SSID`；多个家庭 SSID 时，排除列表须包含与上述策略相同的全部家庭网络名称，并逐个网络验证 DNS 切换。以后新增或改名时，两处同步更新。家庭 SSID 只写在设备本地，不提交到仓库。

**第 3 步：导入 Quantumult X**

在 Quantumult X 中，进入 **「配置文件」→「从文件导入」**，选择刚才编辑好的 `bootstrap.conf`。

导入完成后，bootstrap 中已预配置的远程资源（规则、重写、脚本等）会在 QX 首次刷新时自动拉取。

## 当前去广告入口

按效果选择各端实现，不要求三端来源一致。同一 App 只启用一个专用入口；阿里系 amdc 是独立的跨 App 调度处理。模板的 `enabled` 是新安装默认值，设备按已验收选择启用，不能据模板推断设备状态。

| 内容 | 当前 QX 入口 |
|---|---|
| 高德 | `rewrite/Amap.snippet`，墨鱼固定规则与仓库页面清理合并；含分流，在重写和分流各加载一次，均开启解析器 |
| 微博、闲鱼 | `rewrite/fmz200-Weibo.snippet`、`rewrite/fmz200-XianYu.snippet`；微博含分流，需两处加载；闲鱼只有重写，加载到分流会报空资源错误 |
| 网易邮箱、大麦、航旅纵横 | `rewrite/NeteaseMail.conf`、`rewrite/DaMai.conf`、`rewrite/Umetrip.conf` |
| Safari、YouTube、微信外链 | `rewrite/QSearch.conf`、`rewrite/YouTube.conf`、`rewrite/WeChatUnblock.conf` |
| 小红书、知乎、小宇宙 | 仓库 `rewrite/fmz200-*.snippet`；小宇宙保留 AI 总结、正常搜索和推荐，小红书首页去视频笔记与视频频道 |
| 阿里系 amdc | `rewrite/AlibabaAmdc.conf`，其他专用入口不再带 amdc |
| 共用开屏补充 | `rewrite/StartupSupplement.conf`：神州、滴滴、一嗨、1688、拼多多、淘宝、得物；航旅 startup/discardrp 归航旅入口 |

KOP 资源解析器及四个辅助脚本（IP_API、地理位置、节点信息、流媒体查询）均引用原站完整提交，版本以模板为准。升级前重新核验字节及对应功能，辅助任务的时间、开关保持现有设置；固定脚本不冻结查询服务的响应。

脚本固定版本及来源记录在资源文件头和生成器中。fmz200 的混合资源含分流时需在 `[rewrite_remote]`、`[filter_remote]` 各引用一次，并开启 `opt-parser=true`；只有重写的资源不加分流。微信公众号、美团外卖、虎扑、米家、猫眼、乐刻、豆瓣 App、中国移动为按需启用的补充。

知乎重写 URL 的 `#regout=^(USER-AGENT|IP6-CIDR)%2C&ntf=0` 排除会被解析为 `url reject` 策略的混合分流行；独立分流 URL 不加此参数。保留模板参数，避免出现多余策略组。

Safari 超级搜索要求默认搜索引擎为 DuckDuckGo；App Store 地区页面跳转不改变 Apple 账号地区，跨区下载仍需切换账号或地区。

哔哩哔哩：使用仓库内冻结的旧版规则 `quantumultx/rewrite/bilibili_ad.conf`（deezertidal 转载的墨鱼 `biliad.conf`，最后更新 2023-06-08），其失效的 `bilibili_json.js` 已替换为仓库内 `quantumultx/scripts/bilibili_json-safe.js`（以墨鱼 GitHub 删除前的最后一版为基线，2026-10-07 修正单次完成；旧脚本保留回滚）。上游均已停更，仓库仅做明确的安全与健壮性修正；它解密 `app.bilibili.com` 等 B 站接口主机，有意不解密 `grpc.biliapi.net`；只要解密 `app.bilibili.com`，Quantumult X 下历史记录与评论区加载就可能偏慢。视频播放页的广告（播放器下方的推广卡等）来自 `grpc.biliapi.net` 上的 `bilibili.app.viewunite` 接口，冻结版处理不到，真机已确认；在 QX 上接受这一点：要去掉它，需要解密 `grpc.biliapi.net` 并另加处理该接口的规则，这会让加载再次变慢。不要与墨鱼自建站 `BiliBiliAds.conf` 或 Biliverse ADBlock 同时启用。

冻结版的规则、`bilibili_json-safe.js` 和两个外部脚本（app2smile `bilibili-proto.js`、yjqiang `bilibili_dynamic.js`，链接固定到 2026-09-30 的提交）都不会随上游变化。它自带 hostname，使用 `opt-parser=true` 与原版一致，不需要 `#outhn=*`，也不依赖「仓库自定义重写」。脚本会把「我的」页会员字段改成大会员样式，这只影响客户端显示，不会获得服务端会员权益。冻结版中动态相关的两条规则原文都含 `DynAll`，其中 app2smile 那条的正则还包含 `app.bilibili.com` 上的旧接口 `view.v1.View/View`（如上，当前视频页广告不经过它），若要排除它们，把 bootstrap 中的链接写成 `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/quantumultx/rewrite/bilibili_ad.conf#out=DynAll`（链接原本没有 `#` 参数，首个参数用 `#`，之后的参数才用 `&` 连接），这会把 app2smile 那条整行去掉；此做法未经真机验证。

## 家庭 / 外出自动切换

与 Loon 相同（见 [Loon 说明](../loon/README.md#家庭--外出自动切换)）：不使用 `running_mode_trigger` 或 `ssid_suspended_list`，运行模式在任何网络下都保持「规则分流」。策略页上方的 32 个组与路由器、Loon 同名同序，在这些组里选择节点。规则实际引用的是排在最后的 16 个「· 自动」ssid 策略（如 `🤖 人工智能 · 自动`、`🐟 兜底分流 · 自动`、`🇭🇰 香港节点 · 自动`）：

- 连上家里 Wi-Fi：走 DIRECT，交给路由器上的 ShellCrash 分流；
- 其他 Wi-Fi 与蜂窝：走同名基础组，即原来在 QX 里选好的节点。

「· 自动」策略里没有需要选择的内容。`🛡️ 安全防护` 不包装，在家仍然 REJECT；重写、脚本和 MitM 在家同样生效。`[dns]` 中的全局 `doh-server` 带 `excluded_ssids=HOME_SSID`：在家跳过 DoH，查询退回普通 `server`，由路由器把 53 端口劫持给 Mihomo 解析（依赖 ShellCrash 当前的 DNS 劫持）；外出照常使用 DoH。分域名的 DNS 设置，以及本地添加的机场节点专用 DoH，都不受影响。

真机验收（同步配置后各做一次）：

1. 家里 Wi-Fi：打开一个会走代理的网站（如 Google），「请求记录」里应显示 `🐟 兜底分流 · 自动` 或对应服务的「· 自动」策略、出站为 DIRECT；网站仍能打开（由路由器代理）。
2. 蜂窝或其他 Wi-Fi：同一网站的出站应为同名基础组所选的节点。
3. 两种网络下各打开一个已知广告域名或 B 站首页：广告请求仍为 REJECT，B 站去广告仍生效。

## 后续更新（已引用规则内容自动刷新）

仓库中的规则文件（`quantumultx/filter/repo.snippet`）已在 bootstrap 中配置为远程资源：

```ini
[filter_remote]
https://raw.githubusercontent.com/wenbingkun/proxy-config/main/quantumultx/filter/repo.snippet, tag=仓库自定义规则, update-interval=86400, enabled=true
```

修改 `rules/` 下的规则后，按[日常维护](../rules/README.md#日常维护)生成 `filter/repo.snippet` 并合入 `main`，QX 会在后续按配置刷新（示例间隔 24 小时）成功加载时取得新规则；也可手动触发「更新资源」。这只更新 snippet 的内容；本地 `bootstrap.conf` 的策略组和资源行不会随之改变，见[日常维护](../rules/README.md#日常维护)末尾的同步边界说明。

## 兼容与已知限制

旧 `AmapPageCleanup.conf`、`StartUpGaps.conf`、`UmetripAds.conf` 及其他已替换资源保留公开路径供兼容回滚，不与当前同 App 入口同时启用。高德不再单独加载页面净化；航旅旧新入口也不并用。

高德合并版本在 Surge 验收通过，用户随后反馈 QX/Loon 实测正常；模拟测试只证明接口处理和业务字段保护，不能替代后续版本的真机复测。微博国际版当前效果差异已接受。微信小程序（问卷管家）开屏仍有残留；一嗨只匹配四张已验证投放图片，新素材需要补充。`ddgksf2013.top` 迁移维护期间不可达，运行资源使用冻结副本；未取得的上游新版不视为已同步。

2026-10-07 知乎托管规则改用仓库脚本保留外链原 HTTP(S) 协议、解码目标一次；非 HTTP(S)、重复目标或不合法目标返回 400。搜索推荐按 JSON 字段处理，避免破坏相邻字段。航旅 `umetrip-safe.js` 无改动时透传；有改动但含不安全整数时保留原响应并记录原因，可能保留广告。以上新修正版待真机验收，旧脚本路径保留。
