# 0.4.0 发布前回归

日期：2026-09-22。Windows 本机，Python 3.14.6、Gmsh 4.15.2、CalculiX 2.23、RTX 3090 Ti、CUDA Toolkit 12.6、cuDSS 0.8.0.10。

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

完整执行结果：**48 passed, 2 warnings in 152.63s**。原始输出仅替换本机绝对路径后保存在 [PUBLICATION_TEST_RESULTS.txt](PUBLICATION_TEST_RESULTS.txt)。两条提示来自 Starlette/TestClient 和 AnyIO 的弃用接口，不是求解失败。

回归包含真实 CPU/GPU 数值计算、几何与组件、Agent 任务状态及可靠性、实验性齿轮接触。自动化 Agent 解析测试使用受控响应或离线规则；真实 CLI 调用的历史验证分别记录在 [CPU 基线](VALIDATION.md)、[GPU 验证](GPU_VALIDATION.md)和[组件验证](COMPONENT_WORKBENCH.md)。

本次发布整理修正了测试输出目录：测试写入仓库内 `.runtime/test-runs/<唯一编号>`，不再依赖仓库外部的工作区层级，也不使用正常 `data/` 项目目录。

此轮回归不是新的 50 万/100 万自由度性能测试，不改变 0.2 性能记录的版本范围；也不代表已完成 Hertz 接触压力、齿轮网格收敛或全新 Windows 机器安装验证。
