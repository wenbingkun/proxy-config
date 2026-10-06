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
passphrase = 你的MitM密码短语
p12 =        你的p12证书（base64）
hostname =   需要解密的域名列表（如 *.example.com）
```

> MitM 信息可以从 Quantumult X 的"MitM"设置页面导出，或者生成新证书后复制过来。

再把 `[policy]` 末尾 16 行 `ssid=… · 自动, …, HOME_SSID:DIRECT` 中的 `HOME_SSID` 全部换成家里 Wi-Fi 的名称（见下文[家庭 / 外出自动切换](#家庭--外出自动切换)）。2.4G 与 5G 名称不同时，每行末尾各追加一项 `, <SSID>:DIRECT`。同时替换 `[dns]` 全局 `doh-server` 行的 `excluded_ssids=HOME_SSID`；多个家庭 SSID 时，排除列表须包含与上述策略相同的全部家庭网络名称，并逐个网络验证 DNS 切换。以后新增或改名时，两处同步更新。家庭 SSID 只写在设备本地，不提交到仓库。

**第 3 步：导入 Quantumult X**

在 Quantumult X 中，进入 **「配置文件」→「从文件导入」**，选择刚才编辑好的 `bootstrap.conf`。

导入完成后，bootstrap 中已预配置的远程资源（规则、重写、脚本等）会在 QX 首次刷新时自动拉取。

2026-10-06 初轮替换时，三端退出了墨鱼（ddgksf2013）资源（QX 哔哩哔哩冻结版除外，它已在仓库内）；后续按效果优先回取审核过的固定来源，当前高德入口见下文。初轮替换的其他改动：微博改用 fmz200 `weibo` 的冻结副本（脚本固定到提交：fmz200 `5d5f63f`、zmqcherish `1d9f51b`、Keywos `a06d921`；去掉解锁会员图标那条；Loon、Surge 补上 QX 版才有的微博国际版开屏 `get_coopen_ads` 与 `api.touch-moblie.com`）；闲鱼改用 fmz200 `XianYu` 冻结副本（ishowshu 脚本固定 `d38e228`，去掉 amdc 拦截，补 `idle.ad.expose`）；网易邮箱大师、大麦为仓库自写规则（fmz200 QX 版规则加补充；fmz200 网易邮箱的 Loon、Surge 文件内容实为网易云音乐规则，未采用）；Safari 超级搜索为仓库自写精简版，前缀 yd、trc、tre、gh、yt、bli、wk、zh、db、bd 与 App Store 地区页面跳转 cn / us / hk。新条目验收前默认关闭；旧的托管文件在兼容期内保留不改。 QX 还去掉了墨鱼去开屏 2.0 与微信小程序去广告（保留 blackmatrix7 去广告合集，不能保证完全覆盖）；当前航旅纵横 startup / discardrp 拒绝规则已归入唯一航旅入口 `quantumultx/rewrite/Umetrip.conf`；神州租车投放位及其他仅去开屏的补充统一归入 `quantumultx/rewrite/StartupSupplement.conf`。`StartUpGaps.conf` 已退出模板，仅保留旧公开文件用于兼容回滚，不与上述入口同时启用。

2026-10-06 三端真机验收：网易邮箱、大麦正常，闲鱼小程序与旧版一致，Safari 跳转正常；微博国际版在 Surge 比旧版多一些内容，Loon/QX 的去广告效果弱于 Surge，差异已接受。App Store 地区链接不会修改 Apple 账号地区，跨区下载仍需切换账号或账号地区；QX 微信小程序开屏仍有残留。新条目保持按需开启，旧路径继续保留作为回滚方案。

哔哩哔哩：使用仓库内冻结的旧版规则 `quantumultx/rewrite/bilibili_ad.conf`（deezertidal 转载的墨鱼 `biliad.conf`，最后更新 2023-06-08），其失效的 `bilibili_json.js` 已替换为仓库内 `quantumultx/scripts/bilibili_json.js`（墨鱼 GitHub 删除前的最后一版，2025-03-31）。上游均已停更，此版本不会再更新；它解密 `app.bilibili.com` 等 B 站接口主机，有意不解密 `grpc.biliapi.net`；只要解密 `app.bilibili.com`，Quantumult X 下历史记录与评论区加载就可能偏慢。视频播放页的广告（播放器下方的推广卡等）来自 `grpc.biliapi.net` 上的 `bilibili.app.viewunite` 接口，冻结版处理不到，真机已确认；在 QX 上接受这一点：要去掉它，需要解密 `grpc.biliapi.net` 并另加处理该接口的规则，这会让加载再次变慢。不要与墨鱼自建站 `BiliBiliAds.conf` 或 Biliverse ADBlock 同时启用。

冻结版的规则、`bilibili_json.js` 和两个外部脚本（app2smile `bilibili-proto.js`、yjqiang `bilibili_dynamic.js`，链接固定到 2026-09-30 的提交）都不会随上游变化。它自带 hostname，使用 `opt-parser=true` 与原版一致，不需要 `#outhn=*`，也不依赖「仓库自定义重写」。脚本会把「我的」页会员字段改成大会员样式，这只影响客户端显示，不会获得服务端会员权益。冻结版中动态相关的两条规则原文都含 `DynAll`，其中 app2smile 那条的正则还包含 `app.bilibili.com` 上的旧接口 `view.v1.View/View`（如上，当前视频页广告不经过它），若要排除它们，把 bootstrap 中的链接写成 `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/quantumultx/rewrite/bilibili_ad.conf#out=DynAll`（链接原本没有 `#` 参数，首个参数用 `#`，之后的参数才用 `&` 连接），这会把 app2smile 那条整行去掉；此做法未经真机验证。

