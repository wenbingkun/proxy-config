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

再把 `[policy]` 末尾 16 行 `ssid=… · 自动, …, HOME_SSID:DIRECT` 中的 `HOME_SSID` 全部换成家里 Wi-Fi 的名称（见下文[家庭 / 外出自动切换](#家庭--外出自动切换)）。2.4G 与 5G 名称不同时，每行末尾各追加一项 `, <SSID>:DIRECT`。家庭 SSID 只写在设备本地，不提交到仓库。

**第 3 步：导入 Quantumult X**

在 Quantumult X 中，进入 **「配置文件」→「从文件导入」**，选择刚才编辑好的 `bootstrap.conf`。

导入完成后，bootstrap 中已预配置的远程资源（规则、重写、脚本等）会在 QX 首次刷新时自动拉取。

墨鱼规则同时使用其公开 GitHub 仓库和自建域名资源。`StartUpAds.conf`、`XiaoHongShuAds.conf`、`zhihu.ads.js` 和 `bdpan.ads.js` 会根据客户端 User-Agent 返回不同内容：Quantumult X 请求可获取有效规则或脚本，普通浏览器请求则可能返回 HTML 页面。仓库的远程资源检查会对 QX 资源模拟 Quantumult X 请求。

哔哩哔哩：使用仓库内冻结的旧版规则 `quantumultx/rewrite/bilibili_ad.conf`（deezertidal 转载的墨鱼 `biliad.conf`，最后更新 2023-06-08），其失效的 `bilibili_json.js` 已替换为仓库内 `quantumultx/scripts/bilibili_json.js`（墨鱼 GitHub 删除前的最后一版，2025-03-31）。上游均已停更，此版本不会再更新；它会解密 `app.bilibili.com` 与 `grpc.biliapi.net`，Quantumult X 下历史记录与评论区加载可能偏慢。不要与墨鱼自建站 `BiliBiliAds.conf` 或 Biliverse ADBlock 同时启用。

冻结版的规则、`bilibili_json.js` 和两个外部脚本（app2smile `bilibili-proto.js`、yjqiang `bilibili_dynamic.js`，链接固定到 2026-09-30 的提交）都不会随上游变化。它自带 hostname，使用 `opt-parser=true` 与原版一致，不需要 `#outhn=*`，也不依赖「仓库自定义重写」。脚本会把「我的」页会员字段改成大会员样式，这只影响客户端显示，不会获得服务端会员权益。冻结版中动态相关的两条规则原文都含 `DynAll`，其中 app2smile 那条同时处理视频页 `View/View`，因此若要排除它们，把 bootstrap 中的链接写成 `https://raw.githubusercontent.com/wenbingkun/proxy-config/main/quantumultx/rewrite/bilibili_ad.conf#out=DynAll`（链接原本没有 `#` 参数，首个参数用 `#`，之后的参数才用 `&` 连接），代价是同时失去视频页广告过滤；此做法未经真机验证。

## 家庭 / 外出自动切换

与 Loon 相同（见 [Loon 说明](../loon/README.md#家庭--外出自动切换)）：不使用 `running_mode_trigger` 或 `ssid_suspended_list`，运行模式在任何网络下都保持「规则分流」。策略页上方的 32 个组与路由器、Loon 同名同序，在这些组里选择节点。规则实际引用的是排在最后的 16 个「· 自动」ssid 策略（如 `🤖 人工智能 · 自动`、`🐟 兜底分流 · 自动`、`🇭🇰 香港节点 · 自动`）：

- 连上家里 Wi-Fi：走 DIRECT，交给路由器上的 ShellCrash 分流；
- 其他 Wi-Fi 与蜂窝：走同名基础组，即原来在 QX 里选好的节点。

「· 自动」策略里没有需要选择的内容。`🛡️ 安全防护` 不包装，在家仍然 REJECT；重写、脚本和 MitM 在家同样生效。`[dns]` 未按 SSID 区分，在家仍使用模板中的 DNS 设置。

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
