# ThermoForge Studio 验证记录

当前版本 **0.8.0** 的完整回归、真实主界面、帧一致性、独立报告/视频导出及安装验收，见 [本轮优化验收记录](docs/OPTIMIZATION_ACCEPTANCE_0_8.md)。以下旧版本结果保留原始日期与范围，不代表本轮重新执行过全部大型算例或 GPU。

下文保留 0.6 合并底座的历史验证证据。0.7 一键套件新增电机瞬态双向接触及导出流程，其当前状态单独记录在 [ONE_CLICK_VALIDATION.md](ONE_CLICK_VALIDATION.md)；旧热致闭合小模型的通过结论不等同于新电机工况通过。

生成时间（UTC）：2026-09-30T14:36:51.910393+00:00

本记录由已保存的测试及求解输出生成。执行完成、解析误差、网格收敛和实物材料标定分别列示。

## 软件回归

| 测试组 | 通过 | 跳过 | 失败 | 原始记录 |
|---|---:|---:|---:|---|
| structural_and_forge_cpu | 129 | 18 | 0 | `validation\structure-and-forge-final.xml` |
| legacy_thermal_cpu | 204 | 2 | 0 | `validation\thermal-legacy.xml` |
| gpu | 16 | 0 | 0 | `validation\gpu-regression.xml` |
| gpu_additional | 2 | 0 | 0 | `validation\gpu-additional.xml` |
| fatigue_domain_extension | 2 | 0 | 0 | `validation\fatigue-domains.xml` |
| wear_block_extension | 5 | 0 | 0 | `validation\wear-blocks.xml` |
| verification_provenance | 1 | 0 | 0 | `validation\verification-provenance.xml` |
| native_restart | 1 | 0 | 0 | `validation\native-restart.xml` |
| physical_step_balance | 7 | 0 | 0 | `validation\physical-step-balance.xml` |
| final_coupled_balance | 3 | 0 | 0 | `validation\final-coupled-balance.xml` |

按测试身份去重：361 项通过，0 项失败，2 项未执行。

跳过项主要为显式选择执行的 GPU 测试；独立 GPU 记录列出实际硬件执行结果。附加单项与主回归不重复计数。

## 数值基准

- 原电机静力同网格回归：通过；位移差异 0.0000%，应力差异 0.0000%。
- 解析导热、瞬态平板、相变能量、自由膨胀、完全约束热应力、异材串联杆、循环计数、均值修正、Miner、Findley、库仑滑动、Archard 及表面更新见对应 pytest 数值断言。

| Hertz 场景 | 节点数 | 压力误差 | 接触区尺寸误差 | 通过 |
|---|---:|---:|---:|---|
| hertz-45-k2.0e+14-g400 | 69823 | 2.580% | 1.995% | True |
| hertz-36-k2.0e+14-g400 | 74092 | 4.696% | 2.453% | True |
| hertz-30-k2.0e+14-g400 | 81254 | 4.465% | 3.080% | True |
| hertz-line-16-K1e+15 | 64897 | 0.251% | 0.101% | True |
| hertz-line-20-K1e+15 | 40462 | 0.658% | 0.576% | True |
| hertz-line-25-K1e+15 | 27426 | 2.148% | 1.792% | True |

## 应用与收敛

| 工况 | 执行状态 | 帧数 |
|---|---|---:|
| motor-fatigue-derived | completed | 5 |
| motor-clearance-derived | completed | 11 |
| local-bearing-resumed | completed | 65 |
| motor-thermo-contact-final | completed | 1 |
| motor-contact-balanced | completed | 1 |
| gear-tooth-engagement-final | completed | 73 |
| rolling-grid-45 | completed | 65 |
| rolling-grid-36 | completed | 65 |
| rolling-step-129 | completed | 129 |
| thermal-gap-heating-cooling | completed | 17 |
| thermal-gap-restart-regression | completed | 9 |
| thermal-gap-run | completed | 9 |
| thermal-gap-step-half | completed | 9 |
| thermal-gap-step-quarter | completed | 9 |
| thermal-gap-step-eighth | completed | 9 |
| thermal-gap-step-sixteenth | completed | 9 |
| thermal-gap-step-thirtysecond | completed | 9 |

- 滚动接触三档局部网格：verified。比较值及阈值见 `validation/acceptance.json`。

- 滚动接触步长减半：verified。比较值及阈值见 `validation/acceptance.json`。

- 瞬态接触耦合步长减半：verified。比较值及阈值见 `validation/acceptance.json`。

### 瞬态步长细化的全部记录

| 相邻工况 | 相同时刻最大温差 K | 相同时刻压力差 / 细步长峰值 | 结论 |
|---|---:|---:|---|
| thermal-gap-run → thermal-gap-step-half | 4.92411 | 14.931% | sensitive |
| thermal-gap-step-half → thermal-gap-step-quarter | 2.85012 | 8.599% | sensitive |
| thermal-gap-step-quarter → thermal-gap-step-eighth | 1.56277 | 22.650% | sensitive |
| thermal-gap-step-eighth → thermal-gap-step-sixteenth | 0.866187 | 12.798% | sensitive |
| thermal-gap-step-sixteenth → thermal-gap-step-thirtysecond | 0.461344 | 2.627% | verified |

- 热致闭合与冷却分离：True。
- 原生重启动与完整历史重放：True。
- Hertz 点接触三档收敛：verified；线接触三档收敛：verified。

- 齿轮18°单齿节：驱动 18.000002°，从动 -17.999698°；最大传动偏差 0.052%（限值1%），接触连续性与全局平衡通过；不含局部压力峰值或疲劳寿命验收。

## 交付检查

| 检查 | 已保存结果 |
|---|---|
| 全新环境安装及启动／停止 | True |
| 原结构浏览器流程 | True |
| 多物理场浏览器流程 | True；6 项 |
| MCP 类型化接口 | True；25 个工具 |
| 便携示例配置校验 | True；22 个工况 |
| PDF 逐页视觉检查 | True；8 份报告 |

## 适用边界

- 疲劳和滚动接触曲线为软件演示数据，不是电机／轴承实测材料。
- 整机接触局部网格仅用于流程展示，不能把其尖角峰值作为已验证寿命。
- 齿轮单齿节与材料点整周是不同周期；保留旧完整一周失败试算，不能据单齿节通过宣称整周验证完成。
- Hertz 三档收敛与每档解析误差分别列示；未完成或未达到阈值的场景不能标为通过。
- 磨损几何更新在周期接触研究中执行；瞬态热结构研究保留摩擦历史，但不同时演变磨损几何。
