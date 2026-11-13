# Retouching Your Long-Horizon Professional Photo Again

RetouchAgain is a domain-specific visual agent harness for long-horizon photo retouching. Built on an external agent runtime, it orchestrates iterative visual assessment, tool execution, state tracking, and verification.

Retouching Your Long-Horizon Professional Photo Again（RetouchAgain）是一个面向**图像后期调色与参考图仿色（Color/Tone Matching）**的多轮视觉 Agent 框架。它将专业修图师的工作流抽象为可验证、可回退的工程闭环：让视觉大模型（DSH Agent）专注于看图对比、诊断问题与参数决策，而由确定性的 Python 状态机负责记忆存储、阶段权限隔离、色彩几何契约以及原生后端的原子执行。

---

## 1. 分级调色闭环与阶段流转

为了避免曝光、白平衡与色彩调整互相耦合干扰，RetouchAgain 采用**“先定影调、再调色彩、最后联合精修”**的三阶段架构，并在各阶段之间设置了 4 个全局检查点（`G1`–`G4`）：

```mermaid
flowchart LR
    G1(["G1 初诊<br/>目标图 vs 基线 v0"]) --> Tone["Tone 分支<br/>(统一灰度 · 影调)"]
    Tone -->|"选出最佳影调版"| G2(["G2 色彩重评<br/>目标图 vs 影调版"])
    G2 -->|"需要重调影调"| Tone
    G2 --> Color["Color 分支<br/>(彩色 · 白平衡/HSL/分级)"]
    Color -->|"选出最佳色彩版"| G3(["G3 综合验收<br/>目标图 vs 色彩版"])
    G3 -->|"影调/色彩退化"| Tone
    G3 -->|"色彩需返修"| Color
    G3 --> Joint["Joint 分支<br/>(彩色 · 残差联合精修 + 导出)"]
    Joint -->|"导出实际 JPEG"| G4(["G4 交付确认<br/>目标图 vs 导出 JPEG"])
    G4 -->|"返修（旧确认失效）"| Joint
    G4 -->|"确认交付 / Partial"| Done(["完成"])
```

- **可控回退**：任意检查点发现问题均可退回上游分支，G4 返修后必须重新导出并实际观察新 JPEG 才能确认交付。
- **独立结果维度**：流程结束（`complete`）、JPEG 已导出核验、目标部分达成（`partial`）各自独立记录，不把接口调用成功等同于画面达标。

---

## 2. 视觉观察与色彩/几何管线

模型每一轮的判断质量取决于输入图像的色彩与几何一致性。RetouchAgain 在观察层严格区分图像角色、色彩空间与对比类型：

```mermaid
flowchart TB
    subgraph Input["输入与全分辨率渲染"]
        RAW["RAW / 中间源<br/>IFD → ActiveArea → DefaultCrop → Orientation"] --> Render["后端全分辨率渲染<br/>(LinearRaw / DNG Opacity 白底合成)"]
        Target["作者成片目标<br/>(Display P3 → 真实 ICC 转 sRGB)"]
    end

    subgraph Derive["观察图派生 (upright · RGB8 · sRGB PNG · 剥离 EXIF)"]
        Render -->|"Tone 阶段"| Gray["Rec.709 统一灰度转换<br/>(剥离色彩干扰)"]
        Render -->|"G1-G4 / Color / Joint"| RGB["彩色 sRGB 派生"]
        Target -->|"Tone 阶段"| GrayT["目标灰度派生"]
        Target -->|"其余阶段"| RGBT["目标彩色派生"]
    end

    subgraph Compare["双图对比与 ROI 聚焦"]
        Gray & GrayT --> Pair["每次投递两张图<br/>总览最长边 ≤ 512 / 局部 ROI ≤ 384<br/>附带 4×4 网格元数据"]
        RGB & RGBT --> Pair
        Pair --> TargetCmp["Target 对比: 目标图 vs 当前版<br/>(回答: 离目标还差什么)"]
        Pair --> EffectCmp["Effect 对比: 父版本 vs 子版本<br/>(回答: 这步操作改变了什么)"]
    end
```

