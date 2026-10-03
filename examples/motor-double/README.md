# 电机双端受力 / Motor with two shaft-end loads

[中文首页](../../README.md) · [English README](../../README.en.md) · [完整图集 / Gallery](../../docs/VISUAL_GALLERY.md) · [中文PDF / Chinese PDF](report.zh-CN.pdf) · [数值证据 / Evidence](evidence.json)

教学试算，2026-09-28。10组件、592面、27 mm C3D10网格；CPU 6线程。只施加两个面总力，各1000 N、方向均为全局−Z；底脚安装面XYZ固定，10处理想粘结传力。
Educational calculation, 2026-09-28. Ten components, 592 faces, 27 mm C3D10 mesh, CPU with six threads. Exactly two total face forces of 1000 N each in global −Z, fixed mounting feet and ten ideal bonded interfaces.

## 几何与选面 / Geometry and selections

原158实体装配简化为10组件，保留机壳—底脚、端盖—轴承等效环—输出轴的传力连接。转子槽填充后使用等效密度。转子与定子空气间隙不粘结。
The 158-solid assembly was simplified into ten components while retaining the housing/feet and cover/bearing-equivalent/shaft load path. Filled rotor slots use an equivalent density. The rotor–stator air gap is not bonded.

| 区域 / Region | 组件 / Body | 真实面 / Faces | 设置 / Setting |
|---|---:|---|---|
| 前轴端 / Front end | 8 | 361 | x=−155 mm；[0,0,−1000] N |
| 后轴端 / Rear end | 8 | 369 | x=205 mm；[0,0,−1000] N |
| 负Y底脚 / −Y foot | 2 | 220 | ux=uy=uz=0 |
| 正Y底脚 / +Y foot | 3 | 236 | ux=uy=uz=0 |

1000 N是整个实体端面的总力，不是每个节点的力；孔洞不加载。面号只适用于本次几何哈希，导入其他文件须重新核对。
Each 1000 N is the total force on the solid end-face area, not a force per node; holes are excluded. Face IDs apply only to this geometry hash and must be rechecked for another import.

## 材料与来源 / Materials and provenance

全部为用户授权的教学假设，非CAD内置或认证材料；屈服强度均为空。以下source原文在[evidence.json](evidence.json)逐组件保留，包括叠片各向同性及转子等效密度说明。
All properties are user-authorized educational assumptions, not CAD-embedded or certified material data. Yield strengths are absent. Each original Chinese source string, including lamination and equivalent-density notes, is retained in the evidence file.

> 用户授权的新教学试算假设（2026-09-28）；按几何位置和部件用途推定，非STEP内置数据，非实物牌号或认证材料。缺少强度证据，屈服强度留空。

| 组件 / Component | E / GPa | ν | ρ / kg·m⁻³ |
|---|---:|---:|---:|
| 1 · 机壳 / Housing | 110 | 0.26 | 7200.000000 |
| 2 · 负Y底脚 / −Y foot | 110 | 0.26 | 7200.000000 |
| 3 · 正Y底脚 / +Y foot | 110 | 0.26 | 7200.000000 |
| 4 · 前端盖 / Front cover | 110 | 0.26 | 7200.000000 |
| 5 · 后端盖 / Rear cover | 110 | 0.26 | 7200.000000 |
| 6 · 前轴承等效环 / Front bearing equivalent | 210 | 0.3 | 7850.000000 |
| 7 · 后轴承等效环 / Rear bearing equivalent | 210 | 0.3 | 7850.000000 |
| 8 · 输出轴 / Shaft | 210 | 0.3 | 7850.000000 |
| 9 · 定子 / Stator | 200 | 0.3 | 7650.000000 |
| 10 · 填槽转子 / Filled-slot rotor | 200 | 0.3 | 7204.887978 |

本工况没有自重，密度不产生重力载荷。理想粘结禁止分离、滑移和相对转动，不能替代真实滚动轴承、摩擦或螺栓预紧模型。
There is no gravity load in this case. Ideal bonds prohibit separation, slip and relative rotation; they do not represent actual bearing rolling, friction or bolt preload.

## 连接 / Interfaces

表中为从组件/面 → 主组件/面。容差用于离散接口映射，不移动节点。
Slave body/faces → master body/faces. Gap tolerances are used for discrete interface mapping without moving nodes.

