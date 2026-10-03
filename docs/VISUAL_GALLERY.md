# 可视化全览 / Visual gallery

[中文首页](../README.md) · [English README](../README.en.md) · [案例证据 / Case evidence](../examples/motor-double/README.md)

所有画面来自实际工作台、保存的求解结果或PDF渲染。点击图片查看原尺寸。下列九组全部展开。
All images are actual UI captures, saved solver figures or PDF renders. Click an image for full resolution. All nine groups are expanded.

| 图组 / Group | 来源与范围 / Provenance and scope |
|---|---|
| 01–03 | 2026-09-28 双端各1000 N，27 mm，73,985节点；CPU，10处理想粘结。只含两个外力。 / Two 1000 N end forces, 27 mm, 73,985 nodes; CPU, 10 bonded interfaces. |
| 04–08 | 插件分支保存的单端1000/1500/2000 N及自重、附件等效案例。几何、网格和测点口径与双端案例不同。 / Archived single-end load series with gravity and equivalent attachments; different geometry/mesh/probe scope. |
| 09 | 仓库已有的独立组件与GPU支架展示。 / Previously published component and GPU bracket examples. |

双端工况最大位移 **0.033088 mm**，原始积分点最大米塞斯应力 **43.8145 MPa**。云图色标为节点显示场；动画为静态位移演示。材料为教学假设，没有屈服或允许位移依据，不作强度合格结论，双端工况未验证网格收敛。
The two-end case has **0.033088 mm** maximum displacement and **43.8145 MPa** raw integration-point peak von Mises stress. Color maps use nodal display fields; animation illustrates static displacement. Materials are educational assumptions, with no yield/displacement allowables or mesh-convergence claim for this case.

历史图中的P点与收敛数值只属于其原始记录。双端案例P=(-155,0,0) mm在轴孔内，查询结果无定义；区域极值不能替代该测点。
Historical P-point and convergence values belong only to their source records. In the two-end model, P=(-155,0,0) mm lies in the bore and is undefined; regional extrema do not replace this point.

## 01 · 双端载荷 / Two shaft-end loads

**真实比例总位移，两端各1000 N / True-scale displacement, 1000 N at each end**

[![真实比例总位移，两端各1000 N / True-scale displacement, 1000 N at each end](images/motor-double/00-workbench.png)](images/motor-double/00-workbench.png)

**10组件装配 / Ten-component assembly**

[![10组件装配 / Ten-component assembly](images/motor-double/01-model.png)](images/motor-double/01-model.png)

**实际表面网格，C3D10体单元 / Actual surface mesh of C3D10 elements**

[![实际表面网格，C3D10体单元 / Actual surface mesh of C3D10 elements](images/motor-double/02-mesh.png)](images/motor-double/02-mesh.png)

**两个总力与底脚约束 / Two total forces and fixed feet**

[![两个总力与底脚约束 / Two total forces and fixed feet](images/motor-double/03-boundary.png)](images/motor-double/03-boundary.png)

**总位移与峰值 / Displacement and maximum location**

[![总位移与峰值 / Displacement and maximum location](images/motor-double/04-displacement.png)](images/motor-double/04-displacement.png)

**节点平均米塞斯云图 / Nodal-averaged von Mises display**

[![节点平均米塞斯云图 / Nodal-averaged von Mises display](images/motor-double/06-stress.png)](images/motor-double/06-stress.png)

**双端各1000 N静态变形，最大倍率500 / Two-end static deformation, maximum scale 500**

[![双端静态变形 / Two-end deformation](images/motor-double/10-deformation.gif)](images/motor-double/10-deformation.gif)

## 02 · 局部热点 / Component hotspots


**组件 8：输出轴 / Component 8: shaft**

[![组件 8：输出轴 / Component 8: shaft](images/motor-double/07-hotspot-8.png)](images/motor-double/07-hotspot-8.png)

**组件 6：前轴承等效环 / Component 6: front bearing equivalent**

[![组件 6：前轴承等效环 / Component 6: front bearing equivalent](images/motor-double/07-hotspot-6.png)](images/motor-double/07-hotspot-6.png)

**组件 7：后轴承等效环 / Component 7: rear bearing equivalent**

[![组件 7：后轴承等效环 / Component 7: rear bearing equivalent](images/motor-double/07-hotspot-7.png)](images/motor-double/07-hotspot-7.png)

**组件 4：前端盖 / Component 4: front end cover**

[![组件 4：前端盖 / Component 4: front end cover](images/motor-double/07-hotspot-4.png)](images/motor-double/07-hotspot-4.png)

**组件 5：后端盖 / Component 5: rear end cover**

[![组件 5：后端盖 / Component 5: rear end cover](images/motor-double/07-hotspot-5.png)](images/motor-double/07-hotspot-5.png)

**组件 1：机壳 / Component 1: housing**

[![组件 1：机壳 / Component 1: housing](images/motor-double/07-hotspot-1.png)](images/motor-double/07-hotspot-1.png)

**组件 2：底脚 / Component 2: foot**

[![组件 2：底脚 / Component 2: foot](images/motor-double/07-hotspot-2.png)](images/motor-double/07-hotspot-2.png)

**组件 9：定子 / Component 9: stator**

[![组件 9：定子 / Component 9: stator](images/motor-double/07-hotspot-9.png)](images/motor-double/07-hotspot-9.png)

## 03 · 报告与未定义结果 / Reports and undefined results

[下载28页中文PDF / Download the 28-page Chinese PDF](../examples/motor-double/report.zh-CN.pdf)。目录精简仅作用于该导出件。 / The shortened contents apply to this exported document.

**单页目录，仅标题与页码 / One-page contents, titles and page numbers**

[![单页目录，仅标题与页码 / One-page contents, titles and page numbers](images/motor-double/09-report-contents.png)](images/motor-double/09-report-contents.png)

**缺少屈服强度，安全系数无定义 / Yield data absent: safety factor undefined**

[![缺少屈服强度，安全系数无定义 / Yield data absent: safety factor undefined](images/motor-double/08-safety.png)](images/motor-double/08-safety.png)

## 04 · 模型、材料与组件 / Geometry, materials and components

**完整工作台 / Full workbench**

[![完整工作台 / Full workbench](images/motor/01-model.png)](images/motor/01-model.png)

**逐组件材料与来源 / Component materials and provenance**

[![逐组件材料与来源 / Component materials and provenance](images/motor/14-materials.png)](images/motor/14-materials.png)

## 05 · 变形、剖切与网格 / Deformation, sections and mesh

**静态位移往复播放，最大倍率717 / Static displacement playback, maximum scale 717**

[![静态位移往复播放，最大倍率717 / Static displacement playback, maximum scale 717](images/motor/04-deformation.gif)](images/motor/04-deformation.gif)

**网格线叠加 / Mesh overlay**

[![网格线叠加 / Mesh overlay](images/motor/02-mesh-stress.png)](images/motor/02-mesh-stress.png)

**真实比例×1 / True scale ×1**

[![真实比例×1 / True scale ×1](images/motor/03-displacement-real.png)](images/motor/03-displacement-real.png)

**积分点峰值定位 / Integration-point hotspot**

[![积分点峰值定位 / Integration-point hotspot](images/motor/05-stress-hotspot.png)](images/motor/05-stress-hotspot.png)

**Z向剖切 / Z section**

[![Z向剖切 / Z section](images/motor/06-section.png)](images/motor/06-section.png)

## 06 · 多种结果场 / Result fields

**最大主应力 / Maximum principal stress**

[![最大主应力 / Maximum principal stress](images/motor/17-principal-stress.png)](images/motor/17-principal-stress.png)

**等效弹性应变 / Equivalent elastic strain**

[![等效弹性应变 / Equivalent elastic strain](images/motor/15-elastic-strain.png)](images/motor/15-elastic-strain.png)

**弹性应变能密度 / Elastic strain-energy density**

[![弹性应变能密度 / Elastic strain-energy density](images/motor/16-energy.png)](images/motor/16-energy.png)

## 07 · 探针、路径与比较 / Probes, paths and comparison

本组为历史功能截图，不能作为双端工况的测点或收敛证据。 / Historical feature captures, not probe or convergence evidence for the two-end case.

**历史模型测点与刚度界面 / Historical-model probe and stiffness UI**

[![历史模型测点与刚度界面 / Historical-model probe and stiffness UI](images/motor/07-probe.png)](images/motor/07-probe.png)

**轴表面路径插值 / Interpolation along the shaft surface**

[![轴表面路径插值 / Interpolation along the shaft surface](images/motor/08-path.png)](images/motor/08-path.png)

**独立静力工况比较 / Independent static cases**

[![独立静力工况比较 / Independent static cases](images/motor/09-comparison.png)](images/motor/09-comparison.png)

**历史模型载荷—位移曲线 / Historical-model load–displacement curve**

[![历史模型载荷—位移曲线 / Historical-model load–displacement curve](images/motor/10-load-displacement.png)](images/motor/10-load-displacement.png)

**历史27/18/12 mm网格检查 / Historical 27/18/12 mm mesh study**

[![历史27/18/12 mm网格检查 / Historical 27/18/12 mm mesh study](images/motor/11-convergence.png)](images/motor/11-convergence.png)

## 08 · 报告图表 / Report figures

**历史报告目录 / Historical report contents**

[![历史报告目录 / Historical report contents](images/motor/12-report-contents.png)](images/motor/12-report-contents.png)

**单端力及等效附件载荷 / Single-end force and equivalent attachment loads**

[![单端力及等效附件载荷 / Single-end force and equivalent attachment loads](images/motor/13-report-boundary.png)](images/motor/13-report-boundary.png)

## 09 · 组件编辑与GPU / Component editors and GPU

**独立组件工作台 / Independent-component workbench**

[![独立组件工作台 / Independent-component workbench](component-workbench.png)](component-workbench.png)

**材料编辑 / Material editor**

[![材料编辑 / Material editor](component-material-editor.png)](component-material-editor.png)

**工况编辑 / Case editor**

[![工况编辑 / Case editor](component-case-editor.png)](component-case-editor.png)

**GPU支架算例 / GPU bracket example**

[![GPU支架算例 / GPU bracket example](gpu-workbench.png)](gpu-workbench.png)

**CPU/GPU资源与自检 / CPU/GPU resources and diagnostics**

[![CPU/GPU资源与自检 / CPU/GPU resources and diagnostics](gpu-resources.png)](gpu-resources.png)

## 文件与追溯 / Files and traceability

[双端案例完整说明 / Two-end case](../examples/motor-double/README.md) · [历史素材来源 / Historical asset provenance](images/motor/README.md) · [资产校验清单 / SHA-256 manifest](visual-assets.json)
