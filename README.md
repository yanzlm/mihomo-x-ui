# mihomo-x-ui

在一个项目中部署 **3X-UI + Mihomo + MetaCubeXD + 网页订阅管理**，并提供 REALITY 入站到订阅出口的完整配置步骤。

**MIT · Debian 12/13 新机组合安装 · Python 3.11+ · 原生订阅管理网页**

这是独立项目，不是 3X-UI 或 MetaCubeX 的官方产品。安装时从官方渠道获取上游软件，本仓库不打包其二进制。

## 新 VPS：部署完整组合

从这里开始：[3X-UI + Mihomo 完整部署教程](docs/full-stack.md)。

```sh
git clone https://github.com/yanzlm/mihomo-x-ui.git
cd mihomo-x-ui
# 替换域名和真实公网 IP，先预览安装计划。
python3 stack.py --panel-domain panel.example.com --xui-domain xui.example.com --server-ip YOUR_PUBLIC_IP
# 新 Debian VPS 上以 root 执行同一命令并增加 --apply，才会安装。
```

安装器提供固定版本下载与 SHA-256 校验、3X-UI 和订阅服务的本机监听、Mihomo 与两个网页面板、Caddy HTTPS、systemd 与订阅更新定时器。完成安装后按教程创建 REALITY 入站、添加 Xray → Mihomo 转发、导入订阅并选节点。默认网页使用 443，REALITY 使用 24443，避免端口冲突。

已有节点请使用下面的扩展安装方式；`stack.py --apply` 会拒绝覆盖旧配置。组合安装入口仍需在具体新 VPS 验证，不把服务启动当作代理链路已完成。

## 功能

- 添加、编辑、暂停和删除多个订阅；手动更新或按来源定时更新。
- 支持 Clash/Mihomo YAML、Base64 和原始节点 URI 订阅，具体协议由本机 Mihomo 决定。
- 只提取节点；保留现有分流规则、DNS、监听和控制密钥。
- 自动接入原有使用这些订阅的策略组，尽量保留仍存在的手动节点选择。
- 修改前备份、配置校验、热重载、失败回退；网页可撤销上次订阅修改。
- 下载时检查公网地址、跳转、TLS 和体积限制；可显式过滤本机入口避免回环。
- 可选在 MetaCubeXD 侧栏加入“订阅管理”入口。

访问：`https://你的面板域名/subscriptions/`。使用原 Mihomo Secret 登录；在同源 MetaCubeXD 已连接的浏览器里会尝试复用已保存的连接资料。

## 订阅管理扩展的部署前提

准备一台已有 **Mihomo 及可用配置** 的 Linux VPS（systemd 示例以 Debian/Ubuntu 为主），以及 curl、Python 3.11+、PyYAML、同源 HTTPS 反向代理。核心控制 API 必须只监听 loopback，并有非空 Secret。

下面这条路径只给**已有核心**增加订阅管理，不安装或覆盖现有 3X-UI，不提供节点或购买上游出口。新机需要完整组合时使用上面的教程。单独准备核心可参考：

- [Mihomo 配置文档](https://wiki.metacubex.one/config/)
- [代理集合](https://wiki.metacubex.one/config/proxy-providers/)
- [MetaCubeXD](https://github.com/MetaCubeX/metacubexd)
- [Caddy](https://caddyserver.com/docs/)

## 已有 Mihomo：安装订阅管理扩展

### 1. 获取代码与依赖

```sh
git clone https://github.com/yanzlm/mihomo-x-ui.git
sudo cp -a mihomo-x-ui /opt/mihomo-subscription-manager
sudo chown -R root:root /opt/mihomo-subscription-manager
cd /opt/mihomo-subscription-manager
sudo python3 -m venv .venv
sudo .venv/bin/python -m pip install -r requirements.txt
```

系统需安装 `git curl python3 python3-venv`。代码与虚拟环境应由管理员拥有；网页目录须允许反向代理进程读取。

### 2. 配置当前 VPS

```sh
sudo install -m 600 examples/manager.env.example /etc/mihomo-subscriptions.env
sudoedit /etc/mihomo-subscriptions.env
```

务必调整：`M_ORIGIN`（精确 HTTPS origin，无末尾斜杠）、`M_CORE`、配置/数据路径、核心读取文件的账户与组，以及 `M_BLOCKED_HOSTS`。不要保留示例地址。详见 [配置与迁移](docs/deployment.md)。

已有 HTTP `proxy-providers` 会在初始化时迁入，来源数不限于示例数量。若没有 HTTP provider，用 `M_TARGET_GROUPS` 指定接收首次导入节点的现有策略组，多个名称用逗号分隔。组名默认 `PROXY`。初始化不会接管已有的非 HTTP provider。

### 3. 备份并初始化

先安全备份核心配置及数据目录，记录当前节点选择。首次迁移前的完整备份用于退出本工具；网页“撤销”用于迁移后的订阅修改，二者不同。

在 root shell 中载入你自己编辑的可信环境文件，并创建目录（路径按实际环境修改）：

```sh
sudo -i
set -a
. /etc/mihomo-subscriptions.env
set +a
install -d -m 700 "$M_ADMIN_DATA"
install -d -m 750 -o "$M_FILE_USER" -g "$M_FILE_GROUP" "$M_HOME/managed-subscriptions"
/opt/mihomo-subscription-manager/.venv/bin/python /opt/mihomo-subscription-manager/server.py --bootstrap
exit
```

初始化会连接运行中的核心、获取订阅并校验，成功后把已接管的 HTTP provider 转为本工具维护的 file provider。配置以 JSON（合法 YAML）写回，原注释/排版不保留。**不要同时用其他工具覆盖同一核心配置。**

### 4. 启动服务和定时器

审核 `examples/*.service` 的程序路径及 `ReadWritePaths` 与环境一致，再安装：

```sh
sudo install -m 644 examples/mihomo-subscriptions.service /etc/systemd/system/
sudo install -m 644 examples/mihomo-subscriptions-refresh.service /etc/systemd/system/
sudo install -m 644 examples/mihomo-subscriptions-refresh.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mihomo-subscriptions.service mihomo-subscriptions-refresh.timer
```

服务默认以受 systemd 限制的 root 运行，需要写核心配置、修改文件所属用户和调用核心。可按环境进一步隔离，但不能省略对这些目录的必要访问权限。

### 5. 合并 HTTPS 路由

把 [Caddy 示例](examples/Caddyfile.example) 的路由合并到现有虚拟主机，替换域名、目录和端口。验证配置后重载自己的 Caddy 服务。不要直接覆盖已有站点配置；核心和后端均保持 loopback。

打开 `/subscriptions/`，填写原面板密钥，即可添加订阅。返回节点面板选择节点；节点数量仅表示已导入数量，实际连通性由核心测试。

如需侧栏入口：

```sh
sudo python3 integrate_dashboard.py --ui-dir /var/lib/mihomo/ui
```

该命令备份 `index.html`/`sw.js` 后注入入口并更新 PWA 首页缓存 revision。更新 MetaCubeXD 后可能需要重新执行；独立 `/subscriptions/` 地址不依赖侧栏注入。

## 日常使用

1. “添加订阅” → 名称、链接、更新间隔 → “导入并应用”。
2. 返回节点面板选择节点，新节点已进入对应策略组。
3. 用“编辑”更换链接或暂停来源；“更新全部”立即刷新。
4. 出错时保留上次成功配置；需要恢复上次订阅修改时点“撤销上次修改”。至少保留一个启用来源。

完整 YAML 订阅中的规则、DNS 和监听会被忽略。配置最多管理 32 个来源；下载上限 8 MiB，每来源最多 5000 个节点。定时器每分钟检查到期来源；页面提供常用间隔，后端支持 5 分钟至 7 天。

## 迁移、回退与限制

见 [配置与迁移](docs/deployment.md) 和 [安全说明](SECURITY.md)。源码仓库不包含你的私有订阅、节点缓存、密钥、证书或服务端入站，不能代替灾备。

订阅管理原型曾完成单台 Linux VPS 的上线验证；公开版有自动测试和固定上游组件的兼容检查。组合安装需要在你的域名、网络和 VPS 上完成验收，不承诺覆盖所有发行版、核心和仪表盘版本。没有 TUN/DNS 防泄漏或出口路由的独立端到端保证。

## 开发与许可证

```sh
python -m pip install -r requirements.txt
python -m unittest discover -v
node --check web/app.js
node --check web/integration.js
```

设置 `M_TEST_CORE` 为 Mihomo 可执行文件路径后运行真实核心节点格式测试；否则明确跳过。参见 [贡献说明](CONTRIBUTING.md)。

本项目原创代码采用 [MIT](LICENSE) 许可证。Mihomo、MetaCubeXD、Caddy 和 PyYAML 保留各自的版权与许可证；本仓库不包含其二进制或完整源码。

---

**English:** A deployment project for 3X-UI, Mihomo, MetaCubeXD and a subscription-management web extension. Includes a fresh Debian 12/13 installer with pinned upstream downloads, HTTPS dashboards, and a guide connecting REALITY ingress to Mihomo-selected egress. Existing Mihomo users can install only the extension. Requires manual inbound creation, subscription import, egress selection and end-to-end validation. Independent project; upstream software retains its own licenses.
