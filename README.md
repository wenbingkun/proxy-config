# Proxy Config

自用代理配置仓库：一份共享规则源，生成 **Quantumult X**、**Loon**、**Surge**、**Mihomo**（Clash Verge Rev / ShellCrash）四条线的客户端文件。仓库只放可公开的配置逻辑，订阅、MitM 证书等私密信息只留在设备本地。

## 客户端

| 客户端 | 设备 | 加载方式 | 文档 |
|---|---|---|---|
| Quantumult X | iPhone / iPad | 本地 bootstrap + 远程 snippet | [quantumultx/](quantumultx/README.md) |
| Loon（试点） | iPhone | 本地 bootstrap + 远程规则 / 插件 | [loon/](loon/README.md) |
| Surge（试点） | iPhone | 托管配置 + 本地分离配置 + 模块 | [surge/](surge/README.md) |
| Clash Verge Rev | Windows | 本地主配置 + rule-providers | [mihomo/verge/](mihomo/verge/README.md) |
| ShellCrash | 路由器 | 公开策略模板 + 路由器本地注入订阅 | [mihomo/shellcrash/](mihomo/shellcrash/README.md) |

## 目录

```
proxy-config/
├── rules/            共享规则源（唯一编辑入口）→ rules/README.md
├── quantumultx/      bootstrap 模板、filter / rewrite snippet、托管脚本
├── loon/             bootstrap 模板、规则列表、冻结托管插件
├── surge/            托管配置、bootstrap 模板、家庭模块、规则列表
├── mihomo/
│   ├── rules/        生成的 rule-provider 规则集（Verge 与路由器共用）
│   ├── verge/        Windows 主配置（也是路由器模板的生成源）
│   └── shellcrash/   路由器模板、部署脚本、私密参数示例
├── scripts/          生成与巡检工具
├── tests/            回归测试
└── docs/design.md    设计思路与跨端策略约定
```

## 改规则

只改规则内容时（以下命令均在仓库根目录执行）：

```bash
vim rules/ai_extra.yaml              # 1. 编辑规则源
python3 scripts/build_rules.py       # 2. 生成四条客户端线的规则文件
git commit -am "feat: ..." && git push   # 3. 设备按各自刷新周期加载
```

改策略组、规则映射或主配置时，还要运行 `scripts/build_router_config.py` 并按同步边界更新设备，详见 [rules/README.md](rules/README.md)。

提交前检查：

```bash
sh scripts/check_all.sh
```

清单只维护在这个脚本里，CI 也调用它。`tests/test_shellcrash_override.py` 需要 Mihomo 核心与 ShellCrash 覆写脚本，由 CI 运行。

## 安全

以下内容**不得提交**。已知的私密配置路径已加入 `.gitignore`，但忽略规则只按路径匹配、不识别文件内容，提交前仍需检查 diff：

- `quantumultx/bootstrap.conf`、`loon/bootstrap.conf`、`surge/bootstrap.conf`（及 `surge/Airport.conf`、`surge/*.dconf`）：真实订阅、MitM、家庭 SSID
- `*.p12` / `*.pem` / `*.crt` / `*.key`：MitM 私钥与证书
- `mihomo/shellcrash/providers.env`：路由器订阅参数，仅存路由器本地（`600`）
- 任何订阅 token、Cookie、API Key

仓库内只保留占位符模板：`bootstrap.example.conf`、`config*.yaml`、`config-router*.template.yaml`、`providers.env.example`。

设计思路、跨端策略约定见 [docs/design.md](docs/design.md)。