- **Tone 灰度隔离**：Tone 阶段通过统一的非线性/线性亮度公式将对比图转为灰度，确保模型专注影调层次而不被色偏误导。
- **跨图 ROI 资格**：`Effect` 对比在同一画布上直接复用父子 ROI；`Target` 局部对比则分别定位参考 ROI 与源 ROI，结合材质与受光角色确认可比性，并同步检查羽化邻域与区外保护区。

---

## 3. 记忆存储、建议库与复用机制

在长达数十轮的调色过程中，直接把全量历史和菜单塞进上下文会迅速稀释注意力。RetouchAgain 将会话状态拆分为**三层正交记忆**，并通过**工作包投影 + 游标检索**实现高效复用：

```mermaid
flowchart TB
    subgraph Memory["权威会话三层记忆 (Python Worker)"]
        TK["1. 工具知识<br/>阶段控件白名单 / 单位 / 原生范围"]
        PB["2. 建议库 (Proposal Bank)<br/>Issue 诊断 · Fine Group 参数 · 软依赖<br/>状态: active / needs_reassessment / resolved"]
        EL["3. 执行账本 (Execution Ledger)<br/>版本树 (Parent→Child) · 读回参数<br/>五字段预期 · Review Debt 复核债务"]
    end

    subgraph Reuse["上下文投影与分支复用"]
        WP["每轮工作包 (Working Package)<br/>• 当前焦点 Group<br/>• 上一步完整五字段预期<br/>• 待复核债务 & 少量下一候选"]
        Cursor["历史与长文本<br/>游标分页按需检索"]
        Revert["版本回退处理<br/>保留版本树，废弃分支债务标为<br/>not_applicable_selected_branch"]
    end

    TK & PB & EL --> WP
    EL -.->|"不塞满上下文"| Cursor
    EL -->|"选回早期更佳版本"| Revert
    PB -->|"上游曝光/WB/蒙版几何大改"| Reassess["自动标记下游 Group:<br/>needs_reassessment"]
```

- **动态重评而非盲目重放**：当上游曝光、全局白平衡或共享蒙版几何发生显著调整时，下游关联的 HSL 或色彩分级建议会被标记为 `needs_reassessment`，引导模型基于新画面重新计算数值。
- **跨阶段复核债务（Review Debt）**：Tone 引起的色彩偏移会在 G2 结算，Color 对明暗的副作用会在 G3 结算，确保跨阶段修改不留死角。

---

## 4. 任务分配与单回合执行流

RetouchAgain 将调色任务拆解为 **Issue → Branch → Module → Fine Group** 五层结构，并通过**单回合复合提交（`photo_stage_turn`）**驱动闭环，避免无谓的确认轮次：

```mermaid
sequenceDiagram
    participant Agent as DSH Agent
    participant Adapter as JS Adapter / Schema
    participant Worker as Python 状态机
    participant Native as 原生图像后端

    Worker->>Adapter: 投影当前阶段合同 + 工作包 + 双图 PNG
    Adapter->>Agent: 发起单回合推理请求
    Agent-->>Adapter: 提交 photo_stage_turn<br/>(review + bank_delta + selected_group + action + 5字段预期)
    Adapter->>Worker: 幂等规范化 (raw / canonical / warnings)
    Worker->>Worker: 校验阶段权限 / 参数单位 / 软依赖 Override
    alt 校验不通过（未进入写入）
        Worker-->>Agent: 保留合法 bank 更新，仅返回动作修正提示
    else 校验通过
        Worker->>Native: 执行全局更新或蒙版原子事务<br/>(绑定真实 ID → all-20 中性初始化 → 写入目标字段)
        Native-->>Worker: 读回实际 DevelopSettings + 全分辨率渲染
        Worker->>Worker: 记录父子版本并生成下一轮观察图
    end
```

- **软依赖与灵活路由**：默认路线（如 Tone 先曝光后曲线）仅作为推荐顺序；模型只要给出清晰的 `override_reason`，即可跳过常规顺序优先处理画面瓶颈，或对同一细组进行多轮迭代。
- **蒙版原子事务**：新建 Linear/Radial 蒙版时，宿主会先绑定原生真实 ID 并对全部 20 个局部字段执行中性归零（`amount=100`，其余 19 项置 `0`），彻底消除原生工具的历史滑杆记忆，随后再写入本轮目标字段；跨阶段复用同一物理蒙版时则保留既有参数，只增量更新当前阶段字段。

---

## 5. 示例素材包：`DSC_3734`

仓库内置了一组完整的三角色示例文件（`paired photos/DSC_3734/`，总计 35,225,397 B），用于演示不同图像角色与色彩空间配置：

| 文件 | 角色 | 规格 | 大小 |
|---|---|---|---:|
| [DSC_3734.NEF](<paired photos/DSC_3734/DSC_3734.NEF>) | 原始 RAW 编辑输入 | IFD 8280×5520 | 34,871,808 B |
| [DSC_3734.jpg](<paired photos/DSC_3734/DSC_3734.jpg>) | 作者成片目标 | 512×768, Display P3 | 202,334 B |
| [oDSC_3734.jpg](<paired photos/DSC_3734/oDSC_3734.jpg>) | RAW 基础显影预览 | 512×768, sRGB | 151,255 B |

---

## 6. 文档导航

| 文档 | 内容说明 |
|---|---|
| [系统架构](docs/architecture.md) | 分层架构、G1–G4 检查点、细组组织与单回合复合提交 |
| [工作记忆与任务路由](docs/memory-and-task-routing.md) | 建议库数据结构、状态维度、软依赖 Override 与复核债务 |
| [观察与色彩契约](docs/observation-and-color.md) | 图像角色、ICC 转换、RAW 几何、灰度公式与 ROI 对齐 |
| [操作与蒙版机制](docs/operations-and-masks.md) | 阶段控件白名单、参数单位、Linear/Radial 几何与 all-20 初始化 |
| [通信协议与资源控制](docs/protocol-and-resources.md) | 四层协议、64K 配额、50 步编排模板、四维计数与授权周期 |
| [环境搭建与运行范围](docs/scope-and-environment.md) | 外部依赖说明、环境变量配置与离线合成测试入口 |
| [导出与同步指南](docs/manual-sync.md) | 维护者导出筛选、静态检查与 Git 推送流程 |

---

## 7. 核心源码与离线测试

### 主要模块入口
- `src/dsh_stage_run.mjs`：主循环运行入口，串联 DSH Agent 与阶段状态机。
- `src/dsh_stage_adapter.mjs`：模型请求封装、Schema 动态校验与回执诊断。
- `src/dsh_stage_worker.py`：核心状态机，管理阶段流转、版本树、建议库与执行账本。
- `src/photo_stage_tasks.py` / `src/photo_proposal_bank.py`：任务注册、软依赖解析与工作包构建。
- `src/photo_stage_schema.py` / `src/photo_stage_operations.py`：动态提交合同与蒙版原子事务。
- `src/photo_stage_observation.py` / `src/photo_image_contract.py` / `src/raw_render_contract.py`：色彩、ICC、RAW 几何与灰度观察图派生。

### 运行离线合成测试
在安装 `Pillow` 和 `NumPy` 后，可直接运行以下 5 个脚本验证核心状态机、操作权限与调度逻辑（使用合成夹具，无需外部模型或原生后端）：

```bash
python -I -B -X utf8 src/check_photo_stage_tasks.py
python -I -B -X utf8 src/check_photo_stage_operations.py
python -I -B -X utf8 src/check_photo_stage_schema.py
python -I -B -X utf8 src/check_photo_phase_workflow.py
python -I -B -X utf8 src/check_photo_stage_continuation.py
```

详细依赖与环境变量（`VISHARNESS_PYTHON`、`VISHARNESS_FULL_AUTHOR_ROOT`、`VISHARNESS_DNG_CONVERTER`）配置请参见 [环境搭建与运行范围](docs/scope-and-environment.md)，完整文件清单见 [publication-scope.json](publication-scope.json)。
