# 工作记忆、建议 bank 与任务路由

> 发布派生版/非历史原稿。本文重新描述方法和源码合同，不是原始会话、答卷或实验结果的转录。

## 1. 三类信息与一个权威会话

工具知识回答“该控件是什么、单位和范围是什么、证据到哪一级”。
建议 bank 回答“当前照片有什么问题、可能怎样调整、何时值得采用”。
执行账本回答“实际采用了什么、从哪版产生哪版、真实后果怎样被观察”。
三者关联但不互相冒充；模型不能自行把工具知识或实验资格升级。
它们复用同一 worker 会话，不增加第二套可写摄影状态机。

## 2. 原始提交、canonical 与 warnings

`photo_stage_submission.normalize_submission` 返回规范化提交与警告，不改原输入。
运行记录分别保存 raw、canonical 和 warnings，使输入事实与执行包可对照。
规范化只处理已定义的公开结构：移动字段到合同位置、记录重复或冲突元数据等。
它不推断 Native 参数、不补摄影数值、不把字符串偷偷转成数值，也不裁剪 action。
缺少实质 review/action 字段仍缺少，由普通 schema 反馈处理。

例如选择理由可从 action arguments 移到顶层；冲突原值保留在警告的元数据中。
带实际改动的 keep 可规范为 revise，并留下原状态和路径。
effect 的同区 ROI 表达可按确定规则归一；不会把不同区域强行认作对应区域。
规范化应幂等；canonical 再处理不会产生另一套摄影指令。
公开结构错误的反馈可让 DSH 下一轮更正，不需要宿主代写合法参数。

## 3. 问题与细组保存什么

issue 保存初始诊断、原始整包建议、分类、优先级及全部公开修订。
细组保存所属 issue、分支/module/step、原提案路径、当前参数、依赖与选择理由。
两者都带依据版本、实际观察及修订关系；细组派生不覆盖原始答卷。
稳定组身份来自问题和明确分组，不因某次数值改变就换掉身份。
未知、歧义或未开放字段保留为 unresolved，不猜映射或塞入 Joint 执行。

每组还保存目标变化、保护对象、风险，以及操作/复核关联。
参数是本轮明确绝对值，不是“上次再加一点”的自动累积脚本。
上游曝光、WB 或几何变化后，旧 HSL、分级和局部建议可能需要新版本重评。
保存旧数值的价值是解释与纠错，不是排队自动重放。

## 4. bank 状态不是目标成绩

| 维度 | 例子 | 意义 |
|---|---|---|
| issue 生命周期 | active、deferred、retired | 当前是否继续考虑该问题 |
| 细组调度 | ready、current、later_phase、waiting_dependency | 当前工作位置和等待原因 |
| 证据有效性 | needs_reassessment、off_selected_branch、unresolved | 是否需要重评、是否属于选中支路 |
| 操作后状态 | awaiting_effect | 已操作，尚待真实后果复核 |
| 任务处置 | resolved、retired | DSH 对任务的显式处置 |
| 实际操作 | operation/base/child/actual_params | 真正发生的后端事实 |
| 后果判断 | supported、contradicted、mixed、uncertain 等 | 模型对实际后果的公开判断 |
| 目标结论 | matched、partial、unresolved 等合同值 | 当前目标接近程度，独立于调度状态 |

resolved 不是作者认可；supported 也不是目标达成。
一次操作先记 unreviewed，再将真实图像和 expectation 送入后果复核。
后果复核可以否定预期；不能因为调用成功就填写 improved。
公开模型判断带其身份，不覆盖设置读回和实际操作记录。

## 5. proposal disposition 与 effect judgment

keep/revise/defer/retire/resolved 等是对建议的处置，可以在尚无实际 operation 时发生。
此类记录附带的 operation 引用作为 metadata_reference 留存，未采用引用有 warning。
它们不修改实际 operation outcome，也不因冗余引用就否定本轮其他合法动作。
显式 issue/group 引用错误仍须更正，不能凭相似名字自动挂靠。

