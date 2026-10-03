# 原生 Windows GPU 仿真

`cudss_cuda` 是真实有限元后端。C3D10 刚度组装、FP64 分解与回代、积分点应力/应变及节点内力恢复在 GPU 上执行。几何、网格、连接结构构建、载荷积分和 cuDSS 符号分析仍在 CPU 上执行。

## 安装与使用

验证环境：Windows 11、RTX 3090 Ti 24 GiB、驱动 591.86、CUDA Toolkit 12.6、Python 3.14.6、cuDSS 0.8.0.10。新电脑先运行 Install.ps1，再运行 Install-GPU.ps1；GPU 安装要求已有 CUDA Toolkit 12.6 与兼容驱动，不更改系统驱动或安全策略。

在“计算资源”选择“原生 CUDA + cuDSS”，或向 Agent 输入：

> 使用 GPU，将四个安装孔孔壁固定，在顶面施加向下 1000 N 总力，再比较 1500 N，生成报告。

条件齐全后直接执行。设备不可用、显存不足或数值异常时停止并保留原因，不自动切换 CPU。Diagnose-GPU.bat 与界面“重新运行 GPU 自检”分别检查 CUDA 初始化、cuDSS 加载、NVRTC 编译和真实方程解。更新环境后重新诊断或重启服务。

## 与原计划的适配差异

CuPy 14.2.0 安装成功，但 cub.cp314-win_amd64.pyd 被 Windows Smart App Control 拦截；CodeIntegrity 3077/3033 日志记录了原因。没有修改或绕过系统应用控制。

交付使用原生 CUDA Runtime、Driver 和 NVRTC 接口管理 GPU 数组、运行自有 CUDA 核，cuDSS 求解稀疏系统。这些库在当前系统策略下可以正常加载。运行路径不依赖 CuPy、WSL 或 Visual Studio。

cuDSS 可选 VCOMP 多线程层与 Gmsh 的 OpenMP 运行时在本机同进程中发生异常，故使用默认 CPU 符号分析路径，数值计算继续使用 GPU。性能对照的 CalculiX CPU 明确使用 6 个物理核心。

## 数值、复用与资源边界

- FP64、C3D10 四点积分、曲边等参映射，积分点 Jacobian 必须为正。应变剪切分量为张量剪切；应力顺序 xx/yy/zz/xy/xz/yz。
- 固定与给定位移通过精确自由度消元处理。节点内力通过单元积分独立恢复；反力为约束自由度上的 Ku−F，不重复扣除载荷。
- SPD 分解禁止小主元正则化，不增加弱弹簧。独立自由度残差必须 ≤1e-8，报告合力和合力矩残差必须 ≤1e-4。
- 某些细长模型 cuDSS 迭代修正返回状态 7。原始状态保存在 cudss_ir_status；只有独立单元内力残差 ≤1e-8 且报告平衡校核通过才接受结果，否则失败，不修改工程条件。
- 同一 Agent 任务使用独立 GPU 工作进程；网格、弹性常数和约束自由度相同时复用分解。改变给定位移值只更新右端项；网格、材料改变使缓存失效。任务结束或取消后释放。
- GPU 实际自由度上限 120 万。网格前检查预计主机内存，分解前检查 cuDSS 内存估计。GPU 预算为初始化时空闲显存的 80%，每次分配还检查当前空闲量。不自动粗化或降低精度。
- buffer_peak_bytes 是应用 GPU 缓冲分配峰值；分解内部显存估计单列于 factor_memory_estimates_bytes，前者不是整个 GPU 进程的精确峰值。

## 复现验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_gpu.py -q
.\.venv\Scripts\python.exe scripts/gpu_benchmark.py
```

基准固定约 50 万、100 万自由度支架网格并保存 SHA-256，使用完全相同的节点载荷和约束对照 CalculiX。预热后各运行三次；GPU 每次清除分解缓存，比较中位数，另测第二工况复用收益。

计时包含矩阵组装、传输、分析、分解、求解、原始场恢复；CPU 包含文本输入输出转换。网格、报告不计入计算加速比。GPU_BENCHMARK.json 保存计时和误差，GPU_VALIDATION.md 汇总最终验收。

本次使用 CUPTI 本进程内核活动追踪；Nsight Windows CLI 因管理员权限要求未能采集，脚本已保留。追踪与性能测量分开。普通任务 kernel_trace_verified=false 表示没有为该次任务单独采集追踪；gpu_execution_verified=true 表示固定 CUDA 执行路径已完成并通过独立残差检查。

旧 calculix_cuda 仅用于历史外部 PaStiX 构建，不自动改名为原生后端，不重写历史任务。

参考：[cuDSS 官方文档](https://docs.nvidia.com/cuda/cudss/getting_started.html)、[cuDSS 参数与错误检查](https://docs.nvidia.com/cuda/cudss/types.html)。
