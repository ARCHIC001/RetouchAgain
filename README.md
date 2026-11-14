# Retouching Your Long-Horizon Professional Photo Again

**RetouchAgain** is a domain-specific **visual agent harness** for long-horizon professional photo retouching. Built on an external agent runtime, it decouples high-level visual reasoning from deterministic state governance, perceptual observation contracts, and atomic native execution.

---

## Introduction

Professional RAW photo retouching and reference-based color/tone matching constitute a **long-horizon, non-commutative, and tightly coupled** sequential decision-making problem. Adjusting global exposure simultaneously shifts perceived contrast, highlight roll-off, and chromatic saturation; dozens of continuous sliders, tone curves, and local masks interact across editing stages, causing early misjudgments to compound along the edit trajectory. Existing approaches struggle at both ends of the spectrum: **feed-forward neural retouching models** (e.g., single-shot LUT or pixel-level filters) lack non-destructive native editability and iterative self-correction, while **unconstrained VLM tool-calling** over raw editing APIs suffers from long-horizon context dilution, cross-stage parameter interference, sticky native tool states, and ungrounded parameter guessing without verifying rendered outcomes.

We argue that reliable long-horizon visual retouching is fundamentally a **harness engineering** problem rather than a single-turn prompting or end-to-end weight-fitting problem. Instead of training a task-specific pixel predictor or modifying the underlying foundation model, **RetouchAgain (VisHarness v20)** introduces a deterministic, domain-specific **visual agent harness** between a multimodal reasoning agent (DSH) and a native imaging backend. The agent focuses exclusively on visual comparison, discrepancy attribution, and parameter selection, while the harness enforces perceptual consistency, memory convergence, and atomic execution boundaries.

Specifically, the RetouchAgain harness is built around four tightly integrated mechanisms:
- **Phase-Gated Workflow & Attribution Isolation**: Global checkpoints (`G1–G4`) partition editing into `Tone` (Rec.709 unified grayscale, *Neutral-First* ordering, and optional `pre_tone_wb`), `Color` (contextual `wb_assessment` and `develop.wbPick`), and `Joint` residual refinement, decoupling luminance structure (`structure`) from chromatic appearance (`chroma`) with full backtracking support.
- **Perceptual & Color-Geometry Observation Contract**: A deterministic rendering pipeline enforces `IFD → ActiveArea → DefaultCrop → Orientation` geometry normalization, ICC-to-sRGB color conversion, and full-resolution rendering before downsampling, while `review_anchors` bind `Target` (goal gap) and `Effect` (step outcome) comparisons directly to verified image regions and 4×4 grid metadata.
- **Tri-Layer Memory Topology & Cross-Phase Review Debt**: Session state is separated into *Tool Knowledge*, a *Proposal Bank* (supporting typed `soft/hard` dependencies and ancestor-chain waiver inheritance), and an *Execution Ledger* (`selected_baseline` version tree and `active_masks_summary`). Each turn projects only a focused `context.work_packet` to prevent context drift, while `photo-review-debt/v2` and atomic `spawn_joint_task` track cross-stage side effects.
- **Execution Fence & Resumable Close Proofs**: All native mutations pass through a persistent execution fence that enforces `all-20` neutral initialization on newly created Linear/Radial masks and exact stage-scoped patching on same-ID reuse. Verifiable `close_proof` reconstruction and checkpoint continuation enable lossless recovery across interrupted sessions.

---

## 1. 分级调色闭环与阶段流转（v20）

为消除曝光、白平衡与色彩调整之间的耦合干扰，Harness 采用**“先定影调、再调色彩、最后联合精修”**的三阶段架构，并在节点之间设置 4 个全局检查点（`G1`–`G4`）。v20 在 `G1` 与 `Tone` 之间新增了可选的一次性前置粗白平衡（`pre_tone_wb`）及局部边界复核机制：