航旅纵横改用仓库维护的可读 JSON / Protobuf 实现 `quantumultx/scripts/umetrip.js`，三端使用同一脚本；QX 入口为 `quantumultx/rewrite/Umetrip.conf`，模板仍默认关闭，需真机验收后按需启用。旧 `UmetripAds.conf`、`umetrip.ads.js` 保留原内容作为兼容与回滚入口。网易邮箱大师、大麦已改用仓库自写规则。微信读书精简仍为 Maasea 的 Surge 模块，`quantumultx/rewrite/WeRead.conf` 按原模块手写，脚本固定到提交。12306 专用重写已移除：Surge 真机记录显示广告域名被 AdRules 预匹配拒绝，脚本未执行；QX 的 AdRules 也包含该域名。

同日新增 fmz200 按 App 拆分的 8 个模块（微信公众号、美团外卖、虎扑、米家、猫眼、乐刻、豆瓣 App、中国移动），与 Surge、Loon 同一提交 `5d5f63f`，默认关闭。它们的 `.snippet` 同时含重写和分流，所以同一地址加两次：`[rewrite_remote]` 8 行；含分流规则的虎扑、米家、豆瓣 App、中国移动另在 `[filter_remote]` 加 4 行，`force-policy=🛡️ 安全防护`，放在 AdRules 之后。两处都必须 `opt-parser=true`，由 `resource_parser_url`（KOP-XIAO 解析器，跟随其 master）分别取出重写和分流。2026-10-05 真机实验：虎扑在重写里读出 8 条、在分流里读出 5 条，没有混入另一类。解析器不可用时这 12 项读不出规则。重写排在 bm7、去开屏 2.0、小程序去广告之后，与它们重叠的请求（例如微信公众号文章广告、乐刻和豆瓣的广告接口）由前面的资源先处理。

同日替换掉四项墨鱼资源，新条目同样默认关闭、真机验收后再开，原位置不变（QX 只执行第一条匹配的重写）：知乎改用仓库托管的 fmz200 冻结副本 `quantumultx/rewrite/fmz200-Zhihu.snippet`（只把 11 处脚本地址从 `main` 固定到 `5d5f63f`、图标换成 App Store 小 PNG；同时含分流，`[rewrite_remote]` 与 `[filter_remote]` 各一行，都 `opt-parser=true`）；微信外链改用 `quantumultx/rewrite/WeChatUnblock.conf`，直接加载 zZPiglet 原版脚本（固定到 `0a70fbe`，墨鱼版是它的重新打包）；YouTube 改用 `quantumultx/rewrite/YouTube.conf`，按 Maasea 的 Surge 模块手写，脚本固定到 `65075cd`（与 Surge 模块同一提交），只解密 `youtubei.googleapis.com`；小宇宙改用托管的 `fmz200-XiaoYuZhou.snippet`（无脚本）：移除 AI 总结拦截，搜索和分类只拦截旧版已针对的推广/提示接口，首页仅去掉 `DISCOVERY_BANNER`，保留普通推荐。`tests/test_qx_config.py` 锁定这些文件允许加载的脚本。

知乎重写 URL 带 `#regout=^(USER-AGENT|IP6-CIDR)%2C&ntf=0`：排除 KOP 解析器在重写模式下误转为 `url reject` 策略的两条混合分流，避免出现同名自定义策略组。逗号编码为 `%2C`，防止被当作资源参数分隔符；独立的知乎分流 URL 不加该参数，保留原有拦截。

