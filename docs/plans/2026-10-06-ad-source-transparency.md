# 去广告来源透明化方案（2026-10-06）

状态：**待 Codex 审核**。本文件只是方案，审核通过前不改任何配置。由云端会话撰写；云端访问不了 `ddgksf2013.top` 与 `kelee.one`（代理返回 403），凡需要抓取这两处内容的步骤都标为“本机执行”。

## 1. 目标与原则（用户 2026-10-06 确认）

- 替换墨鱼的起因：部分规则不在 GitHub 公开，放在个人站点上，无法核对、可能被静默修改。
- 原则：三端**不强求统一**。各端优先使用公开的知名仓库；不满足的再由仓库自己补充；**哪个效果好就用哪个**；只要求三端能力相近，允许各端有独特优势。
- 补充约束（本方案提出）：不透明来源不是一律禁止，而是**不得直接加载**；确需使用时，先审核再冻结托管到本仓库（见第 3 节）。

## 2. 事实核查（方案依据）

### 2.1 墨鱼的大部分内容其实在 GitHub 公开

- `ddgksf2013/Rewrite`（规则）与 `ddgksf2013/Scripts`（脚本，579 个提交，最新 `314f610`，2026-10-01）都是公开仓库，有完整提交历史。
- 替换前 QX 模板（`7ed98f4`）加载的 17 项墨鱼资源中：
  - **12 项来自 GitHub**（经 jsDelivr `@master`）：Applet、YoutubeAds、WeiboAds、AmapAds、GoofishAds、UnblockURLinWeChat、ForOwnUse、General、Q-Search、Douban、NeteaseMailAds、XiaoYuZhouAds，另有 `ddgksf2013/Filter` 的 AppleIntelligence.list。这些规则调用的脚本也都在 `ddgksf2013/Scripts`（YoutubeAds 调用的是 Maasea 的脚本）。
  - **4 项来自个人站点 `ddgksf2013.top`**：去开屏 2.0（StartUpAds.conf）、XiaoHongShuAds.conf、zhihu.ads.js、DaMaiAds.conf；另有航旅纵横引用的 `umetrip.ads.js`。
- 结论：墨鱼的真正问题分两层：
  - (a) 上述个人站点上的几项完全不透明；
  - (b) GitHub 上的部分以 `@master` 加载，内容可变，但每次变化都有历史可查，**固定到提交后即可审计**。

### 2.2 可莉（Kelee）确实不公开

- `luestr/ProxyResource` 的 README 原文：“本仓库只是索引库，仅对配置模板开源”。插件和脚本只在 `kelee.one` 上提供，且只响应 Loon 的 User-Agent（见 `scripts/check_remote_resources.py`）。
- 当前只有 Loon 加载可莉：YouTube 去广告、高德地图去广告、知乎去广告、微信外部链接解锁、节点检测工具，共 5 项。

### 2.3 当前三端模板的来源（main `8971cf8`）

| 客户端 | 非 GitHub 来源 | 说明 |
|---|---|---|
| QX | 无 | 去广告资源全部来自 GitHub（多数为固定提交或本仓库托管） |
| Loon | `kelee.one` × 5 | 见 2.2 |
| Surge | 无 | 模块由 `scripts/build_surge_modules.py` 从固定来源生成 |

## 3. 信任分级与冻结托管

| 级别 | 来源 | 用法 |
|---|---|---|
| A | 公开 GitHub 仓库的**固定提交**，或本仓库审核后托管的副本 | 可直接使用 |
| B | 公开知名仓库的 `@master` / latest release（如 blackmatrix7） | 可用；其中的**脚本**应尽量固定到提交 |
| C | 非 GitHub 或只对特定 UA 提供的站点（`ddgksf2013.top`、`kelee.one`） | **不得直接加载**；确需使用时按下面的流程冻结托管 |

C 级冻结托管流程（沿用本仓库已有做法，参考 `quantumultx/scripts/umetrip.ads.js` 与 AGENTS.md 的 Loon 约定）：

1. 本机用对应客户端的 UA 抓取，记录 URL、日期、UA、SHA-256。
2. 按第 4 节清单审核，必要时修改，修改写入文件头。
3. 托管到仓库新路径：QX 放 `quantumultx/rewrite/` 和 `quantumultx/scripts/`，Loon 放 `loon/plugins/`，Surge 经 `surge/modules/converted/` 生成。三端引用仓库副本，不再引用原站点。
4. 带脚本的要配回归测试，并模拟各客户端的输入形式（见 5.8 的教训）。
5. 远程资源巡检记录哈希；更新只能经过 PR。

## 4. 脚本安全审核清单

- **外传**：是否用 `$httpClient`、`$task.fetch`、`fetch` 访问目标 App 以外的地址。
- **远程代码**：是否下载代码后执行（`eval`、`new Function`、动态 `require`）。命中就拒绝，或改写为本地逻辑。
- **混淆**：是否混淆或压缩到无法审阅。无法审阅的不托管；有可读替代的优先用替代。
- **数据**：是否读取 Cookie、Authorization、token，是否写入 `$persistentStore`/`$prefs`，以及读写了什么。
- **范围**：MitM 主机名是否只覆盖该 App 所需；有没有 `*` 通配大范围解密。
- **失败行为**：出错时是否原样放行，`$done` 是否只调用一次。

## 5. 逐 App 决策表

“退化”指替换后比墨鱼版少处理的接口，依据云端会话的规则对比（旧版用仓库内保留的墨鱼转换文件 `surge/modules/converted/*Ads.sgmodule`，新版用当前模块）。效果最终以真机为准。

| App | 替换前（级别） | 当前 | 已知差异 | 建议 |
|---|---|---|---|---|
| 高德 | 墨鱼 AmapAds（A/B，脚本 `amap.js` 在 GitHub） | QX/Surge：fmz200 + 仓库「高德页面净化」；Loon：可莉 | `main-page` 首页卡片、`nearbyrec_smart`、`promotion-web/resource`、`nodefaas` 我的页、`new_hotword`、`aocs/updatable`、`ai_rec`、`scene/recommend`、`card-service-route-plan` 等不再处理；页面净化补回其中一部分 | 真机对比“墨鱼 AmapAds 固定提交版”与当前方案，取效果好者。Loon 在“可莉冻结副本 / fmz200 AutoNavi / 墨鱼转换版”中三选一 |
| 微博 | 墨鱼 WeiboAds（A/B） | 三端 fmz200 冻结副本 | `video/tiny_stream_mid_detail`、`ug/checkin/stream` 不再处理；国际版 `open_app`、`user_center`、`search_topic` 在 Surge/Loon 不再处理（已接受） | 真机对比后决定；如需回到墨鱼版，用固定提交（A 级），无需冻结 |
| 闲鱼 | 墨鱼 GoofishAds（A/B） | fmz200 冻结副本 | 逐条核对无退化；amdc 改由「阿里系 amdc」统一处理 | 保持 |
| 大麦 | 墨鱼 DaMaiAds（**C**） | 仓库自写 | 8 个页面清理接口已去掉（2026-10-05 已接受） | 保持；如要恢复，走冻结托管 |
| 网易邮箱大师 | 墨鱼（A/B） | 仓库自写 | 页面配置与用户信息两项清理已去掉（已接受） | 保持；如要恢复，用固定提交 |
| 小红书 | 墨鱼 XiaoHongShuAds（**C**） | fmz200 + 仓库修改（去首页视频） | 新增了用户要的功能 | 保持 |
| 知乎 | 墨鱼 zhihu.ads.js（**C**） | QX/Surge：fmz200；Loon：可莉 | — | Loon 换 fmz200 `Zhihu.lpx`（固定 `5d5f63f`），与 QX/Surge 同源；效果不差即替换 |
| YouTube | 墨鱼 YoutubeAds（A/B，脚本 Maasea） | QX/Surge：Maasea 固定提交；Loon：可莉 | QX 不再拒绝 googlevideo（已接受） | Loon 候选：fmz200 `YouTube.lpx` 或 Maasea 原生；真机对比 |
| 微信外链 | 墨鱼（A/B） | QX/Surge：zZPiglet 固定提交；Loon：可莉 | — | Loon 换 fmz200 `WeChatUnlockLinkRestrict.lpx` |
| 小宇宙 | 墨鱼（A/B） | fmz200 + 用户保留的功能 | — | 保持 |
| 航旅纵横 | 墨鱼 `umetrip.ads.js`（**C**，已冻结） | 仓库可读版 `umetrip.js` | **见 5.8**：Surge/Loon 上脚本不生效 | 先修脚本（不涉及来源） |
| 开屏（QX） | 墨鱼去开屏 2.0（**C**） | bm7 + StartUpGaps + 开屏补充（三端） | 规则层面未逐条核对；#82 只做了域名层面的覆盖比较（373/437） | 见 5.9 |
| 微信小程序（QX） | 墨鱼 Applet（A/B） | 依赖 bm7 | QX 小程序开屏有残留（已记录） | 可恢复为固定提交版（A 级，规则 37 行，脚本 `applet.js` 公开），真机确认残留是否消失 |
| 节点检测（Loon） | KOP-XIAO 定时任务 | 可莉 | 无公开替代 | 二选一：冻结托管（先按第 4 节审核其网络请求），或在 AGENTS.md 记为明确例外 |

