# 通信协议、上下文管理与资源控制

本文档说明 RetouchAgain 的分层通信协议、图像附件投递机制、默认模型与配额配置，以及运行期的可靠性保护边界。

## 1. 四层协议体系

当前实现基于 `dsh-stage-runtime/v2` 与 `stage-contract/v3`，将协议职责拆分为四层：
1. **运行协议**：定义单次任务的输入图像资格、目标范围与冻结基准。
2. **阶段提交合同**：动态定义 `photo_stage_turn` 的字段结构、当前可用动作与 `contract_revision`。
3. **资源配置（`src/photo_stage_resources.json`）**：集中管理模型选择、Token 预算、附件尺寸与异常监测阈值。
4. **执行账本**：由宿主真实记录请求派发、服务商回执、后端动作与图像证据。

## 2. 图像附件与上下文投递

- **双图流转**：每次视觉请求附带两张已登记的 upright RGB8 sRGB PNG（`target / current` 或 `actual-parent / current`；总览最长边 512，局部 ROI 图最长边 384）。
- **状态追踪**：每份请求与附件严格区分 `prepared`（本地已生成）、`sent`（已派发）与 `receipt`（收到服务商回执）。重发同一张缓存图像仅节省计算开销，不计为新的视觉证据。
- **上下文管理**：每轮按工作包投递当前焦点任务与必要预期，完整历史保留在本地通过游标检索。

## 3. 默认模型与 Token 配额

`src/photo_stage_resources.json` 中的默认配置如下：
- **默认模型**：`deepseek-official` / `deepseek-flash`，推理强度（`effort`）为 `high`。
- **生成配额**：默认 `requested_output_tokens = 65,536`（约 64K 单次生成预算）；静态配置记录的 provider 容量上限为 Context 1,000,000 / Output 393,216。
- **上下文参考区间**：日常工作上下文控制在 32–64K（较宽容上限为 128K），超出时通过优化工作包投影与分页检索保持精简。

## 4. 50 步编排参考模板

系统采用 **50 次请求** 作为标准任务的资源分配起点：**4 个检查点 + Tone 18 次 + Color 16 次 + Joint 8 次 + 机动 4 次**。

该模板用于指导各阶段的精力分配：分支间份额可按画面复杂度动态调剂，提前达到目标即可直接收束，若因局部补证或返修产生额外请求则按实际发生记账。

## 5. 四维资源计数

系统将运行消耗拆分为四个独立指标，清晰区分模型思考、视觉取证与后端渲染：

| 计数指标 | 统计对象 | 说明 |
|---|---|---|
| `model_requests_sent` | 实际发出的模型请求数 | 单次请求附带两张附件计为 1 次请求 |
| `distinct_visual_evidence` | 独立的新图像依据数 | 重复发送缓存图像不计入新证据 |
| `native actions / renders` | 后端实际写入与渲染次数 | 单次模型回合可对应多步原生原子事务 |
| `tokens / cost / time` | 回执返回的 Token、费用与耗时 | 若 thinking token 已含在输出总量中，不重复计费 |

## 6. 传输与并发保护边界

- **传输限制**：默认单次请求体与响应体上限各 16 MiB、文本上限 4 MiB、超时 600 秒。
- **并发锁与故障保护**：单一任务 scope 持有独占 lease；一旦检测到后端状态、回执与持久账本不一致，或存在未知状态的 `pending` 操作，立即停机核对，防止盲目重放造成状态污染。
- **费用异常监测**：基于实际回执统计输入、缓存读写与输出费用，当出现单次异常高消耗或持续无进展时自动暂停核对。

## 7. 任务生命周期（Prepare → Freeze → Authorize）

1. **Prepare（`metadata-only`）**：建立任务索引与初始协议，不触发 RAW 解码或渲染。
2. **基线制备与 Freeze**：完成初始基线渲染与角色核对后，通过 `freeze` 锁定输入源、初始设置与合同版本。
3. **Authorize**：通过授权窗口激活付费模型调用；从闭合检查点续跑时先校验产物与设置一致性。

## 8. 对应源码模块

- `src/photo_stage_resources.json`：模型、配额与监测配置。
- `src/dsh_stage_adapter.mjs`、`src/photo_request_primitives.mjs`：请求发送、回执解析与诊断。
- `src/dsh_stage_validation.py`、`src/photo_stage_continuation.py`：任务 `prepare / freeze` 与检查点续接。
- `src/photo_authorization_window.py`、`src/dsh_stage_authorize.py`：运行授权窗口管理。
