# 共享规则源

> 返回 [总览](../README.md)。下文仓库命令均在仓库根目录执行。

`rules/` 是仓库自维护规则的唯一编辑入口，由 `scripts/build_rules.py` 生成三端格式：

| 产物 | 客户端 |
|---|---|
| `mihomo/rules/*.yaml` | Clash Verge Rev、ShellCrash（rule-provider） |
| `quantumultx/filter/repo.snippet` | Quantumult X（`[filter_remote]`） |
| `loon/rules/*.list` | Loon（`[Remote Rule]`，`geoip_cn.list` 为手工维护） |

## 日常维护

日常使用中，你的操作只有以下几步：

```bash
# 1. 编辑规则文件（见下节"添加自定义规则"）
vim rules/ai_extra.yaml

# 2. 重新生成客户端专用文件
python3 scripts/build_rules.py

# 3. 生成 ShellCrash 路由器公开策略模板
python3 scripts/build_router_config.py

# 4. 可选：校验生成结果是否正确（返回 0 表示通过）
python3 scripts/build_rules.py --check
python3 scripts/build_router_config.py --check
python3 tests/test_deploy_shellcrash.py
python3 tests/test_rule_provider_scope.py

# 轻量检查外部规则、QX 脚本和图标，不会保存下载内容
python3 scripts/check_remote_resources.py --mode light

# 5. 推送到 GitHub
git add .
git commit -m "feat: 添加 xxx 规则"
git push
```

推送完成后：

| 客户端 | 同步方式 | 生效时间 |
|---|---|---|
| Quantumult X | 自动拉取 `quantumultx/filter/repo.snippet` | 按配置刷新（示例间隔 24h），成功加载后生效；也可手动触发「更新资源」 |
| Loon | 自动拉取 `loon/rules/*.list` | 按 Loon 的资源刷新生效；也可在 App 中手动更新 |
| Clash / Mihomo | 自动拉取 `mihomo/rules/*.yaml` | 按配置刷新（示例间隔 24h），成功加载后生效；也可手动触发 Provider 刷新 |
| ShellCrash / Mihomo | 路由器本地部署任务拉取并注入 `config-router.template.yaml` | 按本地任务计划，或手动运行部署脚本 |

**主配置与远程资源内容的同步边界**

上表说明已配置资源或部署任务的更新方式，不代表推送任意文件都会更新设备。按本仓库的本地导入方式使用 QX、Clash Verge Rev 时，还需区分：

| 改动类型 | Quantumult X | Clash Verge Rev（Windows） | ShellCrash（路由器） |
|---|---|---|---|
| 已引用的远程规则内容 | 资源成功刷新后加载；引用的策略组须已存在 | 对应 rule-provider 成功刷新后加载 | 对应 rule-provider 成功刷新后加载 |
| 策略组名称、候选、类型和测速参数 | 更新本地配置的 `[policy]` | 编辑 Verge 中的配置副本，或重新导入对应完整配置 | 由已配置的部署任务成功应用策略模板；否则手动执行部署流程 |
| 增删资源引用、调整主配置中的规则映射 | 更新 `[filter_remote]` 资源行及 `force-policy`，或相应 `[filter_local]` | 更新配置副本中的 `rule-providers`、`rules` 等定义 | 同上，仅限策略模板管理的部分 |
| 其他本地主配置 | `[general]`、`[dns]`、`[rewrite_remote]`、`[task_local]` 等条目需按修改内容手动同步；已有远程内容仍按各自机制更新 | 主配置运行参数及 Verge 本地覆写分别按实际来源修改 | ShellCrash 本地 DNS、TUN、sniffer、任务及覆写设置按各自部署说明处理，不由策略模板统一覆盖 |

手动同步时只改对应条目，保留 QX 的订阅、MitM 私密材料及其他本地设置；Windows 重新导入前也需保留或重新填写本地订阅及必要设置。设备已保存的组选择不保证随候选顺序变化而重置；原候选被删除时，需检查客户端实际回退到哪一项。

远程资源轻量巡检每周由 GitHub Actions 自动执行：它只检查实际配置依赖，使用有限并发、超时和每项前 4 KiB 内容识别 404、HTML 错误页及错误图标类型，不 clone 上游仓库，也不把响应写入 Git。手工触发 `Remote Resources` workflow 时可选择 `full`，对 Clash 规则执行完整 YAML / 文本结构检查。失败日志会隐藏 URL 查询参数，避免泄漏可能存在的 token。

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
- `mihomo/rules/my_service.yaml` — Clash rule-provider 格式
- `quantumultx/filter/repo.snippet` — QX filter 格式（整个文件重新生成）
- `loon/rules/my_service.list` — Loon 规则列表，不含策略；还需在 `loon/bootstrap.example.conf` 的 `[Remote Rule]` 中加一行，`policy=` 与 `qx_policy` 相同

Loon 不支持 `domain_regex`，规则源里出现该类型时，生成器会直接报错。
