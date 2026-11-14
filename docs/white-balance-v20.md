# 白平衡：视觉基准、局部复用与跨阶段复核（截至 v20）

> 发布派生版/非历史原稿。本文补充既有说明，不替换此前正文；描述通用机制，不包含实验结果或收费授权。

## 1. 判断白平衡问题，而不是追求无条件中性

G2 在实际彩色作者／当前图上重评，不把 Tone 灰度判断当作新颜色证据。`review.wb_assessment` 将三个维度分别记录：

- `reference_status`：`available` 或 `not_available`。有参照时记录 1–2 组独立的作者／当前格号、材质、受光、`neutrality_basis` 及温度／Tint 残差；没有可靠参照时允许空 `reference_pairs`。
- `target_intent`：`neutral`、`intentional_cast` 或 `uncertain`，附 `intent_reason`。材质自身颜色、环境反射及作者有意氛围不是需要自动消除的色偏。
- `wb_task_status`：`needed`、`not_needed` 或 `uncertain`。没有任务是合法判断，不强迫修改 WB。

`atmosphere.route` 可为 `grading`、`local_wb`、`none` 或 `uncertain`，附理由。暗部／高光氛围可以交给分级，空间分离的偏色可以交局部；不由宿主从文字自动生成参数。

上述字段位于 assessment 层级，不应复制成每个 reference pair 的另一套分类。宿主绑定实际 observation、版本、阶段、比较模式和视图范围；模型判断不是测量真值。

## 2. 实际读回、软步长与可选原生采样

工作包提供实际 WB `mode`、`scale`、`temp`、`tint`，不把未知标度假定为 Kelvin。对已确认 Kelvin、约 5000K 的情形，150–250K 可作轻微调整的量级提示，约 500K 及以上应有更明确依据；Tint 约 3–6、2–3 轮也只是软指导，不是硬次数或强制双轴修改。实际提交仍为绝对值。

Color／Joint 可在控件真实可用时选择 `develop.wbPick`。采样必须有物理中性依据，使用完整定向源坐标；原生采样是定向 `SourceLevel::Thumb` 的 5×5 邻域，不是全分辨率成片测量。宿主验证返回值与实际读回、有效 scale，以及非 WB 设置未改变。静态控件目录本身不足以证明当前源资格；不可用时由模型重新选择合法动作，不伪造采样结果。

## 3. 同一实际蒙版的精确 patch

Tone 局部只开放六项影调；Color 局部只开放 Temp／Tint；Joint 可使用这八项。复用同一实际 mask ID 时只 patch 本次字段，保留既有影调、几何、可见性和反相状态。新建使用原生返回真实 ID，并执行 all-20 中性初始化，再写目标值；不能假定 `mask.add` 返回列表末项就是新 ID。

截至 v19–v20，重复表单不应阻挡已经明确的合法动作：

1. **真实列表为空且明确 create**：局部 WB 新建可使用模型原始 rationale／目标与真实空清单登记依据，不为填写空复用表单另发确认轮。
2. **明确唯一实际 ID 的 adjust**：当本次 typed action 仅指向一个实际存在的 mask ID，重复 `wb_local_review` 缺失或不合格时，可保留原始说明和 warning，以明确 ID 选择继续校验。宿主没有据此证明两个区域语义相同，也不猜测新几何。
3. **仍然保留的限制**：未知 ID、阶段外参数仍拒绝；已有蒙版时再 create 仍需解释不同用途，不能把 adjust 兼容当作无理由重复新建许可。

## 4. effect 支持不等于作者匹配

父／子 effect 只回答“实际改变了什么”，作者／当前 target 才回答“离目标还有什么差距”。标 resolved 需要适用的当前 target 依据，可复用同阶段同版本已实际记录的依据，不另加普遍确认轮。resolved 也不是永久冻结，新证据可以显式 revise。

实际 Color WB 变化在原同条 G3 债务上增加区域影调／边界关注，不另造审批队列。Kelvin 温差、Tint、mode、有效 picker 和局部 WB 差异按实际设置判断；被拒提案、零变化不生成执行事实。待复核不冒已解决，也不因 pending 就阻止合法前进。

## 5. 代码入口与边界

- [`photo_white_balance.py`](../src/photo_white_balance.py)：assessment、局部复用、读回上下文与耦合信息。
- [`dsh_stage_worker.py`](../src/dsh_stage_worker.py)：当前合同与显式 ID 兼容接线。
- [`photo_stage_operations.py`](../src/photo_stage_operations.py)：阶段权限和蒙版事务。
- [`check_photo_white_balance.py`](../src/check_photo_white_balance.py)：离线合成检查。

合成检查证明合同和工程保护，不保证模型识别中性材质、采样点选择或目标匹配质量。[v20 使用与版本承接](v20-update-and-usage.md)列出运行前提；[记忆与区域复核](memory-review-v20.md)说明债务和实际引用。
