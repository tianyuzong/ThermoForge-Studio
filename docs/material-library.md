# 材料预设使用说明

已内置 26 张材料卡：15 种塑料、9 种金属、2 张教学演示卡。进入本地工作台，点击“编辑材料”，按分类或名称搜索，再选择材料并保存。工况设置的“组件材料”页也使用同一材料库。

选择材料只填入编辑表单，点击保存后才修改项目或组件。可编辑参数并另存自己的材料卡；旧工况和历史结果保留原有材料。中文指令可指明具体预设牌号；泛称“塑料”“尼龙”“POM”存在多个候选时需要进一步指定。

预设用于室温线弹性软件验证。厂家数据和建模近似分开标注，特别是泊松比；塑料不包含蠕变、非线性或打印层间各向异性。铜合金未指定状态时屈服为空，不输出屈服安全系数。应按实际牌号、供货状态、厚度和试验条件修改参数。

| 分类 | 材料 | E / GPa | ν | 密度 / kg·m⁻³ | 屈服 / MPa |
|---|---|---:|---:|---:|---:|
| 通用塑料 | ABS · Terluran GP-22 | 2.3 | 0.35 | 1040 | 45 |
| 通用塑料 | PP · TECAPRO MT（热稳定） | 2 | 0.4 | 930 | 34 |
| 通用塑料 | PE-HD · Robaflow PE 500 | 1.2 | 0.42 | 960 | 27 |
| 通用塑料 | PVC-U · Trovidur（白色硬 PVC） | 3 | 0.38 | 1450 | 45 |
| 工程塑料 | PC · TECANAT natural | 2.2 | 0.37 | 1190 | 69 |
| 工程塑料 | PC/ABS · Bayblend T65 XF | 2.35 | 0.37 | 1130 | 54 |
| 工程塑料 | PA6 · TECAMID 6（尼龙） | 3.3 | 0.39 | 1140 | 78 |
| 工程塑料 | PA66 · TECAMID 66（尼龙） | 3.5 | 0.39 | 1150 | 84 |
| 工程塑料 | POM-C · TECAFORM AH（共聚甲醛） | 2.8 | 0.35 | 1410 | 67 |
| 工程塑料 | POM-H · TECAFORM AD（均聚甲醛） | 3.4 | 0.35 | 1430 | 79 |
| 工程塑料 | PET · TECAPET natural | 3.1 | 0.38 | 1360 | 79 |
| 高性能塑料 | PEEK · TECAPEEK natural | 4.2 | 0.38 | 1310 | 116 |
| 高性能塑料 | PEI · TECAPEI natural | 3.2 | 0.37 | 1280 | 127 |
| 高性能塑料 | PSU · TECASON S | 2.7 | 0.37 | 1240 | 89 |
| 高性能塑料 | PVDF · TECAFLON PVDF | 2.2 | 0.38 | 1780 | 62 |
| 结构钢 | S235JR 结构钢（厚度 ≤16 mm） | 210 | 0.3 | 7850 | 235 |
| 结构钢 | S275JR 结构钢（厚度 ≤16 mm） | 210 | 0.3 | 7850 | 275 |
| 结构钢 | S355J2 结构钢（厚度 ≤16 mm） | 210 | 0.3 | 7850 | 355 |
| 不锈钢 | 304 / 1.4301 不锈钢（冷轧板带） | 200 | 0.3 | 7900 | 230 |
| 不锈钢 | 304L / 1.4307 不锈钢（冷轧板带） | 200 | 0.3 | 7900 | 220 |
| 不锈钢 | 316L / 1.4404 不锈钢（ASTM A240） | 200 | 0.3 | 8000 | 170 |
| 铝合金 | 6061-T6 铝合金（挤压型材） | 69 | 0.33 | 2700 | 240 |
| 铜及铜合金 | 紫铜 C11000（屈服待指定状态） | 117.21 | 0.328125 | 8910 | 未指定 |
| 铜及铜合金 | 黄铜 C26000（屈服待指定状态） | 110.32 | 0.34 | 8530 | 未指定 |
| 教学演示 | 演示钢材（假设值） | 210 | 0.3 | 7850 | 235 |
| 教学演示 | 演示铝材（假设值） | 70 | 0.33 | 2700 | 150 |