### 5.8 已知缺陷：航旅纵横脚本在 Surge/Loon 不生效

- **原因**：`quantumultx/scripts/umetrip.js` 只读 `$response.bodyBytes`，这是 QX 的接口。Surge/Loon 开启 `binary-body-mode` 后，二进制内容放在 `$response.body`（Uint8Array），脚本因此原样放行，也不打印日志。旧版 `umetrip.ads.js` 同样如此。
- **依据**：仓库已固定的 Maasea `youtube.response.js` 的 Surge 适配器把 `bodyBytes` 映射为 `body`，Loon 继承 Surge；app2smile `bilibili-proto.js` 也写的是 `isQuanX ? $response.bodyBytes : $response.body`。
- **修法**：输入时，QX 取 `bodyBytes`，否则当 `ArrayBuffer.isView($response.body)` 成立时取 `body`。输出按原字段回传：QX 返回 `{bodyBytes: ArrayBuffer}`，Surge/Loon 返回 `{body: Uint8Array}`。`tests/test_umetrip_script.py` 增加 Surge/Loon 二进制输入的用例。
- **真机验证**：Surge 脚本日志应出现 `[Umetrip]` 记录。

### 5.9 QX 去开屏 2.0

- 不建议整份恢复：它解密数百个主机名，MitM 范围过大，且为 C 级。
- **本机执行**：
  1. 用 QX UA 抓取 2.0，冻结一份到 `.local/` 存档（不进仓库）。
  2. 与用户已安装 App 清单（`.local/`）逐条对照，规则层面而非域名层面。
  3. 列出“已安装、2.0 有、现有三端都没有”的规则。
- 对照出的缺口，按 App 补进 `quantumultx/rewrite/StartupSupplement.conf`（三端同步）；规则少的可直接自写，带脚本的按第 3 节冻结托管。

## 6. 不建议整体回退到替换前

整体回退会丢掉与墨鱼无关、而且是用户要的改动：

- 小红书首页去视频笔记与视频频道（#80）
- 小宇宙保留 AI 总结、搜索、推荐（#79）
- 高德页面净化与开屏补充（#84）
- 阿里系 amdc（#81）与 CI 测试（#85）
- 闲鱼空分流修复（#86）

此外，回退后会重新以 `@master` 直接加载 C 级站点，回到原来担心的状态。第 5 节的逐 App 方案可以在保留这些改动的同时，把效果更好的墨鱼版本以固定提交（A 级）或冻结副本（C 级）的形式取回。

## 7. 实施分批

每批一个 PR，按 AGENTS.md 流程：方案审核 → 实现 → CI 全绿 → 真机验收 → 合并。

1. **批次 1（修复，无来源变化）**：5.8 航旅纵横脚本。
2. **批次 2（Loon 去可莉，公开同源替代）**：知乎、微信外链换 fmz200 托管副本（沿用 `scripts/build_loon_plugins.py` 的 `MIRRORS`，统一换小 PNG 图标）；YouTube 真机对比后再定。可莉条目在兼容期内保留并默认关闭。
3. **批次 3（效果对比，可能取回墨鱼 A 级版本）**：高德、微博、QX 小程序。墨鱼版一律固定到 `ddgksf2013/Rewrite` 与 `ddgksf2013/Scripts` 的完整提交 SHA，脚本若引用 `ddgksf2013.top` 则走冻结托管。用户真机 A/B 对比，取效果好者。
4. **批次 4（本机执行后再定）**：5.9 去开屏 2.0 缺口对照；节点检测工具的处理。

发布路径遵守 AGENTS.md：

- 新文件用新路径，旧路径在兼容期内保留不改。
- 合并前设备试验用 `@<完整提交 SHA>` 地址。
- 每批写明旧 → 新的映射和回滚步骤：回滚即在设备上改回旧条目，或 revert 该 PR。

## 8. 需要本机或用户完成的事项

- 用 Loon UA 抓取可莉插件，用 QX UA 抓取 `ddgksf2013.top` 资源：云端无法访问。
- 已安装 App 清单与使用频率：位于 `.local/`，云端不可见。用于第 5.9 节，并为第 5 节排定优先级。
- 各批次的真机 A/B 对比与验收。

## 9. 请 Codex 重点审核

1. 第 3 节的分级是否合理：B 级 `@master` 是否接受，脚本是否必须固定。
2. 第 5 节每个“建议”是否与 AGENTS.md 现有约定冲突。尤其是 #86 刚写入的“Loon 高德保留可莉”，本方案改为“三选一，以真机效果定”。
3. 5.8 的修法是否覆盖 QX、Loon、Surge 三种输入形式。
4. 批次划分与回滚是否足够小、可独立验收。
