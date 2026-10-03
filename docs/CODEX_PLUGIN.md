# Strength Studio · Codex 插件

插件将完整工作台封装为 **中文技能 + 19 个本地 MCP 工具 + CPU 求解服务**。Codex 在当前对话中理解和补问，提交结构化工况，读取真实结果并解释；不需要再启动一次嵌套的 Codex 规划。浏览器工作台继续用于选面、云图、变形动画、剖切和报告查看。

支持 Windows、64 位 Python 3.11+（本机验证为 3.14.6）和已登录的 Codex CLI。CPU 求解默认单队列、6 线程、300000 自由度，无需 GPU。线程配置不应超过本机逻辑核心数。GPU 模块保留在源码中，但插件只提交 CPU 静力工况。

## 安装

```powershell
git clone https://github.com/tianyuzong/strength-studio.git D:\strength-studio
cd D:\strength-studio
powershell -ExecutionPolicy Bypass -File .\Install-CodexPlugin.ps1
powershell -ExecutionPolicy Bypass -File .\Start.ps1 -NoBrowser
```

已部署工作台时使用 `Install-CodexPlugin.ps1 -SkipEngineInstall`。如果 `codex` 不在 PATH，增加 `-CodexPath '实际的 codex.exe 路径'`。自定义端口使用 `-Port 端口号`，启动服务时指定同一个端口。

安装脚本完成以下操作：

1. 复用或安装源码目录的 Python 环境及经过 SHA-256 校验的 CalculiX；使用已构建前端。
2. 在 `.runtime/plugin-env` 安装单独的 MCP 依赖，避免改变求解环境。
3. 将允许发布的文件复制至 `~/plugins/strength-studio`，注册个人插件目录 `~/.agents/plugins/marketplace.json`。
4. 保存本机连接配置并调用 `codex plugin add strength-studio@personal`（已有个人目录名称会保留）。不会复制 Codex 登录文件。

安装后**新建一个 Codex 任务**，从插件列表选择“Strength Studio 结构仿真”，或使用 `$structural-simulation` 技能。当前已开始的任务需要新任务才能拾取新增工具。默认工作台地址为 <http://127.0.0.1:8876/>。

## 使用示例

```text
使用 Strength Studio 插件，新建内置支架的软件验证项目。
使用内置演示钢材，将安装孔孔壁固定，在竖板顶面施加沿全局 -Z 的 1000 N 总力。
先读取真实几何证据并核对选面，再使用 CPU 求解。
读取位移、原始积分点等效应力、反力及平衡残差，给出带中文目录的 PDF 报告。
所有材料和边界条件均按教学假设标注。
```

已有模型可说：“导入我指定的 STEP 文件，先列出组件与候选固定面，缺少物理条件时集中补问。”已有结果可说：“查询该作业坐标 (…) m 沿 -Z 的位移，不重新求解。”修改工况可说：“保留其他设置，把指定的载荷改成 1500 N，新建工况并与 1000 N 比较。”

## 工具与数据流

| 阶段 | 工具 |
|---|---|
| 启动和状态 | `studio_status`、`studio_start` |
| 项目与模型 | `studio_projects`、`studio_create_project`、`studio_context`、`studio_demo`、`studio_import_model` |
| 材料与类型 | `studio_materials`、`studio_schemas` |
| 执行 | `studio_validate_case`、`studio_submit_case`、`studio_task`、`studio_cancel` |
| 真实结果 | `studio_job`、`studio_results`、`studio_query`、`studio_compare` |
| 报告与导出 | `studio_artifacts`、`studio_export` |

提交后返回任务编号，不在 MCP 调用中等待整段求解。复用 request_id 可以避免重复提交；修改工况必须使用新 id。结果请求由后端当前 OpenAPI 定义校验。默认使用 SI 单位，STL 必须明确长度单位。查询历史结果不会修改历史报告；需要进入新报告的探针/路径应写入新工况的 `result_requests`。

## 配置、升级与卸载

- 连接配置：`%LOCALAPPDATA%\StrengthStudio\plugin.json`，含 `workspace`、`url`、`plugin_python`；也可通过 `STRENGTH_PLUGIN_CONFIG` 指定文件。源码里没有任何用户绝对路径。
- CPU 设置：源码目录的 `local-settings.json`，参考 `local-settings.example.json`。调整后重启服务。
- 升级：在持久的源码目录选择目标版本，重新运行安装脚本。已有插件快照会备份，安装版本附带缓存标识；模型和结果继续保留在原 `data` 中。
- 卸载：`codex plugin remove strength-studio@personal`；仿真数据与源码仍保留。停止工作台使用源码目录中的 `Stop.bat`。
- 诊断：`Diagnose-CPU.bat`；插件接口诊断使用 `.venv\Scripts\python.exe scripts\strength_client.py health`。

发布包使用白名单，排除数据、登录信息、本机配置、虚拟环境、求解器二进制、日志和界面备份。求解和 CAD 处理在本机运行；当前 Codex 对话会按用户请求接收选定的几何、工况和结果上下文，插件不自动上传源 CAD 或结果至 GitHub。工作台接口只访问回环地址，绕过代理并拒绝重定向。

## 发布与验证

```powershell
.venv\Scripts\python.exe -m pytest tests -q
.runtime\plugin-env\Scripts\python.exe scripts\smoke_plugin.py
.venv\Scripts\python.exe scripts\package_plugin.py
```

`smoke_plugin.py` 使用真实 stdio MCP 会话创建独立拉杆演示项目，完成几何、工况校验、CPU 求解、重复提交幂等检查、测点查询和中文 PDF 导出。验收文件保存在 `.runtime/plugin-acceptance`，不会进入发布包。发布包位于 `dist-plugin/Strength-Studio-Codex-0.5.0.zip`，包含逐文件 SHA-256 清单。具体本机结果见 [插件验收记录](PLUGIN_ACCEPTANCE.md)。

插件清单采用本机 Codex 支持的 `.codex-plugin/plugin.json` 与 `.mcp.json` 格式，参考 [OpenAI 插件打包文档](https://developers.openai.com/plugins/build/plugins)。GitHub 发布不等于进入 OpenAI 公共插件目录。

## 分析边界

三维实体各向同性小变形线弹性静力、独立组件及明确的理想粘结装配。CPU 不支持通用摩擦/齿轮接触、塑性、热应力、疲劳、屈曲、断裂、梁壳专用单元、复合材料或动力学。材料预设仅供参考，缺屈服数据不提供安全结论。位移动画不代表振动或真实时间，1 倍表示真实静态位移。
