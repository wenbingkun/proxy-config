# AGENTS.md

本文件是本仓库给 AI 编码代理（Claude Code、Codex 等）的唯一项目说明；`CLAUDE.md` 只做引用。用户以中文沟通，代码、命令、标识符保持英文。

## 项目

自用代理配置的**公开**仓库：一份共享规则源，生成四条客户端线的文件。设备通过 raw / jsDelivr 远程拉取其中的规则、snippet 和插件。

| 线 | 目录 | 设备 |
|---|---|---|
| Quantumult X | `quantumultx/` | iPhone、iPad |
| Loon（试点，QX 为回滚方案） | `loon/` | iPhone |
| Surge（试点） | `surge/` | iPhone |
| Mihomo | `mihomo/verge/`（Windows Clash Verge Rev）、`mihomo/shellcrash/`（路由器 ShellCrash） | Windows、路由器 |

共享：`rules/`（规则源）、`mihomo/rules/`（生成的 rule-provider，Verge 与路由器共用）、`scripts/`（生成与巡检）、`tests/`、`docs/design.md`。各端用法写在各自目录的 `README.md`，根 README 只做导航。

## 真值源与生成物

- 自维护规则只改 `rules/*.yaml` 与清单 `rules/local_rules.yaml`，然后 `python3 scripts/build_rules.py` 生成 `mihomo/rules/*.yaml`、`quantumultx/filter/repo.snippet`、`loon/rules/*.list`、`surge/rules/*.list`（含 `reject_allow.list`）。
- Mihomo 策略唯一来源是 `mihomo/verge/config.yaml`；`config-single.yaml` 和 `mihomo/shellcrash/config-router*.template.yaml` 由 `python3 scripts/build_router_config.py` 生成。
- **生成物不手改**。`loon/rules/geoip_cn.list` 是手工文件。
- 新增规则集还要在 `loon/bootstrap.example.conf` 的 `[Remote Rule]` 和 `surge/proxy-config.conf` 的 `[Rule]` 各加一行；Loon 与 Surge 都不支持 `domain_regex`。

## 提交前检查（仓库根目录执行，任一失败即停）

```sh
set -e
python3 scripts/build_rules.py --check
python3 scripts/build_router_config.py --check
python3 scripts/build_surge_modules.py --check
python3 scripts/build_loon_plugins.py --check
python3 scripts/check_hygiene.py
python3 scripts/check_upstreams.py
python3 scripts/check_acceptance.py
for t in tests/test_*.py; do [ "$t" = tests/test_shellcrash_override.py ] || python3 "$t"; done
sh -n mihomo/shellcrash/deploy.sh
```

`tests/test_shellcrash_override.py` 需要固定版本的 Mihomo 核心、mmdb 与 ShellCrash `clash_modify.sh`（版本与哈希见 `.github/workflows/validate.yml`），由 CI 运行；本地跑时按同样哈希下载。不要用 `cmd || echo` 这类会吞掉失败的写法。

## 发布路径

- 设备按固定路径拉取文件。改名或移动已发布的路径前，必须给出旧→新映射、仓库与设备引用清单、发布 / 切换 / 回滚步骤。
- 默认保留兼容期；只有用户明确接受旧路径失效时才直接切换，并记录尚未迁移的设备。
- 合并前不要请求新路径的 jsDelivr `@main` 地址（会缓存 404），用 `@<完整提交 SHA>` 验证。

## 各端约定（改动前先读，别“补齐”有意的差异）

去广告以各端能力相近为目标，优先采用适合该客户端的原生实现，不为统一来源替换已验证方案。当前 Loon 高德保留可莉插件；新增补充前先核对现有覆盖，避免同一 App 出现容易混淆的重复条目。以效果优先，不禁止墨鱼来源；回取的公开资源与脚本固定完整提交，个人站点内容仅用审核后的冻结副本。每 App/服务每端只保留一个启用的专用入口；仅去开屏规则可合入共用补充，阿里系 amdc 独立。QX 同一混合资源双段加载算一个入口。

