# 贡献

欢迎通过 Issue 报告问题，通过 Pull Request 提交改动。请提供系统、Python/Mihomo 版本和最小脱敏复现，不要提交真实订阅、服务器配置或运行缓存。

本地验证：

```sh
python -m pip install -r requirements.txt
python -m unittest discover -v
node --check web/app.js
node --check web/integration.js
```

设置 `M_TEST_CORE` 为本机 Mihomo 可执行文件路径后，会额外运行节点格式的核心集成测试。没有内核时该测试明确跳过。

本仓库不打包 Mihomo、MetaCubeXD 或 Caddy。改动涉及它们时请注明测试版本，保留各上游许可证。修改订阅时必须保留用户规则和 DNS，并覆盖失败回退测试。
