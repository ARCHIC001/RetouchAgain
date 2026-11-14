# 记忆拓扑、区域复核与实际引用（截至 v20）

> 发布派生版/非历史原稿。本文只追加机制；诊断、处置、实际执行和摄影收益保持分离。

## 1. 一份建议库，不让派生细组变成第二执行队列

issue 保存完整原提案和修订历史，task_groups 是其派生细组。必须显式选择 issue／group 后才能执行，宿主不从历史绝对值自动拼操作。组级 skip 记为 skipped，不冒已执行或已改善；显式 revise 可重开。retire 也不等于恢复该组曾写入的设置。

依赖区分：旧字符串依赖作为 soft disposition；typed 对象可指定 soft／hard、disposition／effect 和实际 operation。hard disposition 只认当前适用分支的 resolved，不把 skip／retire算已满足；effect 需要当前祖先链的真实操作及实际后果判断。已复核不等于有改善。

soft skip／retire 的 waiver 绑定确切处置记录，可沿选中祖先链继承，不在每个 child 重复生成，未复核实际 operation 优先。未知、歧义或循环引用不能靠 override 假满足。

## 2. 活跃蒙版与当前真实链

全阶段 `active_masks_summary` 区分 selected／observed，保留隐藏、零值或复杂蒙版身份。相同 kind 的中心距严格小于 0.1 只给非阻断几何提醒，不自动合并、复用、拒绝或新增收费确认。Linear 中点不是 coverage center；Radial 半径按长边比例解释，不把大于 1 自动裁掉。

`selected_baseline` 提供 root→selected 真实链、实际改变的控件和 inactive 引用。成功选版发生非直接 child 变化时记录 `BRANCH RESET NOTICE`；只观察旧版不是选版。顶层 `context.work_packet` 是唯一正文，嵌套投影使用明确引用；旧操作参数和弃支正文可折为可检索索引，原 state 不删除，未复核预期仍完整保留。

## 3. 锚点与逐 focus 债务

`review.review_anchors` 分别记录 target／protected、explicit_cells／full_frame／unknown、view_role、region 和 qualification。宿主只绑定真实 observation、版本、阶段、模式、ROI 和 grid，不从历史 WB 格号、蒙版几何或散文猜测。

`photo-review-debt/v2` 对同一实际 operation 保持一条债务和稳定 focus ID。每项检查包含 focus_id、outcome、evidence、review_region，可带本次实际 review_cells；相同格号不代表跨 ROI 配准。

- `no_regression` → resolved，表示已复核未见回退。
- `minor_acceptable` → accepted_tradeoff，表示明确接受代价。
- `spawn_joint_task` → reviewed_joint_task，`review_complete=true` 且 `photographic_fix_claimed=false`。

spawn 原子、幂等地创建 `joint-from:<debt_id>` 的 Joint 诊断，`groups:[]`，只保存证据和保护关系；不生成 controls、参数、几何。后续模型显式补方案、选组才可执行。pending 离开选中祖先链标为不适用，选回只恢复原 pending，不清真正已复核状态。

## 4. v18–v20：解除冗余格式阻塞，不降低证据要求

### 可选 anchor 不合格

原提交及 warning 保留，不能绑定的 role／cell 不作为实际证据；缺失定位回落为明确 unknown focus，不猜格号或冒充 full-frame。严格绑定函数本身仍拒错误来源。

### 债务批次与合法动作分流

不合格或时机不适用的附带 debt batch 记为 `not_adopted_debts_unchanged`，原债务不变，其他明确合法 action 继续正常校验。被采纳批次的债务更新与 Joint spawn 内部仍是原子事务，失败不留半个诊断任务；不是自动结清或放松 G2／G3／G4 彩色 target、到期与祖先链要求。

### 稳定 group ID

完整 group ID 不重复加 issue 前缀；修订遗漏旧组不自动 retire。已 retired 对象的重复 keep／retire仅留审计，不复活或改变原基线。历史畸形但已存在的 ID 保留，显式精确选择仍按实际身份处理，不猜缩写。

### 实际 operation 与 expectation 引用

当前观察版若存在唯一真实 operation→expectation 对应，可确定性规范化其别名并留原值及 warning。当前后果已实际复核时，冗余 effect 声明只归档，不覆盖旧 judgment、不阻下一合法动作；没有真实绑定的新后果仍必须提供证据，未知 ID 不能猜配。

## 5. 代码入口

[`photo_proposal_bank.py`](../src/photo_proposal_bank.py)、[`photo_stage_tasks.py`](../src/photo_stage_tasks.py) 管理建议、依赖与稳定组；[`photo_working_context.py`](../src/photo_working_context.py) 管理当前链／蒙版上下文；[`photo_review_anchors.py`](../src/photo_review_anchors.py) 和 [`photo_phase_scheduler.py`](../src/photo_phase_scheduler.py) 绑定区域及复核；[`dsh_stage_worker.py`](../src/dsh_stage_worker.py) 接合单回合事务。

相关合成入口为 [`check_photo_working_context.py`](../src/check_photo_working_context.py)、[`check_photo_review_anchors.py`](../src/check_photo_review_anchors.py) 和发布清单列出的阶段测试。合同测试不是摄影收益证明。另见[WB](white-balance-v20.md)、[Tone](tone-guidance-v20.md)、[v20使用与安全边界](v20-update-and-usage.md)。
