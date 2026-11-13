# 仓库范围、运行依赖与环境搭建

本文档介绍 VisHarness 公开仓库的目录组成、外部运行依赖、环境变量配置，以及离线合成测试的运行方法。

## 1. 仓库内容与外部依赖

本仓库包含 VisHarness 的核心编排源码、技术文档、离线单元测试以及一组示例素材：

| 组件 | 仓库内已提供 | 需在本地环境准备 |
|---|---|---|
| **编排与状态机** | `src/` 下的 Python 状态机/契约模块与 JS 运行脚本 | 兼容的外部 DSH 运行时（agent / session / llm 能力） |
| **图像后端接口** | 原生命令映射与参数契约定义 | 原生图像编辑器后端（控件目录、设置读回与全分辨率渲染） |
| **Python 图像处理** | 色彩空间、RAW 几何、透明合成与灰度派生源码 | Pillow、NumPy、LittleCMS、TIFF 编解码及 DNG 转换工具 |
| **示例与测试** | `paired photos/DSC_3734/`（NEF、Display P3 目标图、sRGB 预览图，共 35,225,397 B）及 5 个 `check_*.py` 测试脚本 | 更多真实照片、模型 API 密钥与运行授权 |

> 说明：仓库入口为 `src/dsh_stage_run.mjs`。共享基础模块中保留了部分历史 `SYSTEM` 与旧版 compact helper 供底层复用。

## 2. 环境变量配置

仓库通过环境变量解耦本地绝对路径（具体映射见根目录 `publication-scope.json`）：

- **`VISHARNESS_PYTHON`**：JS 运行时调用的 Python 解释器路径（默认回退为 `python`；若配置文件中已显式指定 `runtime.python` 则优先使用配置值）。
- **`VISHARNESS_FULL_AUTHOR_ROOT`**：需要读取原尺寸作者目标图时的根目录路径（默认流程使用短边 512 副本）。
- **`VISHARNESS_DNG_CONVERTER`**：外部 DNG 转换器可执行文件路径。

仓库通过 `.gitattributes` 将所有文本文件的换行符统一为 `LF`。

## 3. 运行离线合成测试

在已安装 `Pillow` 和 `NumPy` 的 Python 环境中，可直接运行以下 5 个独立脚本验证核心状态机、操作权限、Schema 生成、阶段调度与续接逻辑（全部使用合成夹具，无需真实照片、原生后端或在线模型）：

```bash
python -I -B -X utf8 src/check_photo_stage_tasks.py
python -I -B -X utf8 src/check_photo_stage_operations.py
python -I -B -X utf8 src/check_photo_stage_schema.py
python -I -B -X utf8 src/check_photo_phase_workflow.py
python -I -B -X utf8 src/check_photo_stage_continuation.py
```

## 4. 完整环境接入步骤

1. **熟悉接口**：先阅读 [系统架构](architecture.md) 与 [通信协议与资源](protocol-and-resources.md)。
2. **准备 Python 环境**：安装 Pillow、NumPy、LittleCMS 及所需 RAW/DNG 工具，并设置上述环境变量。
3. **跑通离线测试**：运行 5 个 `check_*.py` 脚本，确认状态机与契约逻辑通过。
4. **对接运行时与后端**：接入 DSH 运行时与原生修图后端，验证控件目录、设置读回与全尺寸渲染输出。
5. **准备输入并运行**：配置好目标/源图基线与模型 API 授权后，通过 `src/dsh_stage_run.mjs` 启动调色闭环。
