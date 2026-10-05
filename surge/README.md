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

Surge 没有像 Loon `[Plugin]` 那样在配置里列出模块的段落，模块要在 App 里按 URL 安装（或把文件放进 iCloud Drive/Surge 作为本地模块），启用状态按设备保存、不随 iCloud 同步。Loon 中启用的插件在仓库里按 App 拆成单独的模块，放在 `surge/modules/rewrite/`，都归在 proxy-config 分类下，装好后逐个按需启用。按 URL 安装的模块随仓库自动更新；本地副本不会。

原来的合并模块 `surge/modules/rewrite.sgmodule`（「去广告与增强合集」）在设备迁移到拆分模块后，于 2026-10-05 删除；需要时可以从提交 `17c7b8d` 取回。

| 内容 | 来源 |
|---|---|
| 哔哩哔哩（空降助手已关闭）、YouTube、blackmatrix7 去广告与安全重定向、Siri（iRingo）、Spotify | 各作者的 Surge 原生模块，固定到提交或发布标签 |
| 微博、闲鱼、豆瓣网页、Safari 超级搜索、神机重定向 | 墨鱼 QX 原版没有 Surge 版，冻结在 `surge/modules/converted/`：Script-Hub 转换，脚本固定到 Loon 版审核过的提交，补回 Script-Hub 丢掉的 jq 改写，修正 sg 商店地区，微博脚本按序编号 |
| 小红书、知乎 | fmz200 的公开 QX 规则（Kelee 小红书插件的合著者），同样冻结转换，脚本固定到提交 |
| 高德地图、微信外链 | 墨鱼的 QX 原版（QX 端在用的版本），同样冻结转换 |
| 网易邮箱大师、小宇宙、大麦、航旅纵横（2026-10-05 新增） | 墨鱼的 QX 原版，同样冻结转换。大麦、航旅纵横在 ddgksf2013.top 上，没有提交可固定，记录抓取日期和哈希；航旅纵横原版引用的脚本地址对所有客户端都返回网页，仓库托管了改正地址的 `quantumultx/rewrite/UmetripAds.conf` 和脚本副本 `quantumultx/scripts/umetrip.ads.js`。上游脚本由 Protobuf 和 JSON 两段拼成，JSON 那段从未被调用；副本只改了入口，按内容选择路径，`$done` 只调用一次，由 `tests/test_umetrip_script.py` 回归（处理二进制响应体，加 `binary-body-mode=1`） |
| 微信读书精简（2026-10-05 新增） | Maasea 的 Surge 原生模块，模块和脚本都固定到提交（脚本由 `SOURCES` 的 `pins` 在生成时替换）；作用是去除小红点、小圈子提示和评论数等，不是去广告 |
| 微信公众号、美团外卖、虎扑、米家、猫眼、乐刻、豆瓣 App、中国移动（2026-10-05 新增） | fmz200/wool_scripts 按 App 拆分的原生模块，固定到提交 `5d5f63f`，都没有脚本（测试锁定提交和无脚本）。只接入 Surge 和 Loon，QX 本阶段不加。没有采用 fmz200「美团」（整段拒绝 `d.meituan.net` 等后缀）|

注意：Surge 会依次执行所有命中的 Body Rewrite（QX 只执行第一条），闲鱼的通用搜索 jq 规则因此也作用于搜索底纹和发现页，效果与 Loon 版相同，2026-10 真机检查闲鱼搜索页正常。

Loon 上小红书、高德、知乎、微信外链用的是 Kelee 的插件，它们是 Loon 专有语法、脚本只对 Loon 提供，所以 Surge 改用上表的公开来源，规则和效果可能与 Kelee 版不同；节点检测是 Loon 独有功能，没有移植。Loon 中默认关闭的 BoxJS 也没有移植。

冻结转换的共同修改：脚本名编号保证唯一；模块里的 IP 规则加 `no-resolve`（否则排在规则最前面的 IP 规则会让每个请求先在本地解析）；QX 的 `response-body` 由 Script-Hub 转成它的 `replace-body.js`，固定到 Script-Hub `6b4fb62`。

生成：`python3 scripts/build_surge_modules.py`，每个来源生成一个模块（参数在生成时写入，例如空降助手 `#`、YouTube 与 Siri 用作者默认值）；`--check` 联网重新生成并比对，CI 也会运行。kokoryh 等模块引用的脚本仍跟随各自上游（与 Loon 相同）；`SOURCES` 中写了 `pins` 的来源例外，生成时把脚本换成审核过的提交（目前是微信读书）。Surge 只执行第一个匹配的 http-response 脚本和第一个匹配的 header 模式 URL Rewrite，所以 `SOURCES` 保持 Loon 的插件顺序，转换的 QX 规则保持 QX 原顺序。

多个模块之间谁先执行，Surge 文档没有说明，所以 Loon 里「专用插件排在 blackmatrix7 合集之前」的先后在 Surge 上没有保证。2026-10-05 的核对结果：

- 按主机名看，不同模块在只执行第一条匹配的段（Script、Map Local、URL Rewrite、Header Rewrite）里没有冲突（amdc 脚本除外，两边结果相同，见下）。共享的 MitM 主机名上，blackmatrix7 通用去广告只有 URL Rewrite；它和高德、微博的 URL Rewrite 同时命中时都是 reject，`google.cn` 的跳转在神机重定向和安全重定向里写法相同。共享清单固定在 `tests/test_surge_config.py` 的 `SHARED_MITM`，来源更新后清单变化，测试会失败，要重新核对。
- **例外**：通用去广告里有一批不限主机的 reject 模式，如 `(?i)\badvertising`、`(?i)\badvertisement`、`(?i)\bsplash_screen`、`(?i)\b\/ad\/`，以及按 IP 地址主机写的模式。被解密的请求或纯 HTTP 请求，URL 里带这些词时，会同时命中通用去广告和别的模块的跳转，结果由模块顺序决定。已知的情况：DuckDuckGo 搜索词含 advertising 等词时，Safari 超级搜索的跳转（被拒绝时搜索失败）；知乎外链 `link.zhihu.com/?target=` 的目标地址含 `/ad/` 时；`google.cn`、纯 HTTP 站点的路径带这些词时，与神机重定向 / 安全重定向的跳转。只涉及带这些词的请求，接受这个差异；`SHARED_MITM` 查不出这类重叠。
- Rule 段：B 站的 `DEST-PORT` 拒绝和知乎的 `USER-AGENT,"AVOS*"` 拒绝不限主机，理论上可以与高德、微博的 DIRECT 域名规则同时命中，实际不会出现。
- 高德去广告、闲鱼去广告、大麦去广告都对纯 HTTP 的 `amdc.m.taobao.com/amdc/mobileDispatch` 执行同一个 `amdc.js`（同一提交、同一参数），无论谁先执行，结果都一样。
- 2026-10-05 新增的 8 个 fmz200 模块（微信公众号到中国移动）都与通用去广告共享 MitM 主机名（见 `SHARED_MITM`）。通用去广告在这些主机上只有 URL Rewrite；与新模块的 URL Rewrite 同时命中时两边都是 reject，并且它在请求阶段先执行，会先于新模块的 Map Local 拒绝一部分请求。已确认的例子：虎扑 `search/hotkey`、`interfaceAd/getOther`、`hoopchina` 帖子图；美团 `linglong` 素材、外卖 `startpicture`；猫眼 `adAdmin` 图；豆瓣 `common_ads`；乐刻广告接口；微信 `cps_product_info`；中国移动的广告列表。这不是完整清单，最终命中以请求记录为准。依赖：美团外卖开屏（`wmapi.meituan.com`）只由通用去广告解密；豆瓣的横幅（`frodo.douban.com`）和图片广告（`img*.doubanio.com`）两条 HTTPS 规则没有任何模块解密，本次不覆盖。
- 2026-10-05 新增的网易邮箱大师、大麦、航旅纵横与通用去广告共享 MitM 主机名，但通用去广告在这些主机上只有 URL Rewrite，新模块用的是 Script、Map Local 和 Body Rewrite，不在同一段。通用去广告的拒绝在请求阶段先执行，所以航旅纵横的 `startup` 接口、网易邮箱的 `/mmad/`、大麦的弹窗接口仍由它拒绝，新模块对这几处不起作用。
- 通用去广告还带 `[General] force-http-engine-hosts`（其中有 `weibointl.api.weibo.cn`）。只启用微博去广告、不启用通用去广告时没有这一项，对微博国际版有没有影响未核实。

在「模块 → 安装新模块」中填写以下 URL（复制时注意不要带上空格）：

| 模块 | URL | 默认 |
|---|---|---|
| 在家直连 + 拦截 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/home-direct.sgmodule` | 启用，参数 `HOME_SSID` 填家里 Wi-Fi |
| 在家直连（关闭 AdRules / Privacy 拦截） | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/home-direct-noblock.sgmodule` | 不启用；与上一个**二选一** |
| 哔哩哔哩增强 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/bilibili.sgmodule` | 按需启用 |
| YouTube 增强 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/youtube.sgmodule` | 按需启用 |
| 小红书去广告 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/xiaohongshu.sgmodule` | 按需启用 |
| 高德地图去广告 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/amap.sgmodule` | 按需启用 |
| 知乎去广告 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/zhihu.sgmodule` | 按需启用 |
| 微信外链解锁 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/wechat.sgmodule` | 按需启用 |
| 通用去广告（blackmatrix7） | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/advertising.sgmodule` | 按需启用 |
| Siri 增强 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/siri.sgmodule` | 按需启用 |
| 安全重定向（blackmatrix7） | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/safe-redirect.sgmodule` | 按需启用 |
| Spotify 增强 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/spotify.sgmodule` | 按需启用 |
| 微博去广告 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/weibo.sgmodule` | 按需启用 |
| 闲鱼去广告 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/goofish.sgmodule` | 按需启用 |
| 豆瓣网页增强 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/douban.sgmodule` | 按需启用 |
| Safari 超级搜索 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/q-search.sgmodule` | 按需启用 |
| 神机重定向 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/general.sgmodule` | 按需启用 |
| 网易邮箱大师去广告 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/neteasemail.sgmodule` | 按需启用 |
| 小宇宙去广告 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/xiaoyuzhou.sgmodule` | 按需启用 |
| 大麦去广告 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/damai.sgmodule` | 按需启用 |
| 航旅纵横去广告 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/umetrip.sgmodule` | 按需启用 |
| 微信读书精简 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/weread.sgmodule` | 按需启用 |
| 微信公众号去广告 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/wechat-mp.sgmodule` | 按需启用 |
| 美团去广告 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/meituan.sgmodule` | 按需启用 |
| 虎扑去广告 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/hupu.sgmodule` | 按需启用 |
| 米家去广告 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/mijia.sgmodule` | 按需启用 |
| 猫眼去广告 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/maoyan.sgmodule` | 按需启用 |
| 乐刻去广告 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/leke.sgmodule` | 按需启用 |
| 豆瓣 App 去广告 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/douban-app.sgmodule` | 按需启用 |
| 中国移动去广告 | `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/surge/modules/rewrite/chinamobile.sgmodule` | 按需启用 |

除两个家庭模块外，上表的模块都需要 MitM。前 15 个对应 Loon 中已验收、默认启用的插件；2026-10-05 新增的 5 个（网易邮箱大师到微信读书精简）在 QX、Loon 中默认关闭，之后的 8 个（微信公众号到中国移动）只有 Surge 和 Loon，Loon 中默认关闭；真机逐个验收前都不要当作已验证。12306 和中国电信的模块在 2026-10-05 真机验收后移除：`ad.12306.cn` 已被三端的 AdRules 拒绝（Surge 在家庭模块里预匹配拒绝），12306 的脚本从未运行；fmz200「中国电信」处理的是天翼云盘等其他电信 App 的主机，中国电信 App 实际访问的 `appgo*.189.cn` 一条都不匹配。不用的 App 可以不装，或在模块列表里关掉。

**模块顺序**：「在家直连 + 拦截」要排在所有含 REJECT 规则的模块**之后**，目前是哔哩哔哩增强、小红书去广告、知乎去广告、微博去广告、闲鱼去广告，以及 2026-10-05 新增的虎扑、米家、豆瓣 App、中国移动（它们的模块说明里都写了这一点）。启用新模块、调整顺序或重载配置后都要复核；无法排到家庭模块之前时，关闭这个新模块，保持家庭切换与拦截不变。UDP 没有预匹配阶段，只按主规则顺序匹配；如果家庭模块排在前面，家里发往 B 站 P2P 端口的 UDP 会先命中 SUBNET 而直连。在家播放 B 站视频，到请求记录里筛选目标端口 4480 / 4483 / 8082 / 9102，应全部为 REJECT；出现 DIRECT 就调整模块顺序。也可以用 `python3 scripts/netdiag.py get surge /v1/rules` 查看实际生效的规则顺序：上述模块的规则应排在 `SUBNET,SSID:` 之前。

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
pin surge/proxy-config.conf "$T/proxy-config.dconf"
pin surge/modules/home-direct.sgmodule "$T/home-direct.sgmodule"
pin surge/modules/home-direct-noblock.sgmodule "$T/home-direct-noblock.sgmodule"
if grep -n 'wenbingkun/proxy-config/main/' "$T"/*; then echo "unpinned repo URL" >&2; exit 1; fi
find "$T" -type f -exec chmod 600 {} +
```

把三个文件复制到 iCloud Drive/Surge，`surge/modules/rewrite/` 下要用的模块也原样复制过去作为本地模块（它们不含本仓库的 main 链接，不用固定）；`.sgmodule` 放在配置目录里，就会作为本地模块出现。试验副本存为 `proxy-config.dconf`（分离配置段文件，不会出现在配置列表里），`bootstrap.conf` 的 4 处 `#!include proxy-config.conf` 改为 `proxy-config.dconf`。合并后改回按 URL 安装 `proxy-config.conf` 和模块（两个家庭模块与要用的拆分模块），把 include 改回，并删除本地副本。

## 回滚

关闭 Surge VPN，打开 Loon 或 QX，它们的配置不受影响。Surge 的本地文件都是新增的，不会覆盖其他客户端的文件。
