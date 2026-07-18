# ShellCrash 方案验收记录

> 状态：代码与离线/CI 验证已完成，路由器真实运行验收仍待完成。本文从本节起为当前结论；下方“原始讨论”仅保留审计上下文，不是现行操作指南。

## 当前验收矩阵（2026-07-18）

| 项目 | 结论 | 证据或剩余工作 |
|---|---|---|
| 公共策略、平台运行参数、设备私密参数所有权 | 已实现且已验证 | `clash/config.yaml` 是公共策略源；路由器模板不接管 DNS、TUN、sniffer、controller 或防火墙；真实 URL 仅由本地 `providers.env` 注入 |
| 单/双订阅模板与秘密保护 | 已实现且已验证 | Windows 与路由器均有单/双生成物；生成、占位符、秘密扫描和策略引用检查通过 |
| Mihomo 与 ShellCrash 配置校验 | 已实现且已验证 | 四份配置通过 CI 锁定的 ShellCrash Mihomo `-t`；两个路由器模板通过 ShellCrash 官方覆写后再次通过 `-t` |
| 事务部署、备份、回滚、幂等和已有配置保护 | 已实现且已验证 | `test_deploy_shellcrash.py` 覆盖连续部署、失败不覆盖、备份恢复和已有配置但无核心时拒绝覆盖；真实路由器回滚演练仍列在下方待办 |
| 全新安装由 ShellCrash 获取核心 | 仍待完成 | 代码和模拟事务已验证；目标路由器目前没有核心和旧配置，仍需首次真实部署与进程检查 |
| 米家兼容 | 仍待完成 | 已核对 ShellCrash 官方 fake-IP/sniffer 基线，未预置未经日志验证的广泛域名；仍需真实设备通信与日志验收 |
| IPv4、IPv6、启动和重启 | 仍待完成 | 必须在家中网络可维护时完成出口、透明分流、服务启动和路由器重启测试 |
| 首次部署、定时任务、诊断与回滚文档 | 已实现，真实执行待验收 | 现行指南为 `clash/shellcrash/README.md`；定时任务必须等首次部署和重启均通过后再启用 |
| `base/` + `platforms/` + `groups/` + `generated/` 全目录重构 | 被当前架构替代 | 采用更小的 `clash/config.yaml` 单一策略源，由生成器派生 Windows 单订阅和 ShellCrash 单/双模板 |
| 路由器默认删除 AdBlock、使用 lightweight、预置广泛米家域名 | 已过时/错误 | 当前规则规模已通过目标 Mihomo 校验；没有设备日志证明需要删规则或加入广泛米家清单，不做预防性功能削减 |
| `config-router-public.yaml` 与“自定义配置文件”接入 | 被当前架构替代 | 现行文件为 `config-router*.template.yaml`，经本地注入后写入 `yamls/config.yaml`，再由 ShellCrash 官方覆写和启动流程接管 |

## 路由器待完成清单

- [ ] 使用单订阅执行首次部署，确认 ShellCrash 自动取得核心并出现 `CrashCore` 进程。
- [ ] 冒烟验证国内、国外、Steam 下载、广告域名和米家设备通信，并核对连接日志中的实际策略。
- [ ] 分别检查 IPv4 与 IPv6 公网出口；IPv6 若绕过透明代理，先在路由器本地决定接管或限制方案。
- [ ] 重启路由器，确认 ShellCrash 使用最后一次成功配置自动恢复服务。
- [ ] 在可控窗口演练一次失败回滚，确认 `config.yaml.bak.proxy-config` 可恢复且 DNS/网络不中断。

在以上条目全部完成前，本文保留且不得宣告 ShellCrash 整体验收完成。最终完成后，再依据是否仍有 README、实施计划和 Git 历史未覆盖的独特设计依据，决定将本文精简为短 ADR 或删除。

---

# 原始讨论（历史材料，禁止按其中路径直接操作）

可以。先明确一点：**重新设计不是因为你当前仓库“不能用”，而是它现在更像“公开模板”，还不是一个真正可自动同步的跨设备配置源。**

你现在的实际流程是：

```text
GitHub 公共 config.yaml
        ↓
Clash Verge Rev 导入
        ↓
手工替换 proxy-providers.url
        ↓
本地运行
```

这在单个 Windows 客户端上没问题，但到了 ShellCrash，就会暴露出三个结构性问题。

# 为什么要重新设计

## 1. 公共配置与私密运行参数混在同一层

你的完整配置中同时包含：

* 端口、DNS、策略组、规则逻辑；
* `proxy-providers`；
* 机场订阅 URL 占位符；
* Windows 与路由器共用的运行参数。

其中前半部分可以公开，真实订阅 URL 不能公开。

现在的做法只能把真实 URL 留在客户端本地手工修改。结果是：GitHub 上的文件不是最终可运行配置，而只是模板。

这会导致：

* 新设备首次导入必须手工修改；
* 远程更新可能覆盖本地订阅 URL；
* Windows 与 ShellCrash 的实际配置发生漂移；
* 很难判断某个问题来自仓库版本还是本地修改版本。

Mihomo 的 `proxy-providers` 本身支持独立 URL、独立缓存路径、更新周期和健康检查，因此它天然适合将“节点来源”和“配置逻辑”分层管理。([Metacubex][1])

## 2. 平台差异没有隔离

Windows 客户端和路由器的需求不同。

例如：

| 项目                    | Windows | ShellCrash 路由器    |
| --------------------- | ------- | ----------------- |
| `external-controller` | 可监听本机   | 需考虑 LAN 访问和安全     |
| 透明代理端口                | 通常不用    | 由 ShellCrash 接管   |
| DNS 劫持                | 客户端自身处理 | 与 dnsmasq、局域网设备相关 |
| IPv6                  | 可按客户端设置 | 会影响全屋设备           |
| fake-ip               | 影响单机    | 影响米家、电视、Switch    |
| 规则规模                  | PC 内存充足 | 路由器内存有限           |
| provider 健康检查         | 影响较小    | 会增加路由器资源占用        |

把两者放在同一个最终配置里，必然会出现大量条件差异或手工调整。

## 3. 当前更新方式不具备幂等性

理想的自动更新应该满足：

> 无论执行多少次更新，结果都一致，而且不会抹掉私密参数。

你现在从 GitHub 重新导入 `config.yaml` 后，还要再次手改订阅链接。这意味着更新不是幂等的。

对于 ShellCrash，这尤其麻烦，因为它可能按计划重新生成或覆盖配置。

---

# 推荐的重新设计方案

我建议采用：

> **公共组件仓库 + 平台模板 + 私密本地注入**

不是维护一份完全相同的最终 YAML，而是维护一套统一的“源配置”。

## 目录结构

```text
proxy-config/
├── README.md
├── clash/
│   ├── base/
│   │   ├── common.yaml
│   │   ├── dns.yaml
│   │   ├── sniffer.yaml
│   │   └── geodata.yaml
│   │
│   ├── platforms/
│   │   ├── windows.yaml
│   │   └── router.yaml
│   │
│   ├── groups/
│   │   └── proxy-groups.yaml
│   │
│   ├── providers/
│   │   ├── providers.example.yaml
│   │   └── README.md
│   │
│   ├── rules/
│   │   ├── main.yaml
│   │   ├── lightweight.yaml
│   │   ├── mijia-direct.yaml
│   │   └── adblock-optional.yaml
│   │
│   ├── generated/
│   │   ├── config-windows.yaml
│   │   └── config-router-public.yaml
│   │
│   └── scripts/
│       ├── build.py
│       └── validate.py
│
├── quantumult-x/
│   ├── config.conf
│   └── rules/
│
└── shared/
    ├── direct-domains.txt
    ├── proxy-domains.txt
    ├── reject-domains.txt
    └── mijia-domains.txt
```

这里真正提交到 GitHub 的仍然全是公开内容。

---

# 各层职责

## 一、`base/`：公共运行逻辑

保存 Windows 和路由器都适用的部分：

```yaml
mode: rule
allow-lan: true
log-level: warning

profile:
  store-selected: true
  store-fake-ip: true
```

这里不放：

* 真实订阅地址；
* 设备固定 IP；
* 管理密码；
* 平台特有端口；
* ShellCrash 专属参数。

## 二、`platforms/`：平台差异

### `windows.yaml`

例如：

```yaml
mixed-port: 7897
external-controller: 127.0.0.1:9090
ipv6: false
```

### `router.yaml`

例如：

```yaml
mixed-port: 7897
external-controller: 127.0.0.1:9090
ipv6: false

dns:
  enhanced-mode: fake-ip
  fake-ip-filter:
    - "+.lan"
    - "+.local"
    - "+.mijia.com"
    - "+.miot.com"
```

这里的域名清单应当经过实际日志验证，不应一次性假定完整。Mihomo 官方快速配置示例本身也把米家相关流量视为需要单独处理的场景。([Metacubex][2])

路由器版还可以：

* 移除或关闭 AdBlock；
* 降低健康检查频率；
* 禁用不必要的嗅探；
* 减少 rule-provider；
* 单独维护米家直连规则。

## 三、`providers/`：只保存结构，不保存秘密

仓库中保存：

```yaml
proxy-providers:
  Sub:
    type: http
    url: "__SUB_URL_1__"
    path: ./providers/sub1.yaml
    interval: 86400
    health-check:
      enable: true
      lazy: true
      url: https://www.gstatic.com/generate_204
      interval: 1800

  Sub2:
    type: http
    url: "__SUB_URL_2__"
    path: ./providers/sub2.yaml
    interval: 86400
    health-check:
      enable: true
      lazy: true
      url: https://www.gstatic.com/generate_204
      interval: 1800
```

这些字段都是 Mihomo 原生支持的 provider 配置能力。([Metacubex][1])

实际运行时再把：

```text
__SUB_URL_1__
__SUB_URL_2__
```

替换成真实 URL。

这样真实订阅永远不进入公开 Git。

## 四、`rules/`：把规则拆成可选模块

建议至少分成：

```text
main.yaml
lightweight.yaml
mijia-direct.yaml
adblock-optional.yaml
```

### Windows 版

可以使用：

```text
main
+
adblock
+
完整 ChinaDirect
```

### 路由器版

初期使用：

```text
lightweight
+
mijia-direct
```

以后实测内存足够，再逐步加入广告规则。

Mihomo 的 `rule-providers` 支持 HTTP、文件和内联三种来源，并可以分别指定 domain、ipcidr 或 classical 行为，因此这种模块化拆分是原生支持的。([Metacubex][3])

---

# 最关键的设计：生成最终配置

不要要求客户端自己理解多个 YAML 文件。

由仓库脚本生成两个最终文件：

```text
config-windows.yaml
config-router-public.yaml
```

## 生成逻辑

```text
common.yaml
+ windows.yaml
+ proxy-groups.yaml
+ providers.example.yaml
+ main rules
+ adblock
= config-windows.yaml
```

```text
common.yaml
+ router.yaml
+ proxy-groups.yaml
+ providers.example.yaml
+ lightweight rules
+ mijia direct
= config-router-public.yaml
```

这两个仍然保留订阅占位符。

---

# 私密参数如何注入

有三种方案。

## 方案 A：设备本地替换，占用最低

这是目前最适合你的。

路由器本地保存：

```text
/data/other_vol/ShellCrash/private/providers.env
```

内容：

```sh
SUB_URL_1='https://真实订阅1'
SUB_URL_2='https://真实订阅2'
```

更新脚本执行：

```text
下载 config-router-public.yaml
        ↓
替换 __SUB_URL_1__ 和 __SUB_URL_2__
        ↓
写入 ShellCrash 最终 config.yaml
        ↓
配置校验
        ↓
重启 Mihomo
```

Windows 可继续在 Clash Verge Rev 中手改，或者也使用一个本地生成脚本。

优点：

* 最简单；
* 不需要 Git；
* 不泄露订阅；
* 路由器只需 curl、sed；
* 公共配置更新不会丢失真实订阅。

## 方案 B：私有 GitHub 仓库

建立：

```text
proxy-config-private
```

里面只保存：

```text
providers.secret.yaml
```

不推荐作为第一阶段，因为路由器访问私有 GitHub 需要 token，token 本身又要安全保存。

## 方案 C：GitHub Actions 生成私密配置

通过 GitHub Secrets 注入订阅，然后生成私密产物。

技术上最自动化，但配置 URL 的访问控制会更复杂，不适合你当前阶段。

所以我的建议是：

> **公共仓库生成模板，真实 URL 留在每台设备本地。**

---

# ShellCrash 应如何接入

重构完成后，ShellCrash 不应把完整模板当“提供者”添加。

更合适的方式是：

1. 下载：

   ```text
   clash/generated/config-router-public.yaml
   ```
2. 本地替换订阅占位符；
3. 写入 ShellCrash 的实际配置路径；
4. 让 ShellCrash 使用自定义配置文件；
5. ShellCrash 只负责：

   * Mihomo 运行；
   * 透明代理；
   * 防火墙；
   * DNS 劫持；
   * 开机启动；
   * 服务管理。

也就是说：

```text
GitHub：配置源代码
ShellCrash：运行管理器
Mihomo：执行引擎
设备本地：私密参数
```

职责会非常清晰。

---

# 重新设计后的好处

## 1. 真正实现一处修改，多端同步

你修改：

```text
shared/mijia-domains.txt
```

就可以同时生成：

* Mihomo Windows 规则；
* Mihomo 路由器规则；
* Quantumult X 规则。

不再手工复制三份。

## 2. 订阅 URL 不再被更新覆盖

GitHub 下载下来的模板始终包含占位符，本地生成过程每次都会重新注入真实 URL。

所以：

```text
GitHub 更新
```

不会再导致：

```text
订阅地址恢复成 example.com
```

## 3. Windows 和路由器可以独立优化

Windows 保留完整规则和广告过滤。

路由器使用轻量版：

* 少规则；
* 少 provider；
* 较低健康检查频率；
* 米家优先兼容；
* 更低内存占用。

两者仍共享同一套逻辑来源，不是两套互不相关的配置。

## 4. 更容易排错

出现问题时可以明确判断：

```text
公共基础配置问题
平台适配问题
规则模块问题
私密参数问题
ShellCrash运行问题
```

而不是面对一份数千行 YAML 猜测。

## 5. 可以自动校验

构建脚本可以在提交前检查：

* YAML 语法；
* 重复策略组；
* 规则引用不存在；
* Provider 名称不一致；
* 占位符是否遗漏；
* 是否误提交真实订阅；
* 生成文件是否发生变化。

## 6. 更容易回滚

每次生成的最终配置都对应一个 Git commit。

如果某次更新导致路由器异常，可以直接退回上一个生成版本。

## 7. 更安全

公开仓库不会含：

* 机场 token；
* ShellCrash secret；
* 管理密码；
* 家庭设备 IP；
* 私有域名。

同时又不妨碍自动部署。

---

# 我建议采用的最终结构

最适合你的不是“一份最终 YAML 所有设备完全共用”，而是：

```text
一个公共配置源
        │
        ├── Windows 最终配置
        ├── Router 最终配置
        └── Quantumult X 最终配置
```

再配合：