| 名称 / Interface | 从 / Slave | 主 / Master | 最大间隙 / mm |
|---|---|---|---:|
| 机壳至负Y底脚 | 1 / [164, 166] | 2 / [222, 226] | 0.01 |
| 机壳至正Y底脚 | 1 / [170, 173] | 3 / [238, 242] | 0.01 |
| 前端盖至机壳 | 4 / [249] | 1 / [129] | 0.01 |
| 后端盖至机壳 | 5 / [295] | 1 / [139] | 0.01 |
| 前轴承外圈等效粘结至前端盖 | 6 / [340] | 4 / [292] | 0.5 |
| 后轴承外圈等效粘结至后端盖 | 7 / [344] | 5 / [338] | 0.5 |
| 输出轴至前轴承内孔 | 8 / [352] | 6 / [343] | 0.5 |
| 输出轴至后轴承内孔 | 8 / [351] | 7 / [347] | 0.5 |
| 转子内孔至输出轴 | 10 / [592] | 8 / [348] | 0.5 |
| 定子外圆至机壳内孔 | 9 / [370] | 1 / [130] | 0.5 |

## 结果 / Results

73,985节点、39,431单元、221,955自由度，4,344个粘结节点。计算阶段22.979 s，不含网格、报告和规划时间；不作为通用性能承诺。
73,985 nodes, 39,431 elements, 221,955 DOFs and 4,344 tied nodes. Compute phase: 22.979 s, excluding mesh, report and planning; not a general performance benchmark.

| 组件 / Component | 最大位移 / mm | 原始积分点最大米塞斯应力 / MPa |
|---|---:|---:|
| 1 · 机壳 / Housing | 0.002512 | 9.9025 |
| 2 · 负Y底脚 / −Y foot | 0.000349 | 7.2911 |
| 3 · 正Y底脚 / +Y foot | 0.000350 | 6.3949 |
| 4 · 前端盖 / Front cover | 0.003849 | 11.0286 |
| 5 · 后端盖 / Rear cover | 0.003202 | 10.4607 |
| 6 · 前轴承等效环 / Front bearing equivalent | 0.003921 | 30.9774 |
| 7 · 后轴承等效环 / Rear bearing equivalent | 0.003297 | 23.5028 |
| 8 · 输出轴 / Shaft | 0.033088 | 43.8145 |
| 9 · 定子 / Stator | 0.001641 | 10.7114 |
| 10 · 填槽转子 / Filled-slot rotor | 0.000951 | 6.4519 |

前/后轴端区域最大位移分别为0.033088 / 0.018008 mm。统计范围为选面相邻体单元的节点；不等同于仅端面节点。总应变能0.0245577643 J，力/力矩相对残差4.7383×10⁻⁹ / 3.1728×10⁻⁹，平衡通过。
Front/rear end-region maximum displacement: 0.033088 / 0.018008 mm. Regions include nodes of adjacent volume elements, not only face nodes. Total strain energy: 0.0245577643 J. Relative force/moment residuals: 4.7383×10⁻⁹ / 3.1728×10⁻⁹; equilibrium passed.

P=(-155,0,0) mm在轴端孔内，结果无定义，不吸附到实体。云图为节点显示场，报告强度极值为原始积分点；两者可以不同。仅一档网格，未验证收敛；缺少屈服强度和允许位移，不作安全合格结论。
P=(-155,0,0) mm is inside the shaft bore and is undefined, with no snapping. Color maps use nodal display fields; reported stress extrema use raw integration points. One mesh level only, no convergence verification, no yield or displacement allowables and no acceptance conclusion.

## 发布文件 / Published files

- `report.zh-CN.pdf`：28页，单页目录，合并重复热点条目，正文保留；源报告的30页改为28页，修改仅作用于该导出件。 / 28 pages with compact linked contents and preserved body; this export was shortened from 30 pages.
- `evidence.json`：完整工况、材料source原文、接口、原始指标与查询，不含会话或本机路径。 / Full case, original material sources, interfaces, metrics and queries, without conversations or machine paths.
- [图片与动图](../../docs/images/motor-double)：实际报告原图、2026-09-29工作台截图及静态位移动画（最大×500，原形对照，37帧约4秒）。 / Real report figures, UI capture and static displacement animation, maximum ×500 with original-shape overlay, 37 frames over approximately four seconds.

截图与动图未修改模型或数值。原始CAD、完整网格和计算数据库未公开，因此这些文件用于检查展示内容与配置，不是可独立重算的完整数据集。首次体验可使用内置支架。
The captures do not alter geometry or numerical results. Source CAD, full mesh and calculation database are not published, so this bundle supports review of presentation and configuration, not standalone reproduction. Use the built-in bracket for a first run.

原始作业标识 / Source job: `db45c4882c344bd988fd71e83de433d5`。所有面号与哈希见JSON。 / See JSON for face IDs and hashes.