小红书（2026-10-05）：墨鱼 `XiaoHongShuAds.conf` 换成仓库托管的 fmz200 冻结副本 `quantumultx/rewrite/fmz200-Xiaohongshu.snippet`（重写与分流各一行，`opt-parser=true`，验收前默认关闭）。其中 12 处脚本地址指向仓库托管的 `quantumultx/scripts/xiaohongshu.js`：fmz200 `xiaohongshu.js`（`5d5f63f`，GPL-3.0，许可见同目录 `LICENSE.fmz200`）加三处修改——首页推荐去掉视频笔记（条目 `type` 为 `video`，2026-10-05 Surge 采样确认）、首页频道去掉「视频」（`/v6/homefeed/categories` 中 `oid` 以 `homefeed.video` 开头的频道，另加一条规则匹配该接口）、非 JSON 响应原样放行且 `$done` 只调用一次（`tests/test_xiaohongshu_script.py`）。关注页与搜索里的视频不过滤。Loon、Surge 加载同一个脚本。同日验收通过的知乎、微信外链、YouTube、小宇宙改为默认开启。

高德与阿里系 amdc（2026-10-06）：墨鱼高德换成 fmz200 的 `AutoNavi.snippet`（固定 `5d5f63f`，无脚本，重写与分流各一行，验收前默认关闭）。阿里系 App 纯 HTTP `/amdc/mobileDispatch` 的处理改由仓库自写的 `quantumultx/rewrite/AlibabaAmdc.conf`（脚本 `quantumultx/scripts/amdc.js`，与墨鱼版同一 UA 清单，`tests/test_amdc_script.py`）负责，放在墨鱼去开屏 2.0 之前：QX 只执行第一条匹配的重写，所以它先于墨鱼去开屏、闲鱼、大麦中仍存在的同类规则执行。

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

## 2026-10-06 页面净化与开屏补充

高德页面净化独立保留个人页明确推广卡片、首页「关注 / 推荐 / 附近」社交栏目和帖子、打车推广列表清理；保留地图、定位、搜索、路线与未知组件。首页顶部商业卡片尚未覆盖。微博发现页的空「热门视频」区块及排序入口已合入原微博规则，保留原广告 banner 过滤和正常发现内容。

开屏补充处理神州精确营销接口、滴滴 `static/ad_oss/` 素材，以及一嗨四张已核验广告图片。**一嗨更新素材后需要维护**，不拦截共享图片目录。微信小程序开屏仍未解决。Surge 已真机验收；QX/Loon 仅完成对应规则及模拟检查，新增项模板默认关闭，需逐项验收。

历史版本新增的 `rewrite/AmapPageCleanup.conf` 已退出模板，保留公开兼容路径；`rewrite/StartupSupplement.conf` 继续提供开屏补充。航旅 startup / discardrp 由 `rewrite/Umetrip.conf` 处理；神州、滴滴、一嗨及阿里巴巴（1688）、拼多多、淘宝、得物开屏补充统一由 `rewrite/StartupSupplement.conf` 处理。旧 `StartUpGaps.conf` 仅供兼容回滚，不再作为当前配置入口，也不与航旅或开屏补充并行启用。

高德净化脚本固定到仓库提交 `ced3ace1d4dfa6e6b1301dfb465cb6c5c4056bd8`，避免未合并试验读取不存在的 main 文件。以后更新脚本时先提交源码，再更新三端容器的脚本 SHA 并重新生成 Surge 模块。

闲鱼 `fmz200-XianYu.snippet` 移除 amdc 后只含重写，不作为分流资源导入；加载到 `[filter_remote]` 会得到空分流并报错。

## 效果优先：高德单入口

高德改为本仓库 `rewrite/Amap.snippet`：墨鱼固定版本规则、可读合并脚本与已验证页面清理放在一个资源里；保留此前 fmz200 的 14 条域名分流和开屏请求拦截，去掉墨鱼的 optimus DIRECT 例外及内置 amdc。混合资源分别在重写、分流加载一次，都用解析器，逻辑上是一个入口。模板默认关闭，需新一轮真机验收；旧 AutoNavi、AmapPageCleanup 不并行启用。

脚本保留现有个人页业务/未知卡片和打车 banner 元数据，不照搬旧版仅留两种个人页卡片的广泛清空。其余旧版首页、热词、附近与消息清理按固定来源回取。顶部商业卡片效果仍待验证，不能以模拟测试断言已经解决。阿里系 amdc 继续独立处理。
