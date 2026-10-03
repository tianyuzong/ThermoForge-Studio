# ThermoForge Studio 截图总览

[返回详细 README](../README.md) · [42 页原始中文报告](media/motor-analysis-report.zh-CN.pdf) · [媒体来源与哈希](media-provenance.json)

本页收录 33 张图片：16 张本次实际 UI 截图、9 张平台已有视频导出帧、8 张平台 PDF 页面渲染。另有 4 个原始 1080p MP4 和 1 个缩小动画预览。没有生成或修改物理结果。

截图采集：2026-10-03，平台 0.8.0。本次只打开已有结果，没有为拍图重新求解。电机为软件演示参数；粗步长热结构结果不宣称独立网格/步长验证通过。局部轴承使用另一份明确编号的研究，不能把其证据套到电机。

## 导航

- [真实工作台与配置](#真实工作台与配置)
- [平台导出的真实计算帧](#平台导出的真实计算帧)
- [平台生成 PDF 的页面](#平台生成-pdf-的页面)

静力与稳态为非瞬态展示；疲劳是累计评估。灰色表示无可用数据。整机接触导出视角可能被外壳遮挡，应结合局部截图和报告接触页阅读；整机视角不会凭空给壳体涂上接触值。

## 真实工作台与配置

### 主工作台：共享 CAD、组件材料与仿真助手

![主工作台：共享 CAD、组件材料与仿真助手](screenshots/01-workbench.png)

实际本地工作台截图。电机示例项目 `c468900b`。
### 通用长指令模板：仅草案预览，未提交新求解

![通用长指令模板：仅草案预览，未提交新求解](screenshots/02-prompt-workflow.png)

实际本地工作台截图。电机示例项目 `c468900b`。
### 原结构工况载荷：作用组件、选区与总力单位

![原结构工况载荷：作用组件、选区与总力单位](screenshots/03-structural-loads.png)

实际本地工作台截图。电机示例项目 `c468900b`。
### 组件材料库与独立工况材料

![组件材料库与独立工况材料](screenshots/04-material-library.png)

实际本地工作台截图。电机示例项目 `c468900b`。
### 多物理场研究配置与自动恢复草稿

![多物理场研究配置与自动恢复草稿](screenshots/05-multiphysics-configuration.png)

实际本地工作台截图。电机示例项目 `c468900b`。
### 热边界与组件总功率：图中为演示值

![热边界与组件总功率：图中为演示值](screenshots/06-thermal-boundaries.png)

实际本地工作台截图。电机示例项目 `c468900b`。
### 瞬态温度 600 s：真实末帧和固定色标

![瞬态温度 600 s：真实末帧和固定色标](screenshots/07-transient-temperature.png)

实际本地工作台截图。电机示例项目 `c468900b`。
### 真实温度曲线、节点探针与分开的验证说明

![真实温度曲线、节点探针与分开的验证说明](screenshots/08-history-and-validation.png)

实际本地工作台截图。电机示例项目 `c468900b`。
### 15 s 与 30 s 两档热结构结果比较

![15 s 与 30 s 两档热结构结果比较](screenshots/09-case-comparison.png)

实际本地工作台截图。电机示例项目 `c468900b`。
### CPU 六线程与 GPU 状态：旧静力资源对话框，本轮 CPU 验收

![CPU 六线程与 GPU 状态：旧静力资源对话框，本轮 CPU 验收](screenshots/10-compute-resources.png)

实际本地工作台截图。电机示例项目 `c468900b`。
### 选择性旧项目导入：只查看入口，未执行导入

![选择性旧项目导入：只查看入口，未执行导入](screenshots/11-legacy-import.png)

实际本地工作台截图。电机示例项目 `c468900b`。
### 高周疲劳应力循环统计：演示曲线下未预测有限寿命

![高周疲劳应力循环统计：演示曲线下未预测有限寿命](screenshots/12-fatigue-cycles.png)

实际本地工作台截图。电机示例项目 `c468900b`。
### 600 s 轴承组件局部压力：投影场、全历程色标

![600 s 轴承组件局部压力：投影场、全历程色标](screenshots/13-local-contact-pressure.png)

实际本地工作台截图。电机示例项目 `c468900b`。
### 600 s 轴承组件局部间隙：灰色为无可用值

![600 s 轴承组件局部间隙：灰色为无可用值](screenshots/14-local-contact-gap.png)

实际本地工作台截图。电机示例项目 `c468900b`。
### 局部滚动体—滚道：65 帧周期的 0.05 s 接触压力

![局部滚动体—滚道：65 帧周期的 0.05 s 接触压力](screenshots/15-bearing-cycle.png)

实际本地工作台截图。研究 `25739b108b154e4992c12d9fedfb0cb0`。
### 滚动接触累计疲劳损伤：明确非瞬态、非裂纹生长

![滚动接触累计疲劳损伤：明确非瞬态、非裂纹生长](screenshots/16-bearing-damage.png)

实际本地工作台截图。研究 `25739b108b154e4992c12d9fedfb0cb0`。

## 平台导出的真实计算帧

### 静力应力：视角展示，非瞬态过程

![静力应力：视角展示，非瞬态过程](screenshots/20-static-stress.png)

来源：平台交付 `0cb946bf9f2d47ae` 的 `static/stress-last.png`，研究 `e1680fc9e78f4adeadf49eaef5435cdd`。未修改导出画面。
### 稳态温度：非升温过程

![稳态温度：非升温过程](screenshots/21-steady-temperature.png)

来源：平台交付 `0cb946bf9f2d47ae` 的 `thermal_steady/temperature-last.png`，研究 `de7a74e7db16444699484a05d24a7f26`。未修改导出画面。
### 瞬态温度：初始保存帧

![瞬态温度：初始保存帧](screenshots/22-transient-start.png)

来源：平台交付 `0cb946bf9f2d47ae` 的 `thermal_transient/temperature-first.png`，研究 `587121966d974523b2a9d39d88acb446`。未修改导出画面。
### 瞬态温度：中间保存帧

![瞬态温度：中间保存帧](screenshots/23-transient-middle.png)

来源：平台交付 `0cb946bf9f2d47ae` 的 `thermal_transient/temperature-middle.png`，研究 `587121966d974523b2a9d39d88acb446`。未修改导出画面。
### 瞬态温度：末保存帧

![瞬态温度：末保存帧](screenshots/24-transient-end.png)

来源：平台交付 `0cb946bf9f2d47ae` 的 `thermal_transient/temperature-last.png`，研究 `587121966d974523b2a9d39d88acb446`。未修改导出画面。
### 热结构应力：末保存帧，显示投影与原始极值分开

![热结构应力：末保存帧，显示投影与原始极值分开](screenshots/25-coupled-stress.png)

来源：平台交付 `0cb946bf9f2d47ae` 的 `thermo_mechanical/stress-last.png`，研究 `04eace3ebae441b3b3b1d2988951c31f`。未修改导出画面。
### 接触压力：实际映射的表面场

![接触压力：实际映射的表面场](screenshots/26-coupled-pressure.png)

来源：平台交付 `0cb946bf9f2d47ae` 的 `thermo_mechanical/contact_pressure-last.png`，研究 `04eace3ebae441b3b3b1d2988951c31f`。未修改导出画面。
### 接触间隙：末保存帧，灰色区域无可用场

![接触间隙：末保存帧，灰色区域无可用场](screenshots/27-coupled-gap.png)

来源：平台交付 `0cb946bf9f2d47ae` 的 `thermo_mechanical/contact_gap-last.png`，研究 `04eace3ebae441b3b3b1d2988951c31f`。未修改导出画面。
### 双向热结构温度：末保存帧

![双向热结构温度：末保存帧](screenshots/28-coupled-temperature.png)

来源：平台交付 `0cb946bf9f2d47ae` 的 `thermo_mechanical/temperature-last.png`，研究 `04eace3ebae441b3b3b1d2988951c31f`。未修改导出画面。

## 平台生成 PDF 的页面

### 汇总报告首页与完成范围

![汇总报告首页与完成范围](screenshots/30-report-cover.png)

来源：[平台原始 PDF](media/motor-analysis-report.zh-CN.pdf) 第 **1** 页，Poppler 125 dpi 渲染。
### 平台自动生成的可点击目录

![平台自动生成的可点击目录](screenshots/31-report-toc.png)

来源：[平台原始 PDF](media/motor-analysis-report.zh-CN.pdf) 第 **2** 页，Poppler 125 dpi 渲染。
### 材料属性与来源

![材料属性与来源](screenshots/32-report-materials.png)

来源：[平台原始 PDF](media/motor-analysis-report.zh-CN.pdf) 第 **6** 页，Poppler 125 dpi 渲染。
### 温度与热流图

![温度与热流图](screenshots/33-report-thermal.png)

来源：[平台原始 PDF](media/motor-analysis-report.zh-CN.pdf) 第 **33** 页，Poppler 125 dpi 渲染。
### 实际保存温度历程

![实际保存温度历程](screenshots/34-report-history.png)

来源：[平台原始 PDF](media/motor-analysis-report.zh-CN.pdf) 第 **26** 页，Poppler 125 dpi 渲染。
### 接触与装配间隙

![接触与装配间隙](screenshots/35-report-contact.png)

来源：[平台原始 PDF](media/motor-analysis-report.zh-CN.pdf) 第 **38** 页，Poppler 125 dpi 渲染。
### 平衡、收敛与验证证据

![平衡、收敛与验证证据](screenshots/36-report-verification.png)

来源：[平台原始 PDF](media/motor-analysis-report.zh-CN.pdf) 第 **41** 页，Poppler 125 dpi 渲染。
### 独立组件识别视图

![独立组件识别视图](screenshots/37-report-components.png)

来源：[平台原始 PDF](media/motor-analysis-report.zh-CN.pdf) 第 **5** 页，Poppler 125 dpi 渲染。

## 视频下载

- [热结构温度 MP4](media/thermo-temperature.mp4)
- [热结构应力 MP4](media/thermo-stress.mp4)
- [整机视角接触压力 MP4](media/contact-pressure.mp4)
- [整机视角接触间隙 MP4](media/contact-gap.mp4)

![温度动画缩小预览](media/thermo-temperature-preview.gif)

以画面实际物理时间为准。视频的 24 fps 是编码帧率，不是求解时间步。