**策略组**：以路由器模板为基准，三端的 32 个基础策略组组名、组序一致；QX 与 Loon 另有 16 个 ssid 包装组（见下）。
- 家庭/外出切换（QX、Loon 相同）：需要区分的策略由排在最后的 16 个「· 自动」ssid 组包装，在家走 DIRECT 交给路由器，外出（其他 Wi-Fi、蜂窝）走同名基础组；规则只引用包装组。REJECT、DIRECT、`🛡️ 安全防护` 等不需要切换的策略不包装。全程规则模式：Loon 不用 `ssid-trigger`，QX 不用 `running_mode_trigger` / `ssid_suspended_list`。包装组清单唯一来源是 `scripts/build_rules.py` 的 `HOME_AUTO_GROUPS`（同时决定 `repo.snippet` 的策略名），`tests/test_qx_config.py` 与 `tests/test_loon_config.py` 校验两端模板与之一致。
- QX 的 `🌏 全球加速` 有意不挂规则；ProxyLite 有意不移植到 QX。
- 地区组只含本地区节点；非美地区组按延迟自动优选，美国组手动。

**Quantumult X**
- ssid 策略写法：`ssid=X · 自动, X, X, HOME_SSID:DIRECT`（依次为其他 Wi-Fi、蜂窝、家庭 SSID）。仓库 `repo.snippet` 引用「· 自动」组，设备配置缺少这些组时其规则无法生效。
- 丢弃 UDP 443（QUIC），让 App 回退到 TCP、MitM 才能生效；`fallback_udp_policy = reject`；不设 `no-ipv6`。
- QX 没有 `no-resolve`：没有域名类规则命中的请求会先解析，再按顺序匹配 IP 规则。所以 bm7 `Cloudflare.list` 用资源解析器去掉 IP 规则（URL 加 `#out=IP-CIDR+IP6-CIDR+IP-ASN&ntf=0`，`opt-parser=true`；`ntf=0` 关掉解析器每次更新都弹的“已禁用”通知；关键词区分大小写），只保留域名，与 Mihomo 的 Cloudflare 约定一致；否则未收录、托管在 Cloudflare 上的网站都会进 👨‍💻 开发服务。
- B 站使用仓库冻结托管的 `quantumultx/rewrite/bilibili_ad.conf` 与 `quantumultx/scripts/bilibili_json.js`，不跟上游，不与其他 B 站重写同时启用。
- 观察（2026-09）：QX 测速组只在被请求时测速，在家空闲时停在首个节点，属正常。

**Loon**
- ssid 组写法：`X · 自动 = ssid, default = X, cellular = X, "HOME_SSID" = DIRECT`。
- `GEOIP,CN` 必须作为最后一条远程规则（`loon/rules/geoip_cn.list`），不能放本地 `[Rule]`。
- Loon 同一阶段命中的 Rewrite 按配置顺序全部执行，效果可以叠加；同一字段可能被后面的规则覆盖（QX 是命中第一条即停）。用 Script-Hub 转换 QX 重写后，要检查重叠规则的实际效果，不能按 QX 的首次命中模型推断。
- `loon/plugins/` 托管冻结的转换版，脚本地址固定到审核过的上游提交，文件头写明来源与重新生成方法。墨鱼自建域名 ddgksf2013.top 上的资源没有提交可固定：文件头记录抓取日期和 SHA-256；冻结版引用的脚本托管副本（如 `quantumultx/scripts/umetrip.ads.js`），不直接引用该域名；副本相对上游的修改写在文件头并配回归测试（`tests/test_umetrip_script.py`）。fmz200 按 App 拆分的资源（`fmz200/wool_scripts` 的 `*/split/`）三端原生：无脚本的，Surge `SOURCES` 与 QX 直接引用固定的完整提交 SHA（QX 的 `.snippet` 混有分流，含分流的要在 `[rewrite_remote]`、`[filter_remote]` 各引用一次，都 `opt-parser=true`）；Loon 用 `scripts/build_loon_plugins.py` 生成的托管副本，只换图标（上游是 560 KB 的 GIF）。仓库托管的 Loon 插件图标统一由 `build_loon_plugins.py` 的 `ICONS` 管理，用 App Store 的小 PNG；带脚本的要固定脚本并按附录式设计另行审核。三份测试独立写死期望的提交、无脚本和默认关闭。新插件在模板里先 `enabled=false`，真机验收后再改默认值。
- 远程资源巡检对 kelee.one 使用已验证可用的完整 iOS Loon UA（见 `scripts/check_remote_resources.py`）；只带 `Loon/x.y.z` 时曾返回 403。
- Loon 重新保存配置时会去掉逗号后的空格，比对设备配置前先统一格式。
- 插件图标（`#!icon`）只用小 PNG，不用 GIF：2026-10-05 fmz200 插件的 560 KB 动图图标让 Loon 插件页一直转圈，换成 App Store 的 100×100 PNG（几 KB，`scripts/build_loon_plugins.py` 的 `ICONS`）后真机确认恢复正常。`tests/test_loon_config.py` 拒绝非 PNG 图标；新托管插件要在 `ICONS` 登记对应 App 的图标。QX 的 `img-url` 等其他客户端的图标同样用小 PNG。

**Surge**
- 结构是托管配置 `surge/proxy-config.conf`（公开段）加设备上的 `bootstrap.conf`（`#!include` 公开段，节点、MitM、SSID、机场 DNS 留在本地）。31 个组与 Loon 同名同序，**有意没有** 16 个「· 自动」包装组和 `🛡️ 安全防护`，`HOME_AUTO_GROUPS` 不适用于 Surge。
- 家庭切换与拦截都在 `surge/modules/home-direct.sgmodule`：两条 `AND(RULE-SET 拒绝列表, NOT RULE-SET reject_allow.list)` 拒绝规则（`pre-matching`；`extended-matching` 写在每个 RULE-SET 子规则上，不写在外层）在前，`SUBNET,SSID:{{{HOME_SSID}}},DIRECT` 在后。UDP 没有预匹配阶段，所以顺序不能颠倒，且这个模块要排在其他含 REJECT 的模块之后。`home-direct-noblock.sgmodule` 只有 SUBNET 一行，用作误拦截时的整体放行开关，二选一启用。
- 拒绝列表排在仓库规则之前（与 QX / Loon 相反）。重叠情况由 `scripts/check_reject_conflicts.py` 报告；巡检里只告警，输入或下载错误才算失败。金融保护靠 `reject_allow.list`（由 `hk_banks.yaml` + `intl_brokers.yaml` 生成），不要手写例外。
- 地区组是 `smart`，正则与 Loon `[Remote Filter]` 逐字一致；美国组仍是 `select`。`tests/test_surge_config.py` 校验组、规则顺序、模块内容和占位符。
- Surge 严格按顺序匹配，域名请求遇到第一条会解析的 IP 规则就本地解析；之后带 `no-resolve` 的 IP 规则也会拿这个结果匹配。所以 `surge/proxy-config.conf` 的 `[Rule]` 只允许末尾区域主动解析：再次引用的 `local_network.list`（不带 `no-resolve`，外出时公开 DNS 解析成私有地址的未收录域名仍走 DIRECT）、`Alibaba_All`、`Tencent_All`、`China_All` 三个 RULE-SET（保留对未收录域名的国内 IP 兜底）、`GEOIP,CN`、`FINAL,…,dns-failed`，顺序固定。它前面的每条 RULE-SET 和 IP 规则都必须带 `no-resolve`（含 #57 的 `local_network`、#60 的 `Apple_All`），新增规则集也要加，不能依赖上游列表逐条写了 `no-resolve`。`tests/test_surge_config.py` 会校验。仓库模块（`home-direct`、`home-direct-noblock` 及 `rewrite/` 下的拆分模块）的规则排在整份配置之前，也守这条约定：`home-direct` 两条拒绝规则的 RULE-SET 子规则带 `no-resolve`（2026-10-04 在 iOS 27.2、Surge 5.22.1 上真机实验证实 AND 子规则上的参数有效）；唯一允许的 NOT 是引用 `reject_allow.list` 的那一条，不加 `no-resolve`（否则放行会失效），该列表只能是纯域名。设备本地模块不在测试范围内。这只控制规则阶段的解析时机；DIRECT 建连仍要 DNS，未收录的海外域名仍会在末尾解析。只在 Surge；Loon 先匹配域名，QX 语义不同。
- 改写模块：Surge 没有在配置里列出模块的段落，Loon 中启用的插件按来源拆成 `surge/modules/rewrite/*.sgmodule`（每个 App 一个，分类 proxy-config，设备上按需启用），由 `scripts/build_surge_modules.py` 生成（参数在生成时写入，不手改；`--check` 联网重新生成并比对，CI 也运行）。旧的合并模块 `surge/modules/rewrite.sgmodule` 已于 2026-10-05 删除（设备已迁移）。Surge 没有说明模块之间的执行顺序，Loon 里专用插件在合集之前的先后在这里没有保证：按主机名看没有冲突，共享的 MitM 主机名固定在 `tests/test_surge_config.py` 的 `SHARED_MITM`，变化时先核对只执行首条匹配的段里不会对同一请求给出不同结果，再改清单；通用去广告里不限主机的 reject 模式（`advertising`、`/ad/` 等）与别的模块的跳转仍可能同时命中，这一已接受的差异写在 `surge/README.md`，`SHARED_MITM` 查不出这类重叠。来源是固定提交 / 发布标签的上游 Surge 模块，或 `surge/modules/converted/` 中冻结的墨鱼 QX 转换（Script-Hub `target=surge-module`，文件头写明来源与转换后修改）。Surge 只运行第一个匹配的 http-response 脚本和第一个匹配的 header 模式 URL Rewrite（与 QX 相同，与 Loon 不同），`SOURCES` 保持 Loon 插件顺序，转换时保持 QX 顺序，不要照搬 Loon 版的倒序；脚本名必须唯一。Kelee 的插件用 Loon 专有语法且脚本只对 Loon UA 提供，不移植、也不伪装 UA 抓取：小红书、知乎改用 fmz200 的公开 QX 规则，高德由仓库合并墨鱼固定版本与已验证页面清理，微信外链使用固定版本 zZPiglet；节点检测没有对应功能。模块里的 IP 规则必须带 `no-resolve`。
- 机场 DNS：Surge 没有按节点指定解析器的办法，代理主机名也不匹配 `[Host]`，所以由设备本地模块 `airport-dns.sgmodule`（模板 `surge/airport-dns.example.sgmodule`）覆盖全局 `encrypted-dns-server`；不要再用 `[Host] server:` 给节点指定 DNS。
- 合并前设备试验用固定到提交 SHA 的本地副本（去掉 `#!MANAGED-CONFIG`），见 `surge/README.md`。

**Mihomo / ShellCrash**
- DAZN、Cloudflare、Amazon provider 只用域名规则；不用 GlobalMedia 聚合。共享 CDN 的 IP 段会误判。
- `mihomo/verge/config.yaml` 的 `rules` 分两段：域名段的每条 RULE-SET 都带 `no-resolve`（Mihomo 遇到第一条不带它的 IP 规则就本地解析，域名规则应先决定）；IP 段在 `GEOIP,CN` 前按原顺序再次引用含 IP 规则的 rule-set（不带 `no-resolve`），给没有域名规则命中的请求做 IP 兜底；测试固定了 10-04 含 IP 规则的全部 20 个（LocalNetwork、Lan、AdGuard、Hijacking、Alibaba、Tencent 等），删减要先审核。新增 rule-set 默认带 `no-resolve`；它含 IP 规则时也加进 IP 段。`tests/test_rule_provider_scope.py` 校验。
- 路由器上，仓库只管 proxy-providers、策略组、rule-provider 和规则；端口、DNS、TUN、sniffer、控制器、防火墙归 ShellCrash（当前设备约定 sniffer 保持开启）。Windows 的运行参数仍由 `mihomo/verge/config.yaml` 管理。
- 当前路由器访问 raw.githubusercontent.com 不稳定：部署时把 `TEMPLATE_URL` 指向 jsDelivr 固定的完整 SHA，后台运行；每次部署使用独立的结果目录，记录退出码与完成标记。`start-stop-daemon` 必须带 `-m -p`。
- 回滚使用部署前的固定备份；`yamls/config.yaml.bak.proxy-config` 只服务于本次部署的自动回滚，每次部署都会被覆盖，不能当作长期回滚点。
- 已知限制：Mihomo 遇到不支持 UDP 的节点会跳过规则继续匹配，与 QX 的 reject 不同。

