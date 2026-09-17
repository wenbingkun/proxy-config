# DNS 误分流排查与修复时间线

> 归档日期：2026-09-17。本文为历史故障记录，设备私密设置仍保存在设备本地。

本文按事件发生顺序整理，保留证据、方案变化和验收边界。2026-09-17 早期各轮没有精确时间，按讨论与操作先后编号，不补造时间；已记录的设备时间为 UTC+8。

**截至最后一次复验：设备侧 DNS 修复已生效，抽样分流正常；仓库运行配置未改。服务重启验证两次，Claude 在第 9 节补充了整机重启后恢复的事后证据（非受控断电测试）。jable 的 HTTP 403 已查明为 Cloudflare 浏览器质询，该响应本身不构成 DNS 或分流故障证据；页面可浏览性仍只能用真实浏览器判定。**

| 顺序 | 时间 | 阶段 / 记录来源 | 当时状态 |
|---|---|---|---|
| 1 | 2026-09-03 | [初始排查](#stage-1)，原始审计记录 | 刷新规则无效，怀疑 DNS 污染 |
| 2 | 2026-09-17，第 1 轮 | [仓库审核](#stage-2)，Codex | 仓库检查通过，设备状态待查 |
| 3 | 同日，第 2 轮 | [设备取证与初案](#stage-3)，Claude | 明文 DNS 路径异常得到证据支持 |
| 4 | 同日，第 3 轮 | [方案复核](#stage-4)，Codex | 要求固定代理出口、补足测试 |
| 5 | 同日，第 4 轮 | [补充取证](#stage-5)，Claude | 标准 DoH 可用，暂缓 no-resolve |
| 6 | 同日，第 5 轮 | [实施前结论](#stage-6)，Codex | 收敛为设备侧最小修复 |
| 7 | 同日，复验前；备份名含 14:37:35 | [实施与服务重启验收](#stage-7)，Claude | 修改 dns_fallback，完成设备验收 |
| 8 | 同日，约 14:56–15:00 | [独立设备复验](#stage-8)，Codex | DNS、出口和分流通过抽样复验 |
| 9 | 同日，约 15:05–15:10 | [补充验收](#stage-9)，Claude | 质询响应与重启恢复证据；403 成因假设 |

下文早期候选方案仅供追溯，**最终实际变更以第 7 节为准，最后验证边界以第 8、9 节为准**。备份文件名只作为时间线索，不代表实施起止时间。

<a id="stage-1"></a>

## 1. 2026-09-03：初始排查与候选方案

来源：原始审计记录。该阶段仅刷新规则集，尚未修改设备 DNS 或仓库配置。

### 1.1 问题现象

ShellCrash 面板显示同一个主域名下的连接进入了不同策略组：

| 连接 | 面板显示的规则 | 策略组 |
|---|---|---|
| `assets-cdn.jable.tv:443` | `RuleSet: ProxyLite` | `🌏 全球加速` |
| `jable.tv:443` | `RuleSet: Twitter` | `🌐 社交平台` |

直觉上容易认为，`jable.tv` 和 `assets-cdn.jable.tv` 应该因为属于同一个主域名而使用同一策略。但 Mihomo 不会按注册域名建立“继承关系”，而是对每一条连接独立匹配规则；规则按配置顺序从上到下执行，先命中的规则生效。

### 1.2 当时的规则配置

当时 Clash/Mihomo 主配置中的相关部分如下：

- `Twitter` provider 是 `behavior: classical`，定义见 [`clash/config.yaml:307`](../../config.yaml#L307)。
- `ProxyLite` provider 是 `behavior: domain`，定义见 [`clash/config.yaml:345`](../../config.yaml#L345)。
- `Twitter` 规则排在 `ProxyLite` 之前，见 [`clash/config.yaml:392`](../../config.yaml#L392) 和 [`clash/config.yaml:433`](../../config.yaml#L433)。
- 当时 ProxyLite 规则明确包含 `+.jable.tv`，按域名应覆盖 `jable.tv` 及其子域名。
- 当时 Twitter 规则内容没有 `jable` 字符串，但包含以下目标 IP 规则：

```text
IP-CIDR,192.133.76.0/22
IP-CIDR,199.59.148.0/22
IP-CIDR,199.96.56.0/21
IP-CIDR,202.160.128.0/22
IP-CIDR,209.237.192.0/19
IP-CIDR,69.195.160.0/19
```

`classical` provider 支持域名规则和 IP-CIDR 规则；Mihomo 在检查目标 IP 规则时可以先解析域名。因此，即使域名没有写在 Twitter provider 中，只要 DNS 把域名解析到了 Twitter 的 IP 段，就可能命中 Twitter。参见 [Mihomo 路由规则说明](https://wiki.metacubex.one/en/config/rules/) 和 [rule-provider 内容说明](https://wiki.metacubex.one/en/config/rule-providers/content/)。

### 1.3 设备证据

#### 规则集刷新

当时通过路由器 Mihomo API 执行：

```text
PUT http://192.168.31.1:9999/providers/rules/Twitter  -> HTTP 204
PUT http://192.168.31.1:9999/providers/rules/ProxyLite -> HTTP 204
```

刷新后状态：

```text
Twitter:   33 条规则，updatedAt=2026-09-03T00:51:08+08:00
ProxyLite: 27078 条规则，updatedAt=2026-09-03T00:51:12+08:00
```

这说明两个 provider 已经刷新，且不是长时间未更新导致的普通缓存过期问题。

#### DNS 返回结果

通过 Mihomo DNS API 查询到：

```text
jable.tv              -> 199.59.148.89
jable.tv              -> 69.171.224.40       # 随后再次查询得到不同结果
www.jable.tv          -> 202.160.128.203
assets-cdn.jable.tv   -> 162.125.32.10
```

其中：

- `199.59.148.89` 属于 Twitter 规则的 `199.59.148.0/22`；
- `202.160.128.203` 属于 Twitter 规则的 `202.160.128.0/22`；
- `assets-cdn.jable.tv` 的地址没有落入 Twitter 规则，因此它可以继续被后面的 ProxyLite 域名规则匹配；
- 同一个 `jable.tv` 查询在短时间内返回不同社交平台相关地址，且此前查询结果 TTL 只有 1 秒，这符合上游 DNS 污染或 DNS 劫持的特征。

因此，截图中的分流链路可以还原为：

```text
jable.tv
  -> 路由器 DNS 返回 199.59.148.89
  -> Twitter 的 IP-CIDR 规则命中
  -> Twitter RuleSet
  -> 🌐 社交平台
```

而不是：

```text
jable.tv
  -> 被错误写入 Twitter 域名规则
```

#### 其他观察

活动连接扫描时还曾出现：

```text
www.google.com -> 69.171.235.22 -> RuleSet: Facebook
```

该连接随后关闭，当时未稳定复现，因此暂定为同类 DNS/IP 误判风险，不作为持续性配置错误定论。`x.com`、`web.telegram.org` 等命中社交规则的连接属于预期结果。

路由器当时运行配置还显示 `TUN` 和 IPv6 已启用，而仓库基础配置中分别是关闭状态。这表明路由器正在使用 ShellCrash/Mihomo 生成或覆写后的运行配置，不能只依据仓库的 `clash/config.yaml` 判断实际 DNS 行为；需要核对路由器实际生效的 `dns:` 段。

### 1.4 当时结论

根因优先级如下：

1. **直接根因：路由器上游 DNS 返回了落入社交平台 IP 段的错误地址。**
2. **放大因素：社交 provider 使用 `classical`，包含 IP-CIDR；同时 Twitter 规则排在 ProxyLite 前面。**
3. **规则缓存不是主要根因：两个 provider 已成功刷新，条目数和更新时间正常。**
4. **运行配置可能与仓库源配置不一致：需要核对 ShellCrash 实际加载的 DNS 配置及其 fallback 是否可用。**

按当时下载的 70 个规则 provider 扫描，`jable` 只出现在 ProxyLite，没有发现其他社交 provider 的域名规则包含 `jable`。因此本问题不是仓库把 `jable.tv` 错写进 Twitter，而是 DNS 结果触发了 Twitter 的 IP-CIDR 规则。

### 1.5 当时提出的候选方案（历史记录，非当前操作指令）

| 方案 | 当时目的 | 后续处理 |
|---|---|---|
| A：修复设备上游 DNS | 使用加密解析并验证代理路径 | 最终采纳，具体变更见第 7 节 |
| B：给社交 RULE-SET 加 `no-resolve` | 减少 IP 规则触发解析造成的误分组 | 第 2、4、5、6 节逐步明确局限，未实施 |
| C：前置 `DOMAIN-SUFFIX,jable.tv,🌏 全球加速` | 为该域名及子域名提供临时保护 | 未实施；不能修复其他域名或 DNS 根因 |
| D：引入 SmartDNS/MosDNS | 单独管理国内外解析 | 未实施；先验证现有系统最小修复 |

原拟流程为：读取设备实际 DNS → 修复解析 → 处理缓存 → 评估路由防护。原验收目标包括 jable 主域名及子域名解析、Google 与社交服务分流、重启稳定性，以及记录 ShellCrash 与仓库配置的差异。实际执行及未完成部分见第 7、8 节，不能把原计划视为已执行。

<a id="stage-2"></a>

## 2. 2026-09-17 第 1 轮：Codex 仓库审核

本轮未连接路由器，结论仅覆盖仓库及测试环境。当时仍保留该文档，等待设备证据。

- 仓库检查通过：39 个 YAML 文件解析、两个构建脚本的 `--check`、hygiene、acceptance、地区组、远程资源离线测试、provider 范围、Steam 分流、ShellCrash 部署事务和 shell 语法检查。
- 在线检查通过：184 个唯一资源（70 个 Clash 规则、36 个图标、69 个 QX 资源、7 个脚本、2 个路由器模板）。`full` 模式完整检查 Clash 规则结构，其余资源检查有限响应内容；不代表 QX 脚本运行或实际业务访问已通过。
- 使用 `.github/workflows/validate.yml` 固定版本和 SHA-256 校验的 Mihomo 核心，四份配置均通过 `-t`；单、双订阅路由器模板均通过 ShellCrash 官方覆写脚本集成测试。此测试不是设备 DNS、节点连通性或真实应用分流验收。
- **修正方案 B 的边界：** `no-resolve` 仅阻止该规则主动解析；已有目标 IP（包括更早规则解析得到的 IP）仍参与匹配。当前靠前的 `LocalNetwork` 包含未加 `no-resolve` 的 IP-CIDR，因此只修改社交 RULE-SET 不能保证阻断误分组。参见 [Mihomo 官方说明](https://wiki.metacubex.one/config/rules/#no-resolve)。
- Windows 配置已有 DoH、`respect-rules: true` 和独立的 `proxy-server-nameserver`，当前 DNS URL 使用 IP 地址，不能把缺少显式 `default-nameserver` 单独判为错误。`respect-rules` 表示遵守路由规则，并不保证所有境外 DNS 必走代理。参见 [DNS 官方说明](https://wiki.metacubex.one/config/dns/)。
- 路由器模板有意不包含 DNS/TUN 等运行参数，由 ShellCrash 本地管理；修改 Windows 主配置的 DNS 不会修复路由器 DNS。当时下一步是取得设备实际生效的 DNS 配置及新连接证据，再决定解析修复或域名/IP 规则拆分，不直接套用 A + B。

<a id="stage-3"></a>

## 3. 2026-09-17 第 2 轮：Claude 设备取证与初案

Claude 从 WSL 经 `ssh miwifi` 只读取证。环境：Mihomo `v1.19.17`、ShellCrash `1.9.5alpha15`，`crashcore=meta`、`dns_mod=mix`、`redir_mod=Mix`、`ipv6_dns=ON`。本阶段尚未实施修复。

### 3.1 修复前实际 DNS 配置

```yaml
default-nameserver: [ 223.5.5.5, 2400:3200::1 ]
direct-nameserver: [ 127.0.0.1 ]
enhanced-mode: fake-ip
ipv6: true
respect-rules: true
nameserver-policy: {'rule-set:cn': [ 127.0.0.1 ]}
proxy-server-nameserver : [ 223.5.5.5, 2400:3200::1 ]
nameserver: [ 1.1.1.1, 8.8.8.8 ]
```

要点：

- `nameserver` 是**明文 UDP 53**，不是 DoH。仓库 `clash/config.yaml` 里的 DoH、`fallback` 和 `fallback-filter` 在路由器上**完全不存在**，不能用 Windows 配置推断设备行为。
- 该段由 `$CRASHDIR/starts/clash_modify.sh` 在每次启动时生成，`nameserver` 取自 ShellCrash 变量 `dns_fallback`，其默认值正是 `"1.1.1.1, 8.8.8.8"`（见 `libs/get_config.sh:22`）。
- 只有当 `$CRASHDIR/yamls/user.yaml` 含有 `^dns:` 时，ShellCrash 才会跳过整段生成。

### 3.2 污染复现及 DoH 对照

经 Mihomo DNS API（`/dns/query`）连续三轮查询，结果稳定且全部错误：

| 域名 | 路由器返回 | 实际归属 |
|---|---|---|
| `jable.tv` | `67.230.169.182`（TTL 255） | 伪造 |
| `www.jable.tv` | `128.242.250.157` | 伪造，该主机名实为 NXDOMAIN |
| `assets-cdn.jable.tv` | `31.13.95.33` | Facebook `31.13.64.0/18` |
| `www.google.com` | `174.132.167.252`（TTL 23） | 伪造 |

在路由器上直接对明文 UDP 53 取证，三个上游返回完全相同的伪造结果：

```text
nslookup www.google.com 8.8.8.8   -> 104.244.42.197 , 2001::1
nslookup www.google.com 1.1.1.1   -> 104.244.42.197 , 2001::1
nslookup www.google.com 223.5.5.5 -> 104.244.42.197 , 2001::1
```

当时据此判断明文 DNS 路径受干扰。后续复核指出：这些结果支持存在污染，但不足以区分响应注入、透明重定向或统一上游，也不能外推到所有境外域名。

对照组，经代理的 DoH 返回干净结果：

```text
curl -x 127.0.0.1:7890 https://dns.google/resolve?name=www.google.com
  -> 142.251.152.119 等（真实 Google）
curl -x 127.0.0.1:7890 https://dns.google/resolve?name=jable.tv
  -> 172.66.167.218 , 104.20.42.172（Cloudflare）
curl -x 127.0.0.1:7890 https://dns.google/resolve?name=assets-cdn.jable.tv
  -> 15.235.9.226 , 51.161.118.150（OVH）
curl -x 127.0.0.1:7890 https://dns.google/resolve?name=www.jable.tv
  -> Status 3（NXDOMAIN）
```

### 3.3 当时的根因解释

`respect-rules` 使 DNS 连接遵循规则，但裸 IP `1.1.1.1:53` / `8.8.8.8:53` 最终落入 `MATCH / 🐟 兜底分流`，该组选择 `DIRECT`。异常解析结果随后被前置 IP 规则写入连接 metadata，社交 provider 的 IP-CIDR 因而先于 ProxyLite 域名规则命中。

DoH 域名的引导查询当时正常：

```text
nslookup dns.google 223.5.5.5        -> 8.8.8.8 , 8.8.4.4
nslookup cloudflare-dns.com 223.5.5.5 -> 104.16.248.249 , 104.16.249.249
```

### 3.4 初案及后续更正

Claude 初案是只修改 `dns_fallback` 为域名 DoH，依靠 Google/Cloudflare 规则送入现有代理组，再给 LocalNetwork/Lan 加 `no-resolve`。另建议保留本地国内解析和节点引导 DNS。

以下初案判断随后被修正，不再作为操作依据：

- “域名 DoH 自然保证代理出口”：改为显式指定策略组。
- “只改 LocalNetwork/Lan 就能避免前置解析、每次省一次往返”：后续发现其他前置 provider 也含 IP 规则。
- “受害子域名变化使 jable 后缀例外失效”：错误，后缀规则本来覆盖子域名；未采用的理由是覆盖范围有限且不修复 DNS。
- “curl 空响应即可证明 DoH 被阻断”：初始记录缺少完整请求及状态信息，第 5 轮重做标准 DoH 测试。

<a id="stage-4"></a>

## 4. 2026-09-17 第 3 轮：Codex 复核初案

本轮核对仓库和 CI 固定的 ShellCrash 覆写脚本，未重新连接设备。

1. **明确 DNS 出口。** 域名 DoH 依赖当前规则和策略选择，开发服务组允许 DIRECT。建议使用 `#策略组名`，并保留独立节点域名解析，避免启动依赖。
2. **限制 no-resolve 的承诺。** 它只阻止当前规则主动解析，已有目标 IP 仍参与匹配；后续规则也可能解析。修改本地网段规则还可能改变内网域名直连行为。
3. **更正域名例外的边界。** `DOMAIN-SUFFIX,jable.tv` 覆盖主域名及所有子域名，但无法解决 Google 等其他域名。
4. **补足 DoH 证据。** 对同一有效查询记录 curl 退出码、HTTP 状态及 TLS 错误，并测试 Mihomo 实际使用的标准 `/dns-query`，不能只凭 JSON `/resolve` 成功验收。
5. **区分解析路径。** 修改 `nameserver` 不会同时替换 `direct-nameserver`、国内域名策略和节点引导路径；需要分别检查 A/AAAA、LAN、实际出口及重启后的状态。

参考：[Mihomo DNS 配置](https://wiki.metacubex.one/config/dns/)、[no-resolve 说明](https://wiki.metacubex.one/config/rules/#no-resolve)、[Google DoH 接口](https://developers.google.com/speed/public-dns/docs/doh)。

<a id="stage-5"></a>

## 5. 2026-09-17 第 4 轮：Claude 补充取证与修订

Claude 重新连接设备，接受上述主要意见，并补充以下证据。本阶段仍未修改生产配置。

### 5.1 前置 IP 规则分布

当时统计 Twitter 之前含 IP 类规则的九个 RULE-SET：

| RULE-SET | IP 类规则条数 |
|---|---|
| `LocalNetwork` | 7 |
| `Lan` | 18（`behavior: ipcidr`，整份都是网段） |
| `AdGuard` | 489 |
| `Hijacking` | 42 |
| `WhatsApp` | 11 |
| `Line` | 16 |
| `YouTube` | 3 |
| `BiliBili` | 8 |
| `Telegram` | 15 |

这说明只改 LocalNetwork/Lan 无法阻止其他 provider 触发解析。Claude 将 `no-resolve` 改为暂缓；“改完九条就能解决”的推断在第 6 轮再次收紧。

### 5.2 按 RFC 8484 重测 DoH

使用 `www.google.com` 的 A 查询，GET 参数为 base64url 编码 DNS 报文；以下数据来自 Claude 当时记录。

直连：

| 端点 | curl 退出码 | HTTP | 耗时 | 失败形态 |
|---|---|---|---|---|
| `https://1.1.1.1/dns-query` | 28 | 000 | 12.00s | 连接超时，TCP 未建立 |
| `https://8.8.8.8/dns-query` | 28 | 000 | 12.00s | 连接超时，TCP 未建立 |
| `https://dns.google/dns-query` | 28 | 000 | 12.00s | 连接超时，TCP 未建立 |
| `https://cloudflare-dns.com/dns-query` | 35 | 000 | 0.25s | `Recv failure: Connection reset by peer` |

经代理（`-x http://127.0.0.1:7890`）：

| 端点 | curl 退出码 | HTTP | 响应体 | 失败形态 |
|---|---|---|---|---|
| `https://1.1.1.1/dns-query` | 35 | 000 | 0 | `SSL_connect: SSL_ERROR_SYSCALL` |
| `https://8.8.8.8/dns-query` | 35 | 000 | 0 | `SSL_connect: SSL_ERROR_SYSCALL` |
| `https://dns.google/dns-query` | **0** | **200** | 160 字节 | 成功 |
| `https://cloudflare-dns.com/dns-query` | **0** | **200** | 160 字节 | 成功 |

解码 `dns.google` 返回的 160 字节应答：`rcode=0`，8 条 A 记录，TTL 282，地址为 `142.251.150.119` 至 `142.251.157.119`，全部是真实 Google 地址。**Mihomo 实际要用的标准 `/dns-query` 接口经代理后可用**，Codex 第 4 条提出的验收缺口已补上。

本地 HTTP 代理入口 `-x 127.0.0.1:7890` 不等于最终使用远端代理：裸 IP 请求仍可能经 Mihomo 规则落到 DIRECT。这解释了当时 IP 端点通过本地代理入口仍失败；最终需要检查连接实际策略链。

### 5.3 指定策略组的语法测试

设备 `CrashCore v1.19.17 -t` 接受含 `#策略组名`、空格和 emoji 的 DoH URL，也兼容 ShellCrash 的 `nameserver: [ $dns_fallback ]` 展开格式。

隔离核心的端到端测试当时未完成：`/dns/query` 返回 `DNS section is disabled`，临时实例随后清理，主核心 PID 未变。此时仅能确认语法通过；实际代理出口在第 7 节才完成验证。

Claude 另澄清初版命令实际带了 `name=www.google.com&type=A`，只是在文档中省略。该澄清及接口格式问题在下一轮一并处理。

<a id="stage-6"></a>

## 6. 2026-09-17 第 5 轮：Codex 确认实施方案

本轮未重新连接设备。接受补充的标准 DoH 可用性记录，方案收敛为：**设备侧仅修改 `dns_fallback`，显式指定谷歌服务组；仓库规则不变。**

三项最终更正：

- 仓库的 `🌌 谷歌服务` 没有显式 DIRECT 成员，`👨‍💻 开发服务` 才有。可复用谷歌组，实施时继续检查设备及嵌套组，不必单为 DNS 新增组。
- 九条前置规则全部加 `no-resolve` 也不是充分修复条件：Twitter 自身还可能解析，已知目标 IP 仍会匹配。因此暂缓，不把域名/IP 拆分设为必做任务；只有修复 DNS 后仍有误匹配才进一步评估。
- 接受 `/resolve` 参数省略的澄清；但 `/dns-query?name=...&type=A` 仍不是标准 GET 格式，标准格式为 `?dns=<base64url DNS 报文>`。新一轮 wireformat 测试已补足这一缺口，不再依赖旧测试下结论。

实施要求是备份、最小变更、检查最终配置与实际 DNS 出口，验证 A/AAAA、新连接分流、国内/LAN 解析、节点启动和服务重启持久性。保留 `direct-nameserver` 等原路径并记录其覆盖边界。

<a id="stage-7"></a>

## 7. 2026-09-17：Claude 实施与服务重启验收

本阶段晚于方案确认、早于独立复验。备份名含 `20260917-143735`，原记录未给出完整实施起止时间。

### 7.1 变更内容

只改 ShellCrash 一个变量，未改动本仓库任何配置：

```sh
# /data/other_vol/ShellCrash/configs/ShellCrash.cfg
dns_fallback='https://cloudflare-dns.com/dns-query#🌌 谷歌服务, https://dns.google/dns-query#🌌 谷歌服务'
```

生成的运行配置从 `nameserver: [ 1.1.1.1, 8.8.8.8 ]` 变为：

```yaml
nameserver: [ https://cloudflare-dns.com/dns-query#🌌 谷歌服务, https://dns.google/dns-query#🌌 谷歌服务 ]
```

`dns_nameserver`、`dns_resolver`、`dns_proxy_server` 保持原值，`direct-nameserver` 仍为 `127.0.0.1`，国内解析路径未动。

**选组依据（采纳 Codex 更正）：** 钉在 `🌌 谷歌服务` 而非 `👨‍💻 开发服务`。核对仓库与设备后确认，谷歌组成员为 `[🇺🇸 美国节点, 🇸🇬 狮城节点, 🇯🇵 日本节点, 🇭🇰 香港节点, 🌐 故障转移]`，其嵌套的区域组与 `🌐 故障转移` 也都不含 `DIRECT`，全链路无直连成员；开发服务组则显式包含 `DIRECT`。

**回滚点：**

```text
/data/other_vol/ShellCrash/configs/ShellCrash.cfg.bak.dnsfix-20260917-143735
/data/other_vol/ShellCrash/config.yaml.bak.dnsfix-20260917-143735
```

当时记录的回退方式是删除新增的 `dns_fallback` 一行并重启服务，恢复默认明文 `1.1.1.1, 8.8.8.8`。这也会恢复原有污染风险；本次没有执行回滚演练。

### 7.2 验收结果

按最初拟定的七项验收目标记录如下；“重启”在本节指服务重启：

| # | 标准 | 结果 |
|---|---|---|
| 1 | `jable.tv`、`www.jable.tv` 不再落入社交网段 | **通过**。`jable.tv` → `172.66.167.218`、`104.20.42.172`（Cloudflare）；`www.jable.tv` → NXDOMAIN 并带 SOA，与可信对照一致 |
| 2 | `assets-cdn.jable.tv` 正常解析 | **通过**。→ `148.113.165.12` 等 OVH 地址，不再是 Facebook 的 `31.13.95.33` |
| 3 | `jable.tv:443` 进入 `🌏 全球加速` | **通过**。`rule=RuleSet,ProxyLite`，链路 `[Sub1]🇭🇰 香港 12 / 🇭🇰 香港节点 / 🌏 全球加速` |
| 4 | `www.google.com` 进入 `🌌 谷歌服务` | **通过**。`rule=RuleSet,Google`，链路 `[Sub1]🇺🇸 美国 01 / 🇺🇸 美国节点 / 🌌 谷歌服务` |
| 5 | `x.com`、`web.telegram.org` 仍进 `🌐 社交平台` | **通过**。分别命中 `RuleSet,Twitter` 和 `RuleSet,Telegram` |
| 6 | 重启后结果稳定 | **通过**。连续两次 `start.sh restart`，`nameserver` 每次都由 `dns_fallback` 正确重新生成，解析与策略组选择均保持 |
| 7 | 运行配置与仓库差异有记录 | **通过**。TUN、IPv6、DNS 的设备侧实际取值已记录在“设备取证”节 |

补充 Codex 要求的验收项：

- **AAAA。** `www.google.com` → `2001:4860:48xx:...`（真实 Google v6），`jable.tv` → `2606:4700:10::...`（Cloudflare v6）。投毒地址 `2001::1` 已消失。
- **DNS 实际出口。** 连接表中 `host=cloudflare-dns.com` 与 `host=dns.google` 的记录带 `specialProxy: "🌌 谷歌服务"`、`rule` 为空，链路为 `[Sub1]🇺🇸 美国 01 / 🇺🇸 美国节点 / 🌌 谷歌服务`。`rule` 为空说明该连接**绕过了规则匹配**，由 `#` 指定的策略组直接拨号，证明固定代理被内核真正遵循。
- **国内与 LAN 解析。** `www.baidu.com` → `183.2.172.177`，`www.taobao.com` → `113.105.156.163`，`miwifi.com` → `192.168.31.1`，均正常；`www.baidu.com` 连接命中 `RuleSet,ChinaDirect` 走 `DIRECT / 🇨🇳 国内服务`。
- **节点启动。** 重启后 `proxy-providers` 正常加载，健康检查有延迟数据，代理连接可用，`proxy-server-nameserver` 保持明文 `223.5.5.5` 未造成启动依赖问题。
- **策略组未被重置。** 两次重启后 `🐟 兜底分流` 仍为 `DIRECT`、`🌌 谷歌服务` 仍为 `🇺🇸 美国节点`，说明未清理 `cache.db` 的决定是对的（清理会把选择重置为首个成员，`兜底分流` 会从 `DIRECT` 变成 `♻️ 自动选择`）。

### 7.3 保留本地直连解析的依据与边界

该缺口在配置层面仍存在；本次已观察的连接未见受影响，尚不能推广为本设备所有运行路径都不会触发。

缺口确认：路由器自身查询本地 dnsmasq 仍返回投毒结果。

```text
nslookup www.google.com 127.0.0.1   -> 185.45.5.35 , 2001::1
nslookup www.google.com 192.168.31.1 -> 185.45.5.35 , 2001::1
```

已观察连接正常的依据如下，结论限于样本及对应网络路径：

1. **连接的目标 IP 由主解析器（DoH）填充。** 连接表中 `www.google.com` 记录为 `dnsMode=fake-ip`、`destinationIP=142.251.154.119`，即干净的 DoH 结果，而非 dnsmasq 的 `185.45.5.35`。这正是第 5.1 节记录的前置含 IP 规则的副作用：它们在 `MATCH` 之前就强制解析并把结果写入 metadata，因此 `direct-nameserver` 在这些连接上根本不会被查询。这一解释限于已观察连接，不能保证每条连接都使用相同解析路径。
2. **LAN 客户端的 53 端口被强制重定向到 Mihomo。** 防火墙链 `shellcrash_dns` 对 `192.168.31.0/24` 的 TCP 和 UDP 53 都执行 `REDIRECT --to-ports 1053`，IPv6 侧有对应的 `shellcrashv6_dns` 链。这些规则为所覆盖客户端的普通 DNS 请求提供重定向；本轮未逐个验证所有客户端路径。上面 `nslookup` 之所以能看到投毒结果，是因为查询发自路由器自身，不经过 `PREROUTING`。

实际落到 `DIRECT / 🐟 兜底分流` 的境外连接（如 `http-intake.logs.us5.datadoghq.com`）目标 IP 为 `34.149.66.165`，属正常 Google Cloud 地址，未见投毒。

现有样本不足以支持继续修改 `direct-nameserver`，本次保留。统一改成境外 DoH 可能影响国内 CDN 的就近解析；若后续出现 DIRECT 域名异常，应按实际解析路径单独诊断。

### 7.4 关闭指定代理的功能验证缺口

此前记录“隔离实例的 `dns:` 段恒不启动，`#` 是否被内核遵循未经实测”。现已查明**那是启动竞态造成的误判**：`/version` 接口在核心完全就绪前即可响应，当时的轮询在 DNS 子系统初始化完成前就发起了查询。本次实施中第二次重启后也复现了同样现象，稍后重查即恢复正常。

`#策略组名` 是否被遵循，现已由生产环境的连接表直接证明（见上文“DNS 实际出口”），该遗留项关闭。

### 7.5 当时保留的事项

- **整机重启持久性未实测。** `dns_fallback` 存放在 `configs/ShellCrash.cfg` 这个持久文件中，且每次 `start` 都由 `clash_modify.sh` 重新生成 `dns:` 段，两次服务重启均已验证该再生路径；但未做整机断电重启。如需完整闭环，可在方便时重启路由器后复查一次 `nameserver` 行。
- **仓库侧 `no-resolve` 仍暂缓**，理由见第 6 节，未作改动。
- 实施期间观察到 ShellCrash 的「服务启动后自动同步」计划任务会自行重启核心，与手动重启可能撞车。本次未做处理，后续如需在设备上批量操作，应先避开该任务。

<a id="stage-8"></a>

## 8. 2026-09-17 约 14:56–15:00：Codex 独立设备复验

于设备时间 14:56–15:00 左右通过 `ssh miwifi` 只读检查并主动发起 DNS/HTTPS 测试，未修改设备设置、切换策略、清理缓存或重启服务。结论：本次抽样确认 DNS 修复和预期分流生效；完整网页功能和整机重启恢复不在本次验证范围。

- **配置与出口：通过。** 持久文件 `configs/ShellCrash.cfg` 和实际 `config.yaml` 均包含两个 `#🌌 谷歌服务` DoH 端点；连接表分别观察到 `cloudflare-dns.com`、`dns.google` 的 `specialProxy=🌌 谷歌服务`，规则字段为空，策略链包含谷歌组。API 递归检查该组可达的 178 个组/节点，未发现 `DIRECT` 成员；当前选择为美国组。
- **DNS：通过。** 对 9 个域名各做两轮 A/AAAA 查询，共 36 次。`jable.tv` 返回 `104.20.42.172`、`172.66.167.218` 及 `2606:4700:10::…`；Google 返回 `142.251.150–157.119` 范围内地址和 `2001:4860:…`；`assets-cdn.jable.tv` 返回 `148.113.165.12`、`51.161.118.150` 等地址，AAAA 为空且状态为 NOERROR。`www.jable.tv` 的 A/AAAA 均为 NXDOMAIN。未出现此前记录的错误地址或 `2001::1`。两轮可能使用缓存，不宣称每次均重新访问上游。
- **分流：通过。** 连接表观察到 jable → `ProxyLite / 🌏 全球加速`，Google → `Google / 🌌 谷歌服务`，X → `Twitter / 🌐 社交平台`，Telegram → `Telegram / 🌐 社交平台`，百度 → `ChinaDirect / DIRECT / 🇨🇳 国内服务`。
- **访问：有明确边界。** 百度返回 HTTP 200；Google、X、Telegram 在低并发复测中均为 curl 退出码 0、HTTP 200。jable 为退出码 0、HTTP 403，只能证明 HTTPS 有响应和分流正确，不能宣称页面可正常浏览。首次为抓取连接采用限速时，Google/X 下载超时，Telegram 一次 SSH 握手被重置；取消限速并降低并发后通过，未据此前测试干扰判断设备故障。
- **国内/LAN：通过抽样。** 百度、淘宝 A/AAAA 均有结果；`miwifi.com` 返回 `192.168.31.1` 和 `fd00:6969:6969::1`。IPv4/IPv6 的 PREROUTING 均将 TCP/UDP 53 引入 ShellCrash DNS 链，链内对所列 LAN 网段重定向到 1053；这是规则静态核对，未冒充独立 LAN 客户端抓包验证。
- **稳定性边界：** 检查期间核心 PID 始终为 `32291`。设备 uptime 当时约 16–18 分钟；本轮没有发起或完整观察整机/服务重启，因此重启测试仍引用 Claude 的记录，不能仅凭 uptime 补签整机重启验收。

当前无需追加仓库规则改动。第 7.3 节关于 `direct-nameserver` 的结论应限于已观察连接：它们正常不能证明所有 DIRECT 域名和所有客户端路径永远不受本地解析影响；保留现设置，但避免将抽样成功写成全局保证。

<a id="stage-9"></a>

## 9. 2026-09-17 约 15:05–15:10：Claude 补充验收与重启调查

来源：Claude，经 `ssh miwifi` 只读取证，未修改设备配置，未重启。本节补充质询响应性质与整机重启后的恢复证据；真实浏览器可用性仍未验收。整机重启记录来自事后取证，不是本轮主动执行的重启测试。

### 9.1 jable 的 HTTP 403 为 Cloudflare 质询响应

第 8 节把 jable 的 403 记为「不能宣称页面可正常浏览」。查明该 403 的性质如下：

```text
HTTP/2 403
cf-mitigated: challenge
server: cloudflare
cf-ray: a3c6487e781bc849-HKG
```

`cf-mitigated: challenge` 标识 Cloudflare 质询页面。本次普通 curl 和设置浏览器 User-Agent 的请求均返回 403；这解释了测试结果，但不能推广为所有命令行请求必然失败，也不能据此确认真实浏览器能够正常访问。`claude.ai` 当时也返回相同标记。参见 [Cloudflare 质询响应说明](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/)。

`cf-ray` 的 `HKG` 后缀表示处理请求的 Cloudflare 数据中心，不等于客户端或代理出口位于香港。实际代理出口仍以第 8 节的连接策略链为依据。参见 [Cloudflare Cf-Ray 说明](https://developers.cloudflare.com/fundamentals/reference/http-headers/#cf-ray)。

### 9.2 整机重启后恢复的事后证据

第 7 节和第 8 节都把「整机重启恢复」列为未实测。复核 uptime 时发现，设备在本次修复之后出现过一次整机启动，随后配置仍生效。这支持重启后恢复，但不能区分断电启动、软件重启或其他重置。

证据链：

| 时刻 | 事件 | 证据 |
|---|---|---|
| 14:37:58 | `dns_fallback` 写入持久配置 | `configs/ShellCrash.cfg` 的 mtime 为 `2026-09-17 14:37:58`，此后未被改写 |
| 14:40:19 | 设备整机启动（回推） | `/proc/uptime` 为 `1754.39`，在 `15:09:33` 回推得开机时刻；`/etc` 为 ramfs，`/etc/rc.d/S9*` 软链时间戳集中在 14:40；`/tmp/ShellCrash/ShellCrash.log` 被清空，最早记录为 14:41:46 |
| 14:41:49 | ShellCrash 开机自启成功 | 日志「ShellCrash服务已启动！」；`/etc/rc.d/S99shellcrash → ../init.d/shellcrash`，`START=99` |
| 14:49 起持续 | 运行配置含固定代理的 DoH | 多次复查 `nameserver: [ https://cloudflare-dns.com/dns-query#🌌 谷歌服务, https://dns.google/dns-query#🌌 谷歌服务 ]` |

持久性链条完整：`configs/ShellCrash.cfg` 位于 `/data/other_vol`（ubifs 持久分区，非 tmpfs），写入时间早于开机时间且此后未被改写，说明当前生效值确实是穿过一次整机启动后由持久文件重新生成的。`/etc` 虽为 ramfs，但开机自启软链能被重建，链路成立。

这补足了第 7、8 节当时缺少的整机重启后恢复证据；两节保留原时点的验证范围。没有新增受控断电测试，也未查明重启原因。

### 9.3 重启原因未知；同期 403 与直连的关系尚属推断

该次整机启动**不是有意发起的**，原因未能查明：`dmesg` 环形缓冲区已滚过开机横幅（最早时间戳为 `[25.9]`），设备上不存在 `reboot_reason` 一类记录，`logread` 在该时段无内容。时间上它紧邻当时执行的 `start.sh restart`，但没有证据能确认因果，不作结论。

潜在影响需要记录：约 14:40:19 开机至 14:41:49 ShellCrash 就绪之间存在服务恢复窗口；实际中断起点及窗口内流量路径没有抓包记录。同期 WSL 中的 Claude Code 出现连续 403 并反复要求重新授权。当时的直连对照请求返回拒绝响应：

```text
路由器自身直连（不经 PREROUTING 重定向）
api.anthropic.com -> HTTP 403
出口 IP 已省略；loc=CN
```

经代理则正常：`api.anthropic.com` 未带凭证时返回 401 及正确的 JSON 错误体，`console.anthropic.com/v1/oauth/token` 返回 400 及正确错误，出口 `loc=US`、`colo=LAX`。

结论：当前直连与代理对照支持“重启窗口内部分请求改走直连”的假设，但缺少当时失败请求的出口、连接日志及完整响应，不能认定这是那批 403 的唯一或主要原因，也不能排除认证或服务端因素。该假设不影响已验证的 DNS 修复结论。

**运维提示：** 再次出现 403 时，应从发生问题的客户端核对实际连接策略、出口及响应内容。`/cdn-cgi/trace` 的 `loc` 可辅助识别出口地区，不能单凭 `loc=CN` 判定代理失效，也不能代替认证状态检查。核心启动早期的 `/version` 可先于 DNS 等子系统就绪；初始化期间的 API 错误应在服务就绪后重查。