```mermaid
flowchart LR
    classDef flowStart fill:#eff6ff,stroke:#3b82f6,color:#1e3a8a,stroke-width:2px
    classDef flowMid fill:#dbeafe,stroke:#2563eb,color:#1e3a8a,stroke-width:2px
    classDef flowEnd fill:#bfdbfe,stroke:#1d4ed8,color:#172554,stroke-width:2.5px
    classDef origVisual fill:#fef3c7,stroke:#d97706,color:#78350f,stroke-width:2px
    classDef origGuard fill:#ffe4e6,stroke:#e11d48,color:#881337,stroke-width:2px

    G1(["G1 初诊<br/>彩色目标图 vs 基线 v0"]):::flowStart --> PreCheck{"严重偏色妨碍影调?"}:::origVisual
    PreCheck -->|"可选一次窄动作"| PreWB["pre_tone_wb<br/>(仅 wb.temp/tint → 彩色 Effect → Fresh G1)"]:::origGuard
    PreWB --> Tone
    PreCheck -->|"直接进入"| Tone["Tone 分支 · Neutral-First<br/>(统一灰度 · whites_blacks 起手)"]:::flowMid
    Tone -->|"选出最佳影调版"| G2(["G2 色彩重评<br/>wb_assessment 语境诊断"]):::origVisual
    G2 -->|"需要重调影调"| Tone
    G2 --> Color["Color 分支<br/>(彩色 · WB/wbPick/HSL/分级/局部复用)"]:::flowMid
    Color -->|"选出最佳色彩版"| G3(["G3 综合验收<br/>核查跨阶段债务 & 残差"]):::origVisual
    G3 -->|"影调/色彩退化"| Tone
    G3 -->|"色彩需返修"| Color
    G3 --> Joint["Joint 分支<br/>(残差 & spawn_joint_task 精修 + 导出)"]:::flowMid
    Joint -->|"导出实际 JPEG"| G4(["G4 交付确认<br/>目标图 vs 导出 JPEG"]):::flowEnd
    G4 -->|"返修（旧确认失效）"| Joint
    G4 -->|"确认交付 / Partial"| Done(["完成 & Close Proof"]):::flowEnd
```

- **Neutral-First 影调路线**：Tone 阶段推荐 `whites_blacks → exposure → highlights_shadows → contrast → master_curve → parametric_curve → local_tone → closure`，先立黑白锚点与中灰骨架，再做局部光影。
- **可控回退与独立维度**：任意检查点均可退回上游分支（G4 返修后需重新导出并观察新 JPEG）；流程完结（`complete`）、JPEG 核验与目标部分达成（`partial`）各自独立记录。

---

## 2. 视觉观察、归因隔离与色彩/几何管线

模型每一轮的判断精度取决于输入图像的色彩、几何与对比语境。Harness 在观察层严格规范图像角色、色彩空间转换与物理/色彩归因分离（`tone_assessment`）：

```mermaid
flowchart TB
    classDef flowStart fill:#eff6ff,stroke:#3b82f6,color:#1e3a8a,stroke-width:2px
    classDef flowMid fill:#dbeafe,stroke:#2563eb,color:#1e3a8a,stroke-width:2px
    classDef flowEnd fill:#bfdbfe,stroke:#1d4ed8,color:#172554,stroke-width:2.5px
    classDef origVisual fill:#fef3c7,stroke:#d97706,color:#78350f,stroke-width:2px
    classDef origMemory fill:#d1fae5,stroke:#059669,color:#064e3b,stroke-width:2px

    subgraph Input["数据输入与全分辨率渲染（蓝色主干流）"]
        RAW["RAW / 中间源<br/>IFD → ActiveArea → DefaultCrop → Orientation"]:::flowStart --> Render["后端全分辨率渲染<br/>(LinearRaw / DNG Opacity 白底合成)"]:::flowMid
        Target["作者成片目标<br/>(Display P3 → 真实 ICC 转 sRGB)"]:::flowStart
    end

    subgraph Derive["观察派生与归因隔离（琥珀金：原创视觉机制）"]
        Render -->|"Tone 阶段"| Gray["Rec.709 统一灰度转换<br/>(剥离色彩干扰)"]:::origVisual
        Render -->|"G1–G4 / Color / Joint"| RGB["彩色 sRGB 派生"]:::flowMid
        Target -->|"Tone 阶段"| GrayT["目标灰度派生"]:::origVisual
        Target -->|"其余阶段"| RGBT["目标彩色派生"]:::flowMid
        Gray & GrayT & RGB & RGBT --> PNG["upright · RGB8 · sRGB PNG (剥离 EXIF)<br/>总览最长边 ≤ 512 / 局部 ROI ≤ 384"]:::flowEnd
    end

    subgraph Compare["双图对比与锚点绑定（翠绿：原创锚点与记忆绑定）"]
        PNG --> Anchors["review_anchors 真实绑定<br/>(target/protected · explicit_cells/full_frame · 4×4 网格)"]:::origMemory
        Anchors --> TargetCmp["Target 对比: 目标图 vs 当前版<br/>(tone_assessment 区分 structure / chroma 归因)"]:::origVisual
        Anchors --> EffectCmp["Effect 对比: 父版本 vs 子版本<br/>(tone_boundary_review 检查核心/羽化/区外保护)"]:::origVisual
    end
```

- **归因隔离（`structure` vs `chroma`）**：G1 与 Tone 阶段显式区分“真实影调结构差距”和“由色度/饱和度引起的明暗观感差异”，把后者留给 HSL-L 或色彩分支处理。
- **语境化白平衡（`wb_assessment`）**：G2 区分 `target_intent`（中性参考 vs 作者刻意氛围 `intentional_cast`）与 `atmosphere.route`（交由 `grading` 或 `local_wb`），避免机械消色偏。

---

## 3. 记忆拓扑、工作包复用与 `photo-review-debt/v2`

为防止长程多轮对话稀释注意力，Harness 将会话状态拆分为**三层正交记忆**，并在 v20 引入 **`active_masks_summary`**、**`selected_baseline` 真实链**与 **逐焦点复核债务（`photo-review-debt/v2`）**：

```mermaid
flowchart TB
    classDef flowStart fill:#eff6ff,stroke:#3b82f6,color:#1e3a8a,stroke-width:2px
    classDef flowMid fill:#dbeafe,stroke:#2563eb,color:#1e3a8a,stroke-width:2px
    classDef flowEnd fill:#bfdbfe,stroke:#1d4ed8,color:#172554,stroke-width:2.5px
    classDef origMemory fill:#d1fae5,stroke:#059669,color:#064e3b,stroke-width:2px
    classDef origGuard fill:#ffe4e6,stroke:#e11d48,color:#881337,stroke-width:2px
    classDef origVisual fill:#fef3c7,stroke:#d97706,color:#78350f,stroke-width:2px

    subgraph Memory["权威会话三层记忆（翠绿：原创记忆拓扑）"]
        TK["1. 工具知识<br/>阶段权限 / 单位 / 原生范围"]:::origMemory
        PB["2. 建议库 (Proposal Bank)<br/>Issue & 稳定 task_groups · Typed 依赖 (soft/hard)<br/>Waiver 沿祖先链继承 · 遗漏不自动 retire"]:::origMemory
        EL["3. 执行账本 (Execution Ledger)<br/>selected_baseline (root→selected 真实链)<br/>active_masks_summary (center<0.1 提醒)"]:::origMemory
    end

    subgraph Flow["工作包投影与跨阶段债务流转（蓝色流 + 珊瑚红派生）"]
        TK & PB & EL --> WP["context.work_packet 唯一正文<br/>• 焦点 Group & 上一步五字段预期<br/>• 历史参数与弃支折为可检索索引"]:::flowMid
        EL -->|"真实操作触发"| Debt["photo-review-debt/v2<br/>(绑定稳定 focus_id & review_region)"]:::origVisual
        Debt -->|"no_regression / minor_acceptable"| Settled(["resolved / accepted_tradeoff"]):::flowEnd
        Debt -->|"spawn_joint_task"| JointSpawn["原子创建 joint-from:<debt_id><br/>(仅存证据与保护关系，待模型补方案)"]:::origGuard
        EL -->|"跨分支选版"| Reset["记录 BRANCH RESET NOTICE<br/>废弃分支 pending 标为不适用"]:::origGuard
    end
```

- **Typed 依赖与 Waiver 继承**：支持 `soft / hard`、`disposition / effect` 精确依赖约束；`soft skip / retire` 的豁免记录可沿选中祖先链自动继承。
- **跨阶段复核债务**：Tone 引起的色彩/边界问题进入 G2 债务，Color 的 WB 与 HSL-L 亮度副作用进入 G3 债务；发现跨阶段问题可通过 `spawn_joint_task` 原子派生 Joint 精修任务。

---

## 4. 任务分配、原子事务与单回合执行流

Harness 将调色任务拆解为 **Issue → Branch → Module → Fine Group**，并通过单回合 `photo_stage_turn` 完成“复核 → 更新建议库 → 选组 → 执行”闭环，同时提供**闭合证明（`close_proof`）与检查点续接（`continuation`）**：

```mermaid
sequenceDiagram
    autonumber
    box rgba(237, 233, 254, 0.5) 决策与适配层
        participant Agent as DSH Agent
        participant Adapter as JS Adapter / Schema
    end
    box rgba(209, 250, 229, 0.5) 原创 Harness 状态机与护栏
        participant Worker as Python 状态机 (v20)
    end
    box rgba(239, 246, 255, 0.7) 原生执行后端
        participant Native as 原生图像后端
    end

    Worker->>Adapter: 投影当前阶段合同 + context.work_packet + 双图 PNG
    Adapter->>Agent: 发起单回合推理请求
    Agent-->>Adapter: 提交 photo_stage_turn<br/>(review / anchors / bank_delta / selected_group / action)

    rect rgba(254, 243, 199, 0.45)
        Note over Adapter,Worker: v20 幂等规范化与容错分流
        Adapter->>Worker: 保留 raw/canonical/warnings (如 anchor 回落 unknown / 唯一别名归一)
        Worker->>Worker: 校验阶段权限、Typed 依赖、不适用 debt batch 隔离
    end

    alt 校验不通过（no-effect，未进入原生写入）
        Worker-->>Agent: 保留合法 bank 更新，仅返回结构/动作修正反馈
    else 通过 Execution Fence 校验
        rect rgba(255, 228, 230, 0.45)
            Note over Worker,Native: 原子事务与同 ID 局部复用
            Worker->>Native: 全局更新 / develop.wbPick / 蒙版事务<br/>(新建绑定真实 ID → all-20 中性初始化；复用仅 patch 本阶段字段)
            Native-->>Worker: 读回实际 DevelopSettings + 全分辨率渲染
        end
        Worker->>Worker: 更新版本树、close_proof 闭合证明与下一轮观察图
    end
```

- **同 ID 局部蒙版复用（v19–v20）**：复用既有 `mask ID` 时仅 patch 当前阶段字段（Tone 6 项、Color `Temp/Tint`、Joint 8 项），保留已有参数与几何；当 `typed action` 明确指向唯一现存 `mask ID` 或空列表新建时，自动兼容跳过冗余表单阻塞。

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
| [记忆拓扑与区域复核（v20）](docs/memory-review-v20.md) | Typed 依赖、活跃蒙版链、`photo-review-debt/v2` 与提交容错 |
| [Tone 阶段机制（v20）](docs/tone-guidance-v20.md) | 差距归因隔离、Neutral-First 路线、`pre_tone_wb` 与边界交接 |
| [白平衡机制（v20）](docs/white-balance-v20.md) | `wb_assessment` 语境诊断、`develop.wbPick` 与同 ID 局部复用 |
| [观察与色彩契约](docs/observation-and-color.md) | 图像角色、ICC 转换、RAW 几何、灰度公式与 ROI 对齐 |
| [操作与蒙版机制](docs/operations-and-masks.md) | 阶段控件白名单、参数单位、Linear/Radial 几何与 all-20 初始化 |
| [通信协议与资源控制](docs/protocol-and-resources.md) | 四层协议、64K 配额、50 步编排模板、四维计数与授权周期 |
| [v20 版本与运行说明](docs/v20-update-and-usage.md) | `dsh-stage-runtime-v20` 标识、闭合证明、检查点续接与新增测试 |
| [环境搭建与运行范围](docs/scope-and-environment.md) | 外部依赖说明、环境变量配置与离线合成测试入口 |
| [导出与同步指南](docs/manual-sync.md) | 维护者导出筛选、静态检查与 Git 推送流程 |

---

## 7. 核心源码与离线测试

### 主要模块入口
- `src/dsh_stage_run.mjs` / `src/dsh_stage_adapter.mjs`：主循环运行入口、Schema 动态校验与回执诊断。
- `src/dsh_stage_worker.py`：核心状态机，管理阶段流转、版本树、建议库与执行账本。
- `src/photo_stage_tasks.py` / `src/photo_proposal_bank.py` / `src/photo_working_context.py`：任务注册、Typed 依赖、活跃蒙版摘要与工作包构建。
- `src/photo_review_anchors.py` / `src/photo_phase_scheduler.py`：区域锚点绑定与 `photo-review-debt/v2` 跨阶段债务调度。
- `src/photo_tone_guidance.py` / `src/photo_pre_tone.py` / `src/photo_white_balance.py`：Tone 归因引导、`pre_tone_wb` 与白平衡诊断复用。
- `src/photo_stage_schema.py` / `src/photo_stage_operations.py`：动态提交合同与蒙版原子事务。
- `src/photo_stage_close_proof.py` / `src/photo_stage_continuation.py`：闭合证明重建与检查点无缝续接。
- `src/photo_stage_observation.py` / `src/photo_image_contract.py` / `src/raw_render_contract.py`：色彩、ICC、RAW 几何与灰度观察图派生。

### 运行离线合成测试
在安装 `Pillow` 和 `NumPy` 后，可直接运行以下 **11 个离线合成测试脚本**验证核心状态机、v20 引导机制、闭合证明与调度逻辑（使用合成夹具，无需外部模型或原生后端）：

```bash
python -I -B -X utf8 src/check_photo_working_context.py
python -I -B -X utf8 src/check_photo_review_anchors.py
python -I -B -X utf8 src/check_photo_tone_guidance.py
python -I -B -X utf8 src/check_photo_white_balance.py
python -I -B -X utf8 src/check_photo_stage_tasks.py
python -I -B -X utf8 src/check_photo_stage_operations.py
python -I -B -X utf8 src/check_photo_stage_schema.py
python -I -B -X utf8 src/check_photo_phase_workflow.py
python -I -B -X utf8 src/check_photo_stage_continuation.py
python -I -B -X utf8 src/check_dsh_stage_phase_worker_synthetic.py
python -I -B -X utf8 src/check_photo_stage_close_proof_synthetic.py
```

详细依赖与环境变量（`VISHARNESS_PYTHON`、`VISHARNESS_FULL_AUTHOR_ROOT`、`VISHARNESS_DNG_CONVERTER`）配置请参见 [环境搭建与运行范围](docs/scope-and-environment.md)，完整文件清单见 [publication-scope.json](publication-scope.json)。
