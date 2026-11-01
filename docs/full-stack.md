# 从新 Debian VPS 部署 3X-UI + Mihomo

第一次安装、希望直接照步骤操作，请先看[小白版教程](beginner.md)。本文保留部署细节和检查方法，方便后续排错。

本仓库提供两个入口：新 VPS 使用本文的组合安装；已经运行 Mihomo 的服务器使用 [订阅管理扩展安装](../README.md#已有-mihomo安装订阅管理扩展)。组合安装器面向 **Debian 12/13、systemd、amd64/arm64**，检测到已有节点/配置会停止。

## 1. 结构和端口

```text
家中客户端 ── VLESS + RAW(TCP) + REALITY + Vision ──> 3X-UI 管理的 Xray
                                                        │ 按入站标签转发
                                                        v
                                              Mihomo 127.0.0.1:17891
                                                        │
                         中国/私有地址直连 <── 规则分流 ──> 网页选择的订阅节点

https://panel.example.com  ── Caddy ── MetaCubeXD / 订阅管理
https://xui.example.com    ── Caddy ── 3X-UI /sub/ 订阅服务
```

3X-UI 负责入口、客户端和流量统计；Mihomo 负责订阅节点、选择与分流；Caddy 负责两个网页入口的 TLS。它们是同一项目的一套组合部署，仍保留各自适合的管理界面。

| 用途 | 默认端口/访问范围 |
| --- | --- |
| SSH | 按 VPS 实际配置保留，示例 22/TCP |
| Caddy HTTPS / ACME | 公网 443/TCP、80/TCP |
| VLESS REALITY 入站 | 公网 24443/TCP，可在安装参数中修改 |
| 3X-UI 管理 / 订阅 | 仅 127.0.0.1:2053 / 2096，经 Caddy 访问 |
| Mihomo 控制 / 订阅管理后端 | 仅 127.0.0.1:19091 / 19092 |
| Xray → Mihomo SOCKS | 仅 127.0.0.1:17891，含 SOCKS UDP 支持 |
| 其他本机监听 | mixed 7897、DNS 5335、Xray 内部 API 62789 / metrics 11111 |

**不要把 REALITY 入站也设成公网 443**，它会与本方案的 Caddy 冲突。要用 443 入站，需要另行规划 IP、网页端口或四层转发，不在默认组合安装范围内。

## 2. 域名、网络和系统

准备两个不同域名，示例为 `panel.example.com` 和 `xui.example.com`，A/AAAA 指向这台 VPS。仅填写你实际可用的 AAAA，不要保留错误 IPv6 记录。域名不经过会终止该连接的 CDN 代理。

先在云安全组和现有系统防火墙放行 **实际 SSH 端口、80/TCP、443/TCP、24443/TCP**（自定义入站时相应替换）。保留一个 SSH 会话，确认新会话可以连接后再启用新防火墙。安装器不会自动启用 UFW、改 SSH、改时区或执行系统全面升级。

在新 VPS 上按需安装前置工具；root 登录无需再加 sudo：

```sh
apt-get update
apt-get install -y git python3 ca-certificates
```

新系统的常规升级、重启和时区选择应在安装前自行完成；不要在正在转发流量的旧服务器上照抄全面升级命令。

## 3. 同一个仓库完成组合安装

```sh
git clone https://github.com/yanzlm/mihomo-x-ui.git
cd mihomo-x-ui

# 先预览，不写配置、不安装软件；替换域名和真实公网 IP。
python3 stack.py \
  --panel-domain panel.example.com \
  --xui-domain xui.example.com \
  --server-ip YOUR_PUBLIC_IPV4 \
  --node-port 24443 \
  --ssh-port 22

# 确认参数后，以 root 执行同一命令并增加 --apply。
python3 stack.py \
  --panel-domain panel.example.com \
  --xui-domain xui.example.com \
  --server-ip YOUR_PUBLIC_IPV4 \
  --node-port 24443 \
  --ssh-port 22 \
  --apply
```

若有 IPv6，再加一项 `--server-ip YOUR_PUBLIC_IPV6`，同时列出服务器所有入口 IP，以便 Mihomo 过滤指向自身的节点。示例占位符不是有效 IP，必须替换。

安装器会执行：

1. 检查系统、已有目录和本机 TCP 端口，拒绝覆盖已有 3X-UI/Mihomo/Caddy 站点。
2. 从官方渠道下载 `deploy/versions.json` 指定的 3X-UI、Mihomo 和 MetaCubeXD，核对 SHA-256。Caddy 和基础工具来自 Debian 的签名软件源。
3. 初始化只在本机监听的 3X-UI 管理与订阅服务；生成随机账户、密码、私有登录路径。
4. 安装 Mihomo、MetaCubeXD、订阅管理、systemd 服务和更新 timer，初始化中国/私有地址直连、其他走 `PROXY` 的规则。
5. 配置两个域名的 Caddy HTTPS；验证本机 API 和两个 HTTPS 网页实际可访问后结束。

安装入口使用固定版本，不在每次运行时盲目跟随 latest。版本和摘要见 [版本锁定文件](../deploy/versions.json)。它不会代替你在网页中创建节点，也不会自动购买或提供上游订阅。

登录信息保存在本机 root 私有文件，勿粘贴到聊天、Issue 或 Git：

```sh
cat /etc/mihomo-x-ui/credentials.json
```

这只是服务安装成功。完成下面的入站、桥接和订阅步骤，并验证真实流量后才算代理链路可用。

## 4. 在 3X-UI 创建 REALITY 入站

用私有文件中的 `xui_url` 登录。先检查管理员账户，并按自己的需求开启 2FA，妥善保存恢复资料；完成后重新登录一次。密钥和验证码由你在面板中设置。

在入站列表添加：

| 项目 | 填写方式 |
| --- | --- |
| 协议 | VLESS |
| 监听 | 按实际公网接入需求，通常留空 |
| 入站端口 | 与安装时一致，默认 24443 |
| 客户端 | 新生成 UUID，备注自定 |
| Flow | `xtls-rprx-vision` |
| 传输 | RAW；有的界面仍标 TCP |
| 安全 | REALITY |
| Target / Dest | 从当前 VPS 实测可达的 TLS 目标域名与端口，例如 `addons.mozilla.org:443`；不是所有线路都适用 |
| Server Names / SNI | 与目标站及证书对应的域名，例如 `addons.mozilla.org` |
| X25519 | 在面板生成新密钥对，私钥留服务器；客户端使用面板导出的对应参数 |
| Short IDs | 在面板随机生成，客户端保持一致 |
| 客户端指纹 | 常见选择 `chrome`，以客户端实际支持为准 |

可先检查目标站 TLS，需检查握手成功及证书验证，而非只看到端口打开：

```sh
openssl s_client -connect addons.mozilla.org:443 \
  -servername addons.mozilla.org -tls1_3 -verify_return_error </dev/null
```

保存后导出面板生成的节点链接。RAW 是原 TCP 传输的名称调整；具体字段和兼容性以安装的 Xray 与客户端版本为准。不要把文档里的测试域名、UUID 或密钥当作可用节点。

## 5. 让该入站通过 Mihomo 出站

新部署不会擅自把全部 Xray 流量送给 Mihomo。只为需要中转的入站添加桥接规则，保留 Xray 内部 API 和其他入站的行为。

在 3X-UI 的 **Xray 设置 → 出站** 新增 SOCKS 出站，标签 `mihomo-relay`，地址 `127.0.0.1`，端口 `17891`，本机不设 SOCKS 认证。JSON 对应：

```json
{
  "tag": "mihomo-relay",
  "protocol": "socks",
  "settings": {
    "servers": [{"address": "127.0.0.1", "port": 17891}]
  }
}
```

在 **路由规则** 新增一条按入站标签匹配到 `mihomo-relay` 的规则。默认端口常见标签是 `inbound-24443`，必须以面板实际生成的标签为准；保留内部 API 规则在前，将本条放在会抢先匹配该入站的宽泛规则之前。只填以下所需条件，不混入面板预设的端口、协议或 IP 限制：

```json
{
  "type": "field",
  "inboundTag": ["inbound-24443"],
  "outboundTag": "mihomo-relay"
}
```

安装器会把与所选端口对应的两段示例写到 `/etc/mihomo-x-ui/xray-bridge.json`。它们是**待添加的片段**，不能覆盖整份 Xray 配置。保存应用后检查 Xray 日志；不要同时保留旧的同一入站直连/其他出站规则在它前面。

注意：不要把这台新服务器自身的入站订阅导入它自己的 Mihomo，这会形成回环。Mihomo 这里导入的是其他出口服务器的订阅。3X-UI 生成的本机入口链接用于家里的客户端。

## 6. 在 Mihomo 网页导入出口订阅

1. 打开 `https://panel.example.com/subscriptions/`，用私有文件中的 Mihomo Secret 连接。
2. 添加外部出口订阅，导入并应用。
3. 返回 MetaCubeXD 节点面板，将 `PROXY` 从初始的 `REJECT` 改为需要的节点。
4. 配置需要中转的 Xray 入站后，客户端连接本机 REALITY 入口，海外流量才会进入所选出口。

初始 `REJECT` 用于防止尚未配置订阅时悄悄回落 VPS 直出；导入不等于已自动选中。国内/私有地址规则位于最终代理规则前。这里的“直连”以 VPS 所在位置为基准，**家中局域网访问必须在本地客户端/旁路由保留直连，不能把家中私网目标送上 VPS 后再期待它访问家中设备。**

## 7. 订阅端口与 HTTPS

3X-UI 内部订阅端口为本机 2096，Caddy 将 `https://xui.example.com/sub/...` 转发到它；外部只开放 443，不需要公开 2096、2053。保存订阅设置时维持内部监听为 `127.0.0.1`，不要为了“解决超时”改成全网监听。

若你修改 3X-UI 的订阅路径、监听或启用 JSON/Clash 的独立路径，需要同步调整 Caddy。不要把 `/subscriptions/`（出口订阅管理页面）和 3X-UI 的 `/sub/`（向客户端发布本机入口订阅）混为一谈。

## 8. 验证、性能和维护

```sh
systemctl is-active x-ui mihomo mihomo-subscriptions caddy
systemctl list-timers mihomo-subscriptions-refresh.timer
ss -lntup
journalctl -u x-ui -n 40 --no-pager
journalctl -u mihomo -n 40 --no-pager
```

依次确认：两个 HTTPS 网页证书正常；内部端口只在本机监听；客户端真实连接入口；Xray 路由命中 SOCKS；Mihomo 出现连接且海外规则选择正确；最终出口与所选节点一致；国内和家中局域网按预期直连。再测视频和多次吞吐，不只看面板延迟。单纯安装服务或选择 fake-IP 不能证明全屋 DNS 无泄漏，客户端自带 DoH、IPv6 和网关设置要另行验证。

BBR 属于可选优化，不代表线路不再丢包，也不能解决差线路或带宽限制。先确认内核支持和拥塞控制实际状态：

```sh
sysctl net.ipv4.tcp_available_congestion_control
sysctl net.ipv4.tcp_congestion_control net.core.default_qdisc
```

要启用时由管理员管理独立的 sysctl 文件并验证回读，不反复往 `/etc/sysctl.conf` 追加同一配置。不要仅凭 `lsmod` 判断是否启用，内核内建模块可能不列在那里。

固定版本的更新要先备份并核对兼容性，不能把面板“稳定通道”误当作捆绑 Xray 一定不是预发布的保证。原始安装和参数化代码测试并不等于所有新 VPS 的性能或可达性证明。

## 9. 安装失败与迁移

本安装器只支持新机。失败后保留 `/etc/mihomo-x-ui/install.log`、下载、私有凭据及已创建的数据，**不会自动删除或覆盖后重试**。先查看失败点，停止已启动的新服务，按实际状态修复；不要在未知状态下批量删除配置。软件包安装、ACME 签发和系统账户不属于自动回滚范围。

旧机迁移应先完整备份 3X-UI 数据库、Mihomo 配置、规则缓存、管理状态及服务配置，再依 [迁移说明](deployment.md) 在新机恢复和适配。不要对旧节点运行 `stack.py --apply`。普通网页撤销只处理订阅修改，不能回退完整服务器安装。

## 官方技术资料

- [3X-UI 官方仓库与发布](https://github.com/MHSanaei/3x-ui)
- [Xray RAW 传输](https://xtls.github.io/config/transports/raw.html)
- [Xray REALITY](https://xtls.github.io/config/transport.html#realityobject)
- [Mihomo 配置](https://wiki.metacubex.one/config/)
- [Caddy HTTPS](https://caddyserver.com/docs/automatic-https)

本文是本项目的原创部署说明，不打包或重新许可第三方软件；上游软件保留各自的版权与许可证。
