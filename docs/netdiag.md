# iPhone 网络诊断（netdiag）

让 WSL 里的代理（Codex、Claude Code）直接读取 iPhone 的网络行为，用户只需复现问题。脚本是 `scripts/netdiag.py`（只用标准库），记录存放在 `~/.local/state/ios-netdiag`，保留 7 天，不进仓库。

## 数据来源与能力边界

| 来源 | 内容 | 条件 |
|---|---|---|
| 路由器 Mihomo `/logs` 流 | iPhone 进入 Mihomo 内核的每条新连接：源端口、主机、命中规则、策略链；拨号失败等告警 | iPhone 在家庭 Wi-Fi；QX / Loon / Surge 在家都把流量交给路由器。ShellCrash 让国内域名和 IP 绕过内核，**国内流量（如 B 站、微信）看不到**（2026-10-04 实测） |
| 路由器 `/connections` | 采集时仍打开的连接：远端 IP、DNS 模式、流量 | 同上 |
| Surge HTTP API | 每个请求的规则、策略、notes（DNS、规则评估、错误）、耗时，以及 DNS 缓存、事件、模块；`/v1/requests/recent` 只保留最近 50 条，记录器每 2 秒轮询 | Surge 是当前 VPN，启用本地模块 `netdiag-api.sgmodule`；iPhone 在家庭局域网，或用数据线连电脑（蜂窝网络也可） |

QX 和 Loon 没有可供外部读取请求记录的接口：QX 的 `$configuration` 和 Loon 的 `$config` 只提供策略选择、运行模式、流量统计和 DNS 缓存操作，请求列表和日志只在 App 内查看。因此 QX / Loon 的诊断依靠路由器视角，而且只覆盖进入内核的境外流量：在家时，被 App 拒绝的请求不会出现在路由器上，出现的请求由路由器规则决定出口；国内流量在 QX / Loon 上没有可自动获取的数据。蜂窝网络和外部 Wi-Fi 下，QX / Loon 没有可自动获取的数据，需要回家复现，或在 Surge 中复现。

本次采集未获得可用于逐请求网络诊断的 iOS 系统日志（2026-10-04，iOS 27.2，`pymobiledevice3 syslog live`，默认日志级别）：Surge、Loon、QX 三个隧道进程（Surge-iOS-NE、LoonTunnelProvider、Quantumult X Tunnel）的采集结果中只见系统框架日志，域名显示为 `<private>`，没有 App 自己的逐请求记录；Loon 主 App 自己的日志同样被隐去。这一结论只针对这条采集路线，不推广到其他版本、日志级别或采集方法。

iOS 同一时间只能运行一个 VPN，对比三端行为时需要依次切换 App 复现。

## 安装（本机一次）

1. `~/.config/ios-netdiag/env`（权限 600）：

   ```sh
   ROUTER_API=http://<路由器 IP>:9999
   ROUTER_SECRET=<路由器控制器密钥>
   DEVICE_IPS=<iPhone 局域网 IP>
   # 以下两行在启用 Surge 模块后再加；值后面不能写注释
   SURGE_API=http://<iPhone 局域网 IP>:6171
   SURGE_KEY=<模块里的密钥>
   ```

2. `python3 scripts/netdiag.py install-service`：安装并启动 systemd 用户服务 `ios-netdiag.service`，持续记录。服务直接运行当前工作区里的 `scripts/netdiag.py`：切到没有这个文件的分支前，先 `systemctl --user stop ios-netdiag ios-netdiag-usb`，切回后再 `start`；卸载时 `systemctl --user disable --now` 这两个服务，并删除 `~/.config/systemd/user/ios-netdiag*.service`。
3. 数据线路径（可选，蜂窝网络下读取 Surge）：Windows 装“Apple 设备”App，iPhone 用数据线连接并信任；WSL 建 venv 装 `pymobiledevice3`，在 env 中加 `PYMOBILEDEVICE3=<venv>/bin/pymobiledevice3` 与 `SURGE_API_USB=http://127.0.0.1:16171`，再运行一次 `install-service`，会多装 `ios-netdiag-usb.service`（经 Windows usbmuxd `127.0.0.1:27015` 把本地 16171 转发到 iPhone 的 6171）。局域网不通时记录器自动改走 USB。Windows 重启后需要先打开一次“Apple 设备”App，usbmuxd 才会运行。
4. Surge（可选）：把 `surge/netdiag-api.example.sgmodule` 复制为 iCloud Drive/Surge/`netdiag-api.sgmodule`，换成随机密钥，在 Surge 的模块列表中启用。该 API 监听所有网卡，在不信任的 Wi-Fi 上可以关掉模块。

## 使用

```sh
python3 scripts/netdiag.py status              # 服务、路由器、Surge、iPhone 是否可达
python3 scripts/netdiag.py collect --since 15m # 生成会话目录并打印 Markdown 报告
python3 scripts/netdiag.py get surge /v1/dns   # 只读查询，路径见 Surge HTTP API 手册
python3 scripts/netdiag.py get router /proxies
```

报告包含：数据来源状态、问题列表（Surge 失败 / 拒绝 / 错误 notes / 慢阶段，路由器告警和 REJECT）、Surge 请求按主机汇总、路由器连接按主机汇总、两边都出现的主机。原始数据在会话目录的 JSON 文件中。

## 给代理的约定

报告的 Sources 段标有 `INCOMPLETE` 时，表示有实时数据没拿到，报告只覆盖已记录的部分；`ids skipped` 表示 2 秒轮询可能漏掉了请求，计数只是下限。

用户说“分析刚才的网络行为”时：先 `status`，再按用户描述的时间 `collect --since <窗口>`，读报告和会话目录里的原始 JSON 定位问题，必要时用 `get` 查询当前策略选择或 DNS。要对比各端时，请用户依次在 QX、Loon、Surge 中复现，每次复现后分别采集。报告和原始数据含浏览记录，只在回复中引用必要部分，不提交、不上传。报告只自动隐去家庭 SSID：Surge notes 的原始 JSON 里还有代理节点的域名和 IP、机场相关域名（订阅、面板、DoH），回复、`.local` 以外的文件、提交说明和 PR 中都不能出现这些内容。
