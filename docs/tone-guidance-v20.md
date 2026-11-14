# Tone：归因隔离、neutral-first 与边界交接（截至 v20）

> 发布派生版/非历史原稿。以下为追加的当前机制，不重写既有设计叙述，不包含实验记录。

## 1. G1 先分类，不限制后续建议

G1 彩色总观察可以提出任何后续阶段的判断、建议和参数，Harness 负责按 issue／branch／module／fine group 保存和组织；提前谈颜色不构成整轮拒绝理由。建议存储和实际执行是两个权限面，存下 Color 建议不会让 G1 获得通用编辑权限。

`review.tone_assessment` 记录作者区域、当前区域、材质、受光、证据、保护目标和 handoff。`attribution` 仅为 `structure`、`chroma` 或 `uncertain`，可以随后据实图修订。颜色性明暗可交给 HSL-L／颜色工作，不以全局曝光替代所有色彩差异。黑场、白场和中灰锚点须带资格限制，不自动当作纯黑、纯白或标准色卡；允许没有可靠锚点。

Tone 统一执行 sRGB 解码、线性 `Y=.2126R+.7152G+.0722B`、sRGB 编码并复制到 RGB 三通道。它是可重复的亮度派生，不是绝对感知亮度无损；没有 auto-level 或独立直方图匹配，也不修改 RAW 设置和彩色母版。RGB 目标无法证明作者使用过某一个具体工具。

## 2. neutral-first 是软路线

截至 v15 后的 Tone 推荐顺序为：

`whites_blacks → exposure → highlights_shadows → contrast → master_curve → parametric_curve → local_tone → closure`

它优先建立端点支撑和中灰骨架，不强迫调遍菜单，不强推 0／1 端点。细组可以多轮小步修改，也可根据实际瓶颈显式改优先级、说明跳步理由。既有文档中的旧示例顺序应结合本追加说明理解；18 或总体规划次数不是摄影动作硬 cap。

Tone 的 Master 曲线允许 `[]` reset，或 2–5 个点，x 严格递增、y 不下降；不自动补点、裁点、固定端点或生成复杂波浪。`settings.curve.master` 与 `develop.curve` 两入口语义一致，Joint 的既有范围不因 Tone 限制被偷偷改变。

## 3. 可选的一次前置粗 WB

若严重偏色确实妨碍影调基础判断，G1 可选择窄 action `pre_tone_wb`，不是新增 workflow phase：

- 显式 base_version，只提交动态合同允许的 `wb.temp`／`wb.tint` scalars，至少一项；不开放 G1 通用 settings、commands、picker 或 mask。
- 需要明确 WB 任务、tone_basis_impact、编辑预期与显式细组。
- 一次资格按实际成功消耗；已知未执行不消耗，inflight／unknown 不重放。reload、选父版或检查点续接不能清掉记录。
- 成功后先看实际彩色 effect，再用 fresh G1 完整 target 重建影调判断，才进入 Tone；G2 正常保留。

不用前置 WB 是默认合法路径，不为测试该字段强迫额外请求。

## 4. 局部边界和跨阶段债务

Tone 局部除目标区外，还记录保护边界、羽化邻域和区外区域。实际父／子复核用 `review.tone_boundary_review` 评估 `protected`、`affected` 或 `uncertain`；细边界残差可交同 issue 的 Joint 组，不自动生成另一张蒙版或新几何。

Tone 的颜色／边界后果进入原同条 G2 债务；实际 Color HSL-L 的影调保护进入原同条 G3 债务，并与 WB 关注并存。任务路由不是效果已经改善的证明，pending 也不等于必须先消债才能进行下一合法动作。

## 5. 公开提交兼容不改变摄影参数

v18 起，指定叙述字段中的 string list 可作可逆 JSON 文本规范化；允许的附带叙述、排序提示及重复模块声明保留原文和 warning，而不是付费整理格式。Light 家族别名仅处理组织声明，实际 typed action 决定执行覆盖。

原输入、canonical 和 warning 分开留存。未知 Native 字段不静默剥离，必需信息不从散文猜补，空调用仍拒绝。归因记录完整、接口执行成功，都不能替代实际作者对照与独立摄影判断。

## 6. 代码入口

- [`photo_tone_guidance.py`](../src/photo_tone_guidance.py)：归因记录、保护边界和实际 HSL-L 差异。
- [`photo_pre_tone.py`](../src/photo_pre_tone.py)：一次资格、后果复核、退出和续接检查。
- [`photo_stage_submission.py`](../src/photo_stage_submission.py)：公开结构规范化，不能猜改 Native 参数。
- [`check_photo_tone_guidance.py`](../src/check_photo_tone_guidance.py)：离线合成检查。

另见[白平衡](white-balance-v20.md)、[记忆与区域复核](memory-review-v20.md)及[使用前提](v20-update-and-usage.md)。
