# 第三方组件与参考

Strength Studio 是独立实现。本地参考仓库 `tianyuzong/hot_sim_new`（考察提交 `68d922dd18f4ec5bac3ebf7219cfbb6c39ec485a`）未被修改，也没有作为子目录打包。

## CalculiX

- 版本：2.23，官方 Windows `ccx_static.exe`，本地命名为 `vendor/ccx.exe`。
- [官方 Windows 下载](https://www.dhondt.de/calculix_2.23_4win.zip)
- ZIP SHA-256：`87FC4E1721DF37370EC67D0A244BB14F9BF804B0A95DF49E2AE7369D30EC72E8`
- EXE SHA-256：`E18360EB32A5CD48F91A212C57069212CD8E27D2F798A599A78106BD0B3ECC1F`
- [官方源代码](https://www.dhondt.de/ccx_2.23.src.tar.bz2)，SHA-256：`9C88385C10FB04F5DC6C4E98027A51BEBDD8AEE3920E05190D6C1DD08357D6E7`
- 上游许可证：GNU GPL；随附官方 [GPL v2 文本](vendor/COPYING-CalculiX.txt) 和 [Windows 发行说明](vendor/README_Install.txt)。上游源代码中还包含各贡献组件的声明。
- 源码发行 ZIP 不含 CalculiX 可执行文件；`Install.ps1` 从上述官方地址下载并校验后安装。已经安装的本机目录有求解器。

## Python 依赖

版本由 `requirements.txt` 定义，实际 Windows/Python 3.14 验证环境锁定清单为 `requirements-windows-py314.lock`。pip 安装包保留各包的许可证与声明。

- [Gmsh](https://gmsh.info/) 4.15.2：GPL 及上游例外/商业许可条款。
- [NumPy](https://numpy.org/)、[SciPy](https://scipy.org/)、[h5py](https://www.h5py.org/)：各自 BSD 系列许可证。
- [FastAPI](https://fastapi.tiangolo.com/)、[Pydantic](https://docs.pydantic.dev/)、[trimesh](https://trimesh.org/)、[meshio](https://github.com/nschloe/meshio)、[pytest](https://docs.pytest.org/)：各上游许可证。
- [ReportLab](https://www.reportlab.com/opensource/)：上游 BSD 许可。
- Uvicorn、Starlette、httpx、python-multipart 等传递依赖随包保留声明。

## 前端

React、React DOM、Three.js、lucide-react 及其打包依赖来自 npm，准确版本见 `frontend/package-lock.json`。构建产物的上游许可证收集在 `frontend/THIRD_PARTY_LICENSES.txt`。字体使用系统字体；统一报告 PDF 优先嵌入本机 Microsoft YaHei，未随发行包复制 Windows 字体文件。

本清单记录来源与依赖关系，不将第三方组件的版权或许可改写为本项目所有。

## Codex 插件依赖

插件使用官方 Python MCP SDK `mcp==1.30.0`，独立环境依赖清单见 `requirements-plugin.lock`，不随源码分发安装后的第三方运行库。SDK 及依赖的许可证保留在 pip 安装包内。插件的 Codex 登录仍由 Codex 自身管理。

## NVIDIA CUDA / cuDSS（0.2 新增）

cuDSS 0.8.0.10 及传递依赖由 Install-GPU.ps1 从 PyPI 官方 NVIDIA 包安装，版本锁定在 requirements-gpu.lock。发行 ZIP 不包含 NVIDIA 动态库和上游头文件。自有 CUDA 核由本机 NVRTC 编译；不复制 CuPy 或 CalculiX 的有限元实现代码。

[cuDSS 软件许可](https://docs.nvidia.com/cuda/cudss/license.html) 与安装包中完整条款适用。NVIDIA 库按 AS IS 提供，不附适销性、特定用途适用性或不侵权保证；不承担因使用或无法使用产生的特殊、间接、附带或后果性损害。美国政府最终用户按 48 C.F.R. 2.101、12.212、227.7202 获得商业软件，权利限于适用许可。以上摘要不替代随包完整许可。


## 统一平台 0.6.0

源码来自天宇宗维护的 Strength Studio 与 ThermoForge；两个固定提交见 SOURCE_VERSIONS.json。迁入热代码在 thermal_legacy 命名空间，原有声明保留。新增 CPU 热模块使用 scikit-fem 12.0.2，绘图使用 Matplotlib，空间索引使用 Rtree；安装包中的上游声明适用。系统 Windows 字体文件不随源码 ZIP 复制。官方 CalculiX 在安装时按固定 SHA-256 下载，发行源码包不包含 NVIDIA 运行库。
