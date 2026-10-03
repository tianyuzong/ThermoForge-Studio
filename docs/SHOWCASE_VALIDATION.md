# 展示发布验证 / Showcase publication checks

日期 / Date: 2026-09-29。版本 / Version: 0.5.0。

本次将CPU/Codex插件分支整合到main，增加中英文README、完整可视化图集、双端载荷案例证据与单页目录PDF。双端结果来自2026-09-28实际完成的计算，不为制作展示而重新求解。
This publication brings the CPU/Codex plugin branch into main and adds bilingual READMEs, a complete gallery, two-end motor evidence and a PDF with compact contents. Motor results come from the completed 2026-09-28 run; no new motor solve was performed for presentation.

## 实际检查 / Checks performed

| 检查 / Check | 结果 / Result |
|---|---|
| Python默认回归 / Default regression | **92 passed, 17 skipped, 1 warning in 264.70 s** |
| 跳过项 / Skips | 17项GPU硬件用例，默认需显式`--gpu`。 / GPU hardware tests require explicit opt-in. |
| 提示 / Warning | Starlette/TestClient的httpx弃用提示。 / A Starlette/TestClient httpx deprecation warning. |
| 前端 / Frontend | `npm ci`、`npm run build`成功，TypeScript与Vite构建通过。 / TypeScript and Vite production build passed. |
| 静态变形 / Static deformation | `node tests/visualization.mjs`通过端点、位移插值、零场与原始数据保留检查。 / Endpoints, interpolation, zero field and source preservation passed. |
| 插件清单 / Plugin manifest | `plugin-creator/scripts/validate_plugin.py`通过。 / Passed the plugin validator. |
| 浏览器 / Browser | 实际双端工况、两个载荷、×1位移场、×500静态动画与原形对照核对并截图。 / Actual case, two loads, ×1 field, ×500 playback and original overlay inspected. |
| 报告 / Report | 导出的28页PDF、单页目录及书签/跳转已核对；报告图、截图、动画进行目视检查。 / Exported 28-page PDF, contents, bookmarks and links checked; figures and animation visually inspected. |
| 打包 / Packaging | 白名单打包，包含双语README、图集与案例；校验归档及SHA-256清单。 / Allowlisted package includes both READMEs, gallery and case; archive and hashes checked. |

回归运行使用已安装Python和CalculiX，在独立数据目录完成。首次运行因Windows深层路径超过长度限制失败；增加`STRENGTH_TEST_ROOT`后改用短测试根目录，全部默认用例通过。没有修改生产项目数据或系统路径限制。
Regression used installed Python and CalculiX with isolated data. The initial deep-checkout run hit Windows path-length limits. An optional `STRENGTH_TEST_ROOT` was added and the suite passed with a shorter isolated root, without changing production projects or system policy.

```powershell
$env:STRENGTH_TEST_ROOT = "$env:TEMP/strength-studio-tests"
.\.venv\Scripts\python.exe -m pytest tests -q
cd frontend
npm ci
npm run build
node tests/visualization.mjs
cd ..
.\.venv\Scripts\python.exe scripts\package_plugin.py
```

## 随发布保留的修复 / Included fixes

- 重新导入模型时，只选择与当前模型对应的任务；模型画布不再从另一模型的历史任务继承载荷和约束。 / On import, tasks and model-view boundary conditions must match the current model.
- GPU缓存清理只清理已加载的可选接触模块，不主动导入接触优化依赖；两项回归覆盖未加载及已加载两种情况。 / GPU cache cleanup does not import optional contact dependencies; two regressions cover unloaded and loaded contact modules.
- 发布包保留英文README，并将插件首页及安装说明指向main。 / Packages retain the English README; plugin homepage and installation instructions target main.

## 本次未覆盖 / Outside this verification

未重新执行GPU硬件回归、GPU性能基准、全新Windows安装或Ansys对照；也未为双端载荷新增网格收敛或认证材料数据。历史GPU与单端载荷记录保留独立来源，不作为本次新增验收。
GPU hardware regressions, GPU benchmarks, clean-machine installation and Ansys comparison were not rerun. No mesh-convergence study or certified material data was added to the two-end case. Historical GPU and single-end records retain separate provenance.