```text
每台设备本地私密参数
```

这仍然是“一套配置体系”，但避免强迫不同平台使用完全相同的运行参数。

# 推荐实施顺序

第一阶段只做最小改造：

1. 保留现有 `config.yaml` 不动；
2. 新增 `config-router.template.yaml`；
3. 路由器版删除 AdBlock；
4. 增加米家直连模块；
5. 将订阅 URL 改成占位符；
6. 新增一个本地生成脚本；
7. 生成 ShellCrash 可直接运行的最终配置。

等路由器稳定后，再把 Windows、Quantumult X 和公共规则全部纳入统一构建流程。

这样不会一次性重写整个仓库，也不会影响你当前可用的 Clash Verge Rev 配置。

[1]: https://wiki.metacubex.one/en/config/proxy-providers/?utm_source=chatgpt.com "proxy-providers configuration - mihomo docs"
[2]: https://wiki.metacubex.one/en/example/conf/?utm_source=chatgpt.com "Quick configuration - mihomo docs"
[3]: https://wiki.metacubex.one/en/config/rule-providers/content/?utm_source=chatgpt.com "rule-providers configuration - mihomo docs"


Cluade Fable审核意见：
我读完了 ChatGPT 的方案和仓库现状。先说结论：方案的诊断有一半已经过时，全套重构没有必要；真正值得做的只有它自己列的"第一阶段"——加一份路由器配置和一个本地注入脚本，其余照旧。

方案里已经被现有仓库覆盖的部分
ChatGPT 的方案像是基于一个"单文件模板仓库"的假设写的，但这个仓库其实早就完成了它提议的大部分分层：

"共享规则源 + 多端生成"：方案里的 shared/*.txt 想法，就是现有的 rules/ + build_rules.py，已经在同时生成 Clash rulesets 和 QX snippet。
"秘密不进 Git"：bootstrap.conf 已 gitignore，config.yaml 的订阅 URL 已是 token=replace-me 占位符。
"可自动校验"：.github/workflows/ 里已有 validate、hygiene、acceptance 三条 CI。
方案提议的 base/ + platforms/ + groups/ + generated/ 全套拆分反而会带来实际代价：现有 rule-provider 的 GitHub Raw URL 全部失效（所有设备要重新配置）、README 和 CI 全部重写、build 脚本重写——换来的只是"结构更好看"。这和你之前定下的"不要过度开发"原则直接冲突。

真正的缺口（ShellCrash 带来的新需求）
只有两个：

没有路由器版配置。 Windows 版的 config.yaml 直接给 ShellCrash 用不合适：external-controller 监听、fake-ip filter（米家/局域网设备）、健康检查频率、规则规模这些确实需要路由器专属调整。
路由器更新不幂等。 路由器定期拉取公共配置后，需要自动把占位符换成真实订阅，而不是手工改。
我建议的最小改造
新增 clash/config-router.yaml：从现有 config.yaml 派生，保留占位符，只改路由器相关的差异项（fake-ip-filter、健康检查间隔、按需精简规则）。
新增一个跑在路由器上的小脚本（curl 下载 → sed 替换占位符 → mihomo 校验 → 写入 ShellCrash 配置路径 → 重启），真实订阅 URL 存在路由器本地一个 env 文件里，即方案里的方案 A——这点 ChatGPT 说得对，方案 B/C（私有仓库 / Actions Secrets）在这个阶段都是多余的。
两个文件，不动现有任何结构，Windows 和 QX 完全不受影响。等路由器跑稳了、发现两份 Clash 配置维护起来真的重复了，再考虑是否值得引入构建合并——而不是现在预先重构。

另外提一句：这份方案文件放在 quantumultx/ 目录下有点错位，它讲的是 Clash/ShellCrash；如果要留档建议移到仓库根目录或 docs/。

需要的话我可以直接把 config-router.yaml 和路由器更新脚本做出来。
