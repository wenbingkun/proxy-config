# Proxy Config

统一管理 **Quantumult X**（iPhone / iPad）和 **Clash / Mihomo**（Windows）代理配置的 Git 仓库。

修改规则后只需 `git push`，所有设备在下次刷新周期内自动同步，无需手动操作。

---

## 目录

- [快速上手](#快速上手)
  - [Quantumult X（iPhone / iPad）](#quantumult-xiphone--ipad)
  - [Clash / Mihomo（Windows）](#clash--mihomowindows)
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

---

**后续更新（自动，无需操作）**

仓库中的规则文件（`quantumultx/filter_remote.snippet`）已在 bootstrap 中配置为远程资源：

```ini
[filter_remote]
https://raw.githubusercontent.com/wenbingkun/proxy-config/main/quantumultx/filter_remote.snippet, tag=仓库自定义规则, update-interval=86400, enabled=true
```

当你修改 `rules/` 下的规则并 `git push` 后，QX 会在下次自动更新周期（每 24 小时）拉取最新规则。你也可以在 QX 中手动触发「更新资源」强制立即刷新。

---

### Clash / Mihomo（Windows）

Clash 采用 **rule-providers** 架构，规则文件托管在 GitHub，客户端定期自动拉取。

**第一次配置（仅需一次）**

**第 1 步：下载主配置文件**

将仓库中的 `clash/config.yaml` 下载到本机，放置在 Clash 的配置目录中（通常为 `%USERPROFILE%\.config\clash\` 或 Clash 客户端指定的目录）。

**第 2 步：填写机场订阅链接**

打开 `config.yaml`，找到 `proxy-providers` 部分，将占位符替换为你的真实订阅：

```yaml
proxy-providers:
  Sub:
    url: "https://your-subscription-url.com/subscription.yaml?token=your-token"
```

> 此文件保存在本地，不要将真实订阅链接提交到 Git。

**第 3 步：在 Clash 客户端中加载配置**

重启 Clash 或在客户端界面中选择该配置文件使其生效。

---

**后续更新（自动，无需操作）**

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

当你修改 `rules/` 下的规则并 `git push` 后，Clash 会在下次自动更新周期（每 24 小时）拉取最新规则集。你也可以在 Clash 面板中手动触发 Provider 刷新。

---

**可选：让 Clash 主配置也随 git push 自动更新**

如果你希望代理分组、DNS 设置等主配置变更也能自动同步到所有 Clash 设备，可以在 Clash 客户端中以 **远程订阅 URL** 方式加载配置，而非本地文件：

```
https://raw.githubusercontent.com/wenbingkun/proxy-config/main/clash/config.yaml
```

配置好后，Clash 会定期从 GitHub 拉取最新的 `config.yaml`（需确保仓库为 public）。每次 `git push` 更新配置后，所有设备将在刷新周期内自动同步。

---

## 日常维护

日常使用中，你的操作只有以下几步：

```bash
# 1. 编辑规则文件（见下节"添加自定义规则"）
vim rules/ai_extra.yaml

# 2. 重新生成客户端专用文件
python3 scripts/build_rules.py

# 3. 可选：校验生成结果是否正确（返回 0 表示通过）
python3 scripts/build_rules.py --check

# 4. 推送到 GitHub
git add .
git commit -m "feat: 添加 xxx 规则"
git push
```

推送完成后：

| 客户端 | 同步方式 | 生效时间 |
|---|---|---|
| Quantumult X | 自动拉取 `filter_remote.snippet` | 下次刷新（最长 24h），或手动触发「更新资源」 |
| Clash / Mihomo | 自动拉取 `clash/rulesets/*.yaml` | 下次刷新（最长 24h），或手动触发 Provider 刷新 |

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

---

## 目录结构

```
proxy-config/
│
├── rules/                          # 共享规则源（客户端无关）
│   ├── local_rules.yaml            # 规则清单：ID、策略名映射
│   ├── ai_extra.yaml               # AI 服务补充规则
│   ├── crypto_extra.yaml           # 加密货币补充规则
│   ├── collaboration_extra.yaml    # 商务协作补充规则
│   ├── zoom.yaml                   # Zoom 规则
│   ├── social_media.yaml           # 社交平台补充规则
│   ├── crunchyroll.yaml            # Crunchyroll 规则
│   ├── dev_extra.yaml              # 开发服务补充规则
│   ├── stack_overflow.yaml         # Stack Exchange 规则
│   ├── speedtest.yaml              # 网络测速规则
│   ├── game_extra.yaml             # 游戏平台补充规则
│   └── local_network.yaml          # 局域网 / 本地直连规则
│
├── quantumultx/                    # Quantumult X 客户端层
│   ├── bootstrap.example.conf      # bootstrap 模板（提交到 Git）
│   ├── bootstrap.conf              # 本地实际配置（gitignore，含私密信息）
│   ├── filter_remote.snippet       # 由 build_rules.py 生成，QX filter 格式
│   └── rewrite_remote.snippet      # QX 自定义 rewrite 规则片段
│
├── clash/                          # Clash / Mihomo 客户端层
│   ├── config.yaml                 # Clash 主配置（含 rule-providers 引用）
│   └── rulesets/                   # 由 build_rules.py 生成的 rule-provider 文件
│       ├── ai_extra.yaml
│       ├── crypto_extra.yaml
│       └── ...（其余同 rules/ 中的规则集）
│
├── scripts/
│   └── build_rules.py              # 规则构建脚本
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

Quantumult X 本地持有一份轻量 bootstrap.conf，只包含私密信息和远程资源入口。规则、重写等内容全部通过远程 snippet 加载，GitHub 更新后 QX 自动拉取，本地证书不受任何影响。

**原则三：rule-providers（Clash）**

Clash 主配置只定义代理分组和规则引用结构，具体规则内容通过 rule-providers 从 GitHub Raw 动态拉取。修改规则无需改动主配置，push 后自动生效。

**原则四：规则单源维护**

`rules/` 目录是唯一的规则编辑入口。`build_rules.py` 负责将其转换为各客户端所需的格式，确保两端规则逻辑始终一致，不会出现 QX 和 Clash 规则各自为政的情况。

### 更新流程

```
编辑 rules/*.yaml
       │
       ▼
python3 scripts/build_rules.py
       │
       ├── 生成 clash/rulesets/*.yaml
       └── 生成 quantumultx/filter_remote.snippet
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
| `*.p12` / `*.pem` / `*.crt` / `*.key` | MitM 私钥和证书 |
| 任何真实的订阅 token | 机场账号安全 |
| Cookie、API Key | 个人隐私 |

以上均已在 `.gitignore` 中排除。仓库中只保留：

- `bootstrap.example.conf`：去除所有私密信息的模板，用于首次配置参考
- `config.yaml`：订阅链接以 `https://example.com/...?token=replace-me` 占位

在新设备上首次配置时，只需基于模板填写本地私密信息，后续规则更新完全自动化，无需再次操作。
