# 配置、迁移和回退

## 环境变量

| 变量 | 默认/含义 |
| --- | --- |
| `M_ORIGIN` | 必填，浏览器访问的精确 HTTPS origin；无末尾斜杠。仅本机开发允许 HTTP localhost |
| `M_CONFIG` | `/etc/mihomo/config.yaml`；读取 YAML/JSON，写回 JSON |
| `M_HOME` | `/var/lib/mihomo`；须与运行核心的 `-d` 一致 |
| `M_CORE` | `/usr/local/lib/mihomo/mihomo`；示例覆盖为 `/usr/local/bin/mihomo` |
| `M_API` | `http://127.0.0.1:19091`，必须是 loopback 控制器 |
| `M_ADMIN_PORT` | `19092`；后端固定绑定 `127.0.0.1` |
| `M_ADMIN_DATA` | `/var/lib/mihomo-subscriptions`，私有状态及备份 |
| `M_FILE_USER` / `M_FILE_GROUP` | 默认 `mihomo`；核心读取节点缓存的 Linux 用户/组 |
| `M_TARGET_GROUPS` | 默认 `PROXY`；没有旧 HTTP 来源时，首次导入接入这些现有组（逗号分隔） |
| `M_BLOCKED_HOSTS` | 逗号分隔的本机公网 IP、IPv6 和入口域名；自动附加 localhost |
| `M_USER_AGENT` | 下载订阅时使用的 User-Agent；部分服务可能要求 Mihomo 客户端标识 |

空 `M_FILE_GROUP` 可禁用文件 chown，仅适用于已经安排好一致账户与权限的自定义部署。systemd 示例需要与自定义路径同时调整。

## 策略组行为

迁入 HTTP provider 时，会将引用这些来源的组改为引用启用的管理来源。原规则、DNS、监听和密钥不改变。已有静态节点和非托管 provider 保留。启动时没有 HTTP 来源则只向 `M_TARGET_GROUPS` 指定组加入首批订阅。

迁移后的管理来源在已引用托管来源的组中共同可选。精细的“每组独享某个订阅”映射不属于此版本的网页功能。一个组可以继续手动选 DIRECT、REJECT 或其他节点；服务不会替用户决定出口。

## 新 VPS

1. 在旧服务器仍能访问时导出核心配置、完整 `M_ADMIN_DATA`、`M_HOME/managed-subscriptions`、所需规则文件和缓存、证书续期安排以及核心/网页/systemd 配置。Xray 等外部入站单独备份。
2. 新服务器安装相同架构的核心及依赖，核对版本、服务账户和目录权限。
3. 恢复私有配置和状态；更新 `M_ORIGIN`、本机过滤列表、配置内旧绝对 provider 路径、监听、域名、证书、反向代理。必要时替换节点中对旧服务器的引用。
4. 已有 `state.json` 时不要删除状态重新导入；`--bootstrap` 是幂等初始化，不会重建已有状态。
5. 在新机验证网页认证、导入、更新、失败保留和撤销；比较规则/DNS，重启管理服务后复查持久化，检查 timer。
6. 单独验证真实代理入口、DNS 和最终出口，再切换客户端。源码恢复不等于完整网络恢复。

## 回退

网页撤销基于 `M_ADMIN_DATA/backups/` 中的修改前状态和节点缓存，只回退订阅相关变化，保留当前规则。首次从原生 HTTP 来源迁入前没有托管文件缓存，需用安装前完整备份退出本工具。

完整退出：停止/禁用管理服务与 timer；恢复迁入前核心配置、数据和节点选择，并重新加载核心。若注入过仪表盘入口，还原注入工具输出的 `index.html` 和可选 `sw.js` 备份；删除/撤销反向代理的两条管理路由。按自己的现有服务名称重载网页服务。不要盲目删除全部 Mihomo 数据。

注入工具的备份默认在 `/var/lib/mihomo-subscriptions/dashboard-backups/`。页面缓存更新后可能需要重新刷新浏览器；确认侧栏出现再认为入口生效。

## 排错

```sh
systemctl status mihomo-subscriptions.service
journalctl -u mihomo-subscriptions.service -n 40 --no-pager
systemctl list-timers mihomo-subscriptions-refresh.timer
```

空 capability 或核心目录权限不符会导致配置/缓存读写失败。控制器必须运行且配置 Secret 与磁盘配置一致。TLS、无效协议、同名节点、内网订阅或本机回环节点都会使导入被拒绝。管理页面 HTTP 200 不能代替一次真正的订阅更新测试。