## 条件与来源

### ABS · Terluran GP-22

注塑级，23 °C 典型拉伸数据；E=2300 MPa，拉伸屈服=45 MPa。

- 室温短时、小应变、各向同性线弹性近似；不包含蠕变、温湿度依赖、塑性或打印层间各向异性。拉伸屈服仅用于参考应力比，不能作为塑料长期承载合格结论。
- 泊松比为建模近似，非该厂家数据表实测值；使用前按实际材料复核。
- [INEOS Styrolution · Terluran GP-22](https://www.ineos-styrolution.com/Product/Terluran_Terluran-GP-22_SKU300600120827_lang_de_DE.html)

### PP · TECAPRO MT（热稳定）

热稳定 PP 型材典型值；不是所有 PP 牌号的通用参数。

- 室温短时、小应变、各向同性线弹性近似；不包含蠕变、温湿度依赖、塑性或打印层间各向异性。拉伸屈服仅用于参考应力比，不能作为塑料长期承载合格结论。
- 泊松比为建模近似，非该厂家数据表实测值；使用前按实际材料复核。
- [Ensinger Engineering Plastics Manual · Material standard values](https://www.ensingerplastics.com/en-us/-/media/ensinger/files/document-teaser-files/brochures/shapes/engineering-plastics-manual-english.ashx)

### PE-HD · Robaflow PE 500

Robaflow PE-HD / PE 500 技术数据表指导值，短时拉伸。

- 室温短时、小应变、各向同性线弹性近似；不包含蠕变、温湿度依赖、塑性或打印层间各向异性。拉伸屈服仅用于参考应力比，不能作为塑料长期承载合格结论。
- 泊松比为建模近似，非该厂家数据表实测值；使用前按实际材料复核。
- [Röchling · Robaflow 技术数据表](https://www.roechling.com/fileadmin/assets/Technical%20Data%20Sheet%20Robaflow%C2%AE%C2%A0%20709731%20EN.pdf)

### PVC-U · Trovidur（白色硬 PVC）

未增塑硬 PVC 白色板材；不适用于软 PVC。

- 室温短时、小应变、各向同性线弹性近似；不包含蠕变、温湿度依赖、塑性或打印层间各向异性。拉伸屈服仅用于参考应力比，不能作为塑料长期承载合格结论。
- 泊松比为建模近似，非该厂家数据表实测值；使用前按实际材料复核。
- [Röchling · Trovidur PVC-U white](https://www.roechling.com/industrial/materials/thermoplastics/industrial-plastics/pvc-u/trovidur-pvc-u-white-591337)

### PC · TECANAT natural

未增强型材的厂家典型值；实际批次、含水率与试验条件须复核。

- 室温短时、小应变、各向同性线弹性近似；不包含蠕变、温湿度依赖、塑性或打印层间各向异性。拉伸屈服仅用于参考应力比，不能作为塑料长期承载合格结论。
- 泊松比为建模近似，非该厂家数据表实测值；使用前按实际材料复核。
- [Ensinger Engineering Plastics Manual · Material standard values](https://www.ensingerplastics.com/en-us/-/media/ensinger/files/document-teaser-files/brochures/shapes/engineering-plastics-manual-english.ashx)

### PC/ABS · Bayblend T65 XF

23 °C / 50% RH；拉伸模量 1 mm/min，拉伸屈服 50 mm/min。

- 室温短时、小应变、各向同性线弹性近似；不包含蠕变、温湿度依赖、塑性或打印层间各向异性。拉伸屈服仅用于参考应力比，不能作为塑料长期承载合格结论。
- 泊松比为建模近似，非该厂家数据表实测值；使用前按实际材料复核。
- [Covestro · Bayblend T65 XF](https://solutions.covestro.com/en/products/bayblend/bayblend-t65-xf_000000000006563805)

### PA6 · TECAMID 6（尼龙）

未增强 PA6 型材典型值；吸湿会改变模量和强度，需按实际调湿状态复核。

- 室温短时、小应变、各向同性线弹性近似；不包含蠕变、温湿度依赖、塑性或打印层间各向异性。拉伸屈服仅用于参考应力比，不能作为塑料长期承载合格结论。
- 泊松比为建模近似，非该厂家数据表实测值；使用前按实际材料复核。
- [Ensinger Engineering Plastics Manual · Material standard values](https://www.ensingerplastics.com/en-us/-/media/ensinger/files/document-teaser-files/brochures/shapes/engineering-plastics-manual-english.ashx)

### PA66 · TECAMID 66（尼龙）

未增强 PA66 型材典型值；吸湿会改变模量和强度，需按实际调湿状态复核。

- 室温短时、小应变、各向同性线弹性近似；不包含蠕变、温湿度依赖、塑性或打印层间各向异性。拉伸屈服仅用于参考应力比，不能作为塑料长期承载合格结论。
- 泊松比为建模近似，非该厂家数据表实测值；使用前按实际材料复核。
- [Ensinger Engineering Plastics Manual · Material standard values](https://www.ensingerplastics.com/en-us/-/media/ensinger/files/document-teaser-files/brochures/shapes/engineering-plastics-manual-english.ashx)

### POM-C · TECAFORM AH（共聚甲醛）

未增强型材的厂家典型值；实际批次、含水率与试验条件须复核。

- 室温短时、小应变、各向同性线弹性近似；不包含蠕变、温湿度依赖、塑性或打印层间各向异性。拉伸屈服仅用于参考应力比，不能作为塑料长期承载合格结论。
- 泊松比为建模近似，非该厂家数据表实测值；使用前按实际材料复核。
- [Ensinger Engineering Plastics Manual · Material standard values](https://www.ensingerplastics.com/en-us/-/media/ensinger/files/document-teaser-files/brochures/shapes/engineering-plastics-manual-english.ashx)

### POM-H · TECAFORM AD（均聚甲醛）

未增强型材的厂家典型值；实际批次、含水率与试验条件须复核。

- 室温短时、小应变、各向同性线弹性近似；不包含蠕变、温湿度依赖、塑性或打印层间各向异性。拉伸屈服仅用于参考应力比，不能作为塑料长期承载合格结论。
- 泊松比为建模近似，非该厂家数据表实测值；使用前按实际材料复核。
- [Ensinger Engineering Plastics Manual · Material standard values](https://www.ensingerplastics.com/en-us/-/media/ensinger/files/document-teaser-files/brochures/shapes/engineering-plastics-manual-english.ashx)

### PET · TECAPET natural

未增强型材的厂家典型值；实际批次、含水率与试验条件须复核。

- 室温短时、小应变、各向同性线弹性近似；不包含蠕变、温湿度依赖、塑性或打印层间各向异性。拉伸屈服仅用于参考应力比，不能作为塑料长期承载合格结论。
- 泊松比为建模近似，非该厂家数据表实测值；使用前按实际材料复核。
- [Ensinger Engineering Plastics Manual · Material standard values](https://www.ensingerplastics.com/en-us/-/media/ensinger/files/document-teaser-files/brochures/shapes/engineering-plastics-manual-english.ashx)

### PEEK · TECAPEEK natural

未增强型材的厂家典型值；实际批次、含水率与试验条件须复核。

- 室温短时、小应变、各向同性线弹性近似；不包含蠕变、温湿度依赖、塑性或打印层间各向异性。拉伸屈服仅用于参考应力比，不能作为塑料长期承载合格结论。
- 泊松比为建模近似，非该厂家数据表实测值；使用前按实际材料复核。
- [Ensinger Engineering Plastics Manual · Material standard values](https://www.ensingerplastics.com/en-us/-/media/ensinger/files/document-teaser-files/brochures/shapes/engineering-plastics-manual-english.ashx)

### PEI · TECAPEI natural

未增强型材的厂家典型值；实际批次、含水率与试验条件须复核。

- 室温短时、小应变、各向同性线弹性近似；不包含蠕变、温湿度依赖、塑性或打印层间各向异性。拉伸屈服仅用于参考应力比，不能作为塑料长期承载合格结论。
- 泊松比为建模近似，非该厂家数据表实测值；使用前按实际材料复核。
- [Ensinger Engineering Plastics Manual · Material standard values](https://www.ensingerplastics.com/en-us/-/media/ensinger/files/document-teaser-files/brochures/shapes/engineering-plastics-manual-english.ashx)

### PSU · TECASON S

未增强型材的厂家典型值；实际批次、含水率与试验条件须复核。

- 室温短时、小应变、各向同性线弹性近似；不包含蠕变、温湿度依赖、塑性或打印层间各向异性。拉伸屈服仅用于参考应力比，不能作为塑料长期承载合格结论。
- 泊松比为建模近似，非该厂家数据表实测值；使用前按实际材料复核。
- [Ensinger Engineering Plastics Manual · Material standard values](https://www.ensingerplastics.com/en-us/-/media/ensinger/files/document-teaser-files/brochures/shapes/engineering-plastics-manual-english.ashx)

### PVDF · TECAFLON PVDF

未增强型材的厂家典型值；实际批次、含水率与试验条件须复核。

- 室温短时、小应变、各向同性线弹性近似；不包含蠕变、温湿度依赖、塑性或打印层间各向异性。拉伸屈服仅用于参考应力比，不能作为塑料长期承载合格结论。
- 泊松比为建模近似，非该厂家数据表实测值；使用前按实际材料复核。
- [Ensinger Engineering Plastics Manual · Material standard values](https://www.ensingerplastics.com/en-us/-/media/ensinger/files/document-teaser-files/brochures/shapes/engineering-plastics-manual-english.ashx)

### S235JR 结构钢（厚度 ≤16 mm）

EN 10025-2 热轧板带，公称厚度 ≤16 mm，屈服采用表列最低值。

- 室温、各向同性、小变形线弹性参考；需匹配实际牌号、供货状态及尺寸范围。
- E=210 GPa、ν=0.30、ρ=7850 kg/m³ 为普通钢的建模近似；所列厂家来源仅支持该厚度范围的屈服下限。
- [Salzgitter Flachstahl · Non-alloy structural steels](https://www.salzgitter-flachstahl.de/en/products/hot-rolled-products/steel-grades/non-alloy-structural-steels.html)

### S275JR 结构钢（厚度 ≤16 mm）

EN 10025-2 热轧板带，公称厚度 ≤16 mm，屈服采用表列最低值。

- 室温、各向同性、小变形线弹性参考；需匹配实际牌号、供货状态及尺寸范围。
- E=210 GPa、ν=0.30、ρ=7850 kg/m³ 为普通钢的建模近似；所列厂家来源仅支持该厚度范围的屈服下限。
- [Salzgitter Flachstahl · Non-alloy structural steels](https://www.salzgitter-flachstahl.de/en/products/hot-rolled-products/steel-grades/non-alloy-structural-steels.html)

### S355J2 结构钢（厚度 ≤16 mm）

EN 10025-2 热轧板带，公称厚度 ≤16 mm，屈服采用表列最低值。

- 室温、各向同性、小变形线弹性参考；需匹配实际牌号、供货状态及尺寸范围。
- E=210 GPa、ν=0.30、ρ=7850 kg/m³ 为普通钢的建模近似；所列厂家来源仅支持该厚度范围的屈服下限。
- [Salzgitter Flachstahl · Non-alloy structural steels](https://www.salzgitter-flachstahl.de/en/products/hot-rolled-products/steel-grades/non-alloy-structural-steels.html)

### 304 / 1.4301 不锈钢（冷轧板带）

20 °C；EN 10088-2 冷轧板带 C，Rp0.2 屈服下限。不是冷作硬化强度。

- 室温、各向同性、小变形线弹性参考；需匹配实际牌号、供货状态及尺寸范围。
- 泊松比为建模近似，非该厂家数据表实测值；使用前按实际材料复核。
- [Outokumpu · Core range datasheet](https://www.outokumpu.com/-/media/files/products/core/outokumpu-core-range-datasheet.pdf)

### 304L / 1.4307 不锈钢（冷轧板带）

20 °C；EN 10088-2 冷轧板带 C，Rp0.2 屈服下限。不是冷作硬化强度。

- 室温、各向同性、小变形线弹性参考；需匹配实际牌号、供货状态及尺寸范围。
- 泊松比为建模近似，非该厂家数据表实测值；使用前按实际材料复核。
- [Outokumpu · Core range datasheet](https://www.outokumpu.com/-/media/files/products/core/outokumpu-core-range-datasheet.pdf)

### 316L / 1.4404 不锈钢（ASTM A240）

20 °C；ASTM A240 板带参考，Rp0.2 下限 170 MPa。

- 室温、各向同性、小变形线弹性参考；需匹配实际牌号、供货状态及尺寸范围。
- 泊松比为建模近似，非该厂家数据表实测值；使用前按实际材料复核。
- [Outokumpu · Supra range datasheet](https://www.outokumpu.com/-/media/files/products/supra/outokumpu-supra-range-datasheet.pdf)

### 6061-T6 铝合金（挤压型材）

T6 挤压型材，屈服采用厂家 SI 表列下限 240 MPa；不能直接代替板材或其他热处理状态。

- 室温、各向同性、小变形线弹性参考；需匹配实际牌号、供货状态及尺寸范围。
- E=69 GPa、ν=0.33、ρ=2700 kg/m³ 为该类铝合金的建模近似；厂家表用于屈服下限。
- [Hydro · Alloy 6061 extrusion datasheet](https://www.hydro.com/globalassets/01-products--services/extruded-profiles/americas/ena-resources/alloy-data-sheets/hydro_2019_data_sheet_6061.pdf)

### 紫铜 C11000（屈服待指定状态）

室温弹性及密度参考；退火/硬态尚未指定。

- 室温、各向同性、小变形线弹性参考；需匹配实际牌号、供货状态及尺寸范围。
- E 从 17000 ksi 换算；ν≈0.328 由 E=17000 ksi、G=6400 ksi 按各向同性关系推导；不表示厂家实测泊松比。
- 未指定供货/加工状态的可靠屈服强度，保留为空；不输出屈服安全结论。
- [Copper Development Association · C11000](https://alloys.copper.org/alloy/C11000)

### 黄铜 C26000（屈服待指定状态）

70/30 黄铜，室温弹性及密度参考；退火/硬态尚未指定。

- 室温、各向同性、小变形线弹性参考；需匹配实际牌号、供货状态及尺寸范围。
- E 从 16000 ksi 换算；泊松比为建模近似，非该厂家数据表实测值；使用前按实际材料复核。
- 未指定供货/加工状态的可靠屈服强度，保留为空；不输出屈服安全结论。
- [Copper Development Association · C26000](https://alloys.copper.org/alloy/C26000)

### 演示钢材（假设值）

教学/软件验证假设，不代表实际认证材料。

- 内置教学/软件验证假设；不代表特定牌号或认证材料数据

### 演示铝材（假设值）

教学/软件验证假设，不代表实际认证材料。

- 内置教学/软件验证假设；不代表特定牌号或认证材料数据

## 验证记录

2026-09-27：前端 TypeScript / Vite 构建通过；8 项定向测试通过，包括材料来源与旧工况快照、Codex 上下文材料目录，以及 ABS、6061-T6 和 C11000 三个真实 CalculiX CPU 拉杆试算。拉杆应力及轴向位移与解析解的误差均在 1% 内。