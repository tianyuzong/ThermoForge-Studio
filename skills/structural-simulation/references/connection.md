# 安装与连接

插件包含完整平台源码、MCP 工具和技能；可写数据、虚拟环境和求解器位于持久的源码部署目录。Codex 的插件缓存仅保存发布文件，升级插件不移动仿真数据。

Windows 初次使用：从仓库克隆到用户选择的目录，运行 `Install-CodexPlugin.ps1`，再运行 `Start.ps1 -NoBrowser`。安装脚本会安装工作台依赖、单独的 MCP 环境，创建个人插件目录并调用 Codex 安装。源码部署通常在 `D:\strength-studio`，但不得假定所有机器都使用这个路径。

用户配置位于 `%LOCALAPPDATA%\StrengthStudio\plugin.json`，记录 `workspace`、`url`、`plugin_python`。可用环境变量 `STRENGTH_PLUGIN_CONFIG` 指定配置文件；该文件不得上传。读取配置只取这些字段，不读取任何 CLI 登录文件。

`scripts/Start-Mcp.ps1` 读取上述配置，使用独立 Python 环境运行插件包内的 `strength_mcp.py`。MCP stdout 只用于协议消息。网络仅访问 `127.0.0.1`/`localhost`，绕过代理并拒绝 HTTP 重定向。

工具加载失败时可用 `<workspace>\.venv\Scripts\python.exe <plugin-root>\scripts\strength_client.py health` 诊断；`projects` 列项目，`context --project-id <id>` 读上下文，`schemas` 读 API 定义。服务未启动用 `start`。若没有插件配置或虚拟环境，先完成安装，而不是扫描用户其他磁盘寻找凭据。

安装完成后在新的 Codex 任务中使用插件；当前对话不会凭空获得刚安装的工具。更新插件时从用户选定的源码版本重新运行安装脚本，确认 Codex 安装结果再建议新任务测试。