真正 effect judgment 必须关联实际 expectation、operation、父版和本次观察到的子版。
子版、父版、组或预期错绑属于可定位的合同错误，不当作“只是文字多写了”。
只对当前实际分支和版本形成有效复核，不能用另一候选的后果替代。
五字段预期为 target、expected_change、protected_region、risk、falsifier。
本次看到了哪种变化、是否符合预期、剩余目标差距分别叙述。

## 6. 有序路线、软依赖与 reopen

默认模块顺序来自 `photo_stage_tasks` 注册表；同层再按 priority 组织。
排序决定推荐和投递，不强制执行最高项，也不等于原生渲染顺序。
同 issue 短依赖仅在唯一解析时归一；歧义、缺项和循环保留明确等待理由。
等待不会伪造上游完成，也不阻断无关组。

依赖是摄影任务的顺序提示，不是另一个永久执行锁。
当前真实观察已重评该组、明确选择 group 且说明理由时，可记录依赖 override 后继续。
该 override 不越过阶段权限、原生 ID/单位范围、未知执行或当前版本检查。
同一组可连续多轮，也可 skip、回访或重新排序。
已显式 resolved 的组不因后续任意编辑自动 reopen。
若需重开，应基于新观察明确修订建议或改变任务状态，保留重开依据。

## 7. 一轮内完成复核与下一动作

正常顺序是：真实两图 → 复核上一后果 → keep/revise 当前建议 → 选择下一小组 → 校验并执行。
这些可以在同一个 `photo_stage_turn` 中提交，不另加普遍确认轮。
实际 action 必须属于明确所选组；跨步骤联合组需共同假设与理由。
宿主不从未采用提案中摘参数，不把混包静默拆成多个编辑。

合法建议摄入与动作执行分开记。
如果有明确“未进入原生持久写入”的拒绝证据，可保留合法 bank 更新并反馈动作错误。
该拒绝不生成 child、不声称 effect 已发生，也不接受该动作的 workflow/debt 结清。
已经穿过持久边界、回执不明或 pending 时保留现状并停止核对，不能套用未执行续行。

## 8. G2 增量重评与跨阶段债务

G2 用实际彩色影调选中版逐项 keep/revise/retire/new/uncertain，未覆盖项保持待重评。
提前在 G1 写过颜色计划，不等于已经完成 G2；保留判断也须有新版本依据。
Tone 的颜色副作用可在 G2 正常观察复核，Color 的影调保护可在 G3 复核。
共享蒙版几何变化关联多阶段效果，不因当前只改 WB 就免去影调检查。
债务记录的是“还需哪种观察”，不是额外收费审批队列。

选择旧版后，非所选祖先链债务保留为 `not_applicable_selected_branch` 并关联选版证据。
祖先链上的相关债务仍需公开复核或明确接受取舍，不把离开分支写成已经复核。

## 9. 工作包与全文检索

当前工作包优先投递当前组、上一真实操作的完整预期、待复核项和少量下一候选。
它同时提供延期/等待任务的 ID、状态和等待原因，全文可按 history/detail 游标取回。
不是固定前三条正文挤掉当前任务，也不是每轮重发全部历史。
实际投递范围与仅本地归档的原文分别登记；缓存图像和已存引用不是新观察。

## 10. 源码与原职责相对来源

源码职责：`src/photo_stage_submission.py` 处理规范化；`src/photo_proposal_bank.py` 保存提案、操作、处置和工作包。
`src/photo_stage_tasks.py` 提供纯注册/解析；`src/photo_phase_scheduler.py` 管理阶段与债务。
`src/dsh_stage_worker.py` 统一提交；整体关系见[架构](architecture.md)。

原知识库相对来源（仅作归因，不提供被排除原稿的仓库链接）：

- `01-当前设计/2026-11-13-分支有序模块细组与工作记忆机制.md`：细组、历史、工作包与同轮推进。
- `01-当前设计/2026-11-13-统一DSH分级Harness当前架构与状态.md`：canonical、软依赖和增量重评修订。
- `02-综合调研/后端能力依据/2026-11-12-后端菜单控件参数语义核查与分级Harness依据.md`：知识/bank/执行分层。
- `02-综合调研/VisualHarness实施研究.md`：版本、作者决定与最终画面事实的区别。