## 工作流程

1. 先查清再动手。改配置前写方案或审核报告，交用户转给 Codex 审核；**审核通过前不 commit、不 push**。
2. 报告放 `.local/<router|qx|loon|repo>/<YYYY-MM-DD>-<topic>/`（日期取任务开始日）：`PLAN*.md`、`REVIEW*.md`、`CODEX-REVIEW*.md`、`evidence/`、`rollback/`。报告要写清改动、依据、已跑的验证及复跑命令、反向测试、待审重点和范围外遗留。
   `.local/` 只在用户本机（WSL）存在。动手前先看 `.local/README.md`：不存在就是云端会话或新克隆，**不要创建 `.local/` 及其中的文件**（云端写入回不到本机），方案和报告直接写在回复里，由用户回本机后归档；设备文件（iCloud、路由器）在云端同样不可用。
3. 审核通过后在功能分支提交（Conventional Commits，如 `fix(loon): …`），开 PR，CI 全绿。合并默认由用户安排 Codex 完成；只有用户当次明确授权，代理才自行 squash 合并并删除分支。
4. 涉及设备（QX、Loon iCloud 配置、Verge 配置副本、路由器）时：先备份、写入前核对哈希，只替换必要的行，保留订阅、MitM、节点选择和设备覆写。设备行为的结论要能重复验证。
5. 只做当前任务需要的最小改动。发现范围外的问题，记为后续事项，不顺手修改。
6. 排查 iPhone 网络问题（用户说“分析刚才的网络行为”）时，先用 `scripts/netdiag.py status` 和 `collect --since <窗口>` 取数据，别让用户截图转述；数据来源、能力边界和约定见 `docs/netdiag.md`。
7. 产物放置：本条仅约束不纳入版本控制的本机辅助产物；正式源码、测试、文档和发布生成物仍按仓库现有目录与生成规则管理。本项目专用的一次性脚本、临时测试工具、下载的依赖与数据（如本地跑 `tests/test_shellcrash_override.py` 用的核心、mmdb）、虚拟环境、采样、日志、PR 正文草稿、取证与回滚材料放在 `.local/`：按任务归档到 `.local/<area>/<日期-主题>/`，跨任务复用的放 `.local/tools/`。多个项目通用的工具按用户全局约定放置，不放进本仓库。都不散落在 `~`、`~/workspace` 等用户目录。临时文件用 `tempfile.TemporaryDirectory()` 或在 `.local/` 下建目录，任务结束前删除本任务创建且不再使用的路径，不在 `/tmp` 遗留（不等于清空共享的 `/tmp`）。例外只限系统或工具规定的位置：`~/.ssh/` 的密钥、systemd 用户服务单元、`docs/netdiag.md` 写明的 netdiag 配置与记录目录；现有 netdiag USB 环境 `~/.local/share/ios-netdiag/venv` 被服务单元和入口脚本固化，作为有限例外保留，下次重建时再迁到 `.local/tools/`（按原版本重建、验证、更新配置和服务单元，验收前保留旧环境）。云端会话按第 2 条不建 `.local/`，辅助产物放系统临时目录；需要交付的结论先写进回复，再删除临时副本。

## 隐私与安全（公开仓库）

- 不提交：订阅 URL / token、MitM passphrase / p12 / 证书、`*/bootstrap.conf`、`mihomo/shellcrash/providers.env`、Cookie、API Key；仓库内只用占位符。
- 机场名称、机场 DoH、节点域名、家庭 SSID 不进仓库、提交说明或 PR 描述，只在设备本地写入。
- `.local/` 是本机档案（Git 忽略），含设备原始日志和私密配置，不提交、不上传。索引见 `.local/README.md`。
- 打印设备配置的差异时要脱敏，包括以 `+` / `-` 开头的行。
- 不确定用途的服务和文件，先查依赖并告知用户，不擅自关闭或删除。破坏性操作前必须备份。`chmod` 不用通配符（目录 700，文件按名称或用 `find -type f` 设置）。
