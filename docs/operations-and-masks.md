# 阶段操作与简单蒙版合同

> 发布派生版/非历史原稿。本文从设计与实际校验代码抽取操作语义，不包含真实照片参数或操作成绩。

## 1. 权限按当前阶段投影

每次请求只暴露当前阶段合同；阶段由 worker 确定，不能靠 action 中自报 phase 扩大。
`values`、`settings`、`commands` 与 `mask_plan` 都按同一范围验证，宽 settings 不是旁路。
数值是绝对赋值；保留当前状态不等于重新提交整个隐藏配方。
实际 `list_controls` 的源相关范围和当前可用命令优先于静态通用目录。

| 阶段 | 可写全局家族 | 可写局部字段 |
|---|---|---|
| Tone | Light 六项；Master 点曲线；参数曲线四区与三个分界 | 六项影调 |
| Color | 全局 WB；Vibrance/Saturation；八色 HSL；三影调轮分级及共享权重；三原色 Hue/Sat | Temp/Tint |
| Joint | 上述已开放影调/颜色；Texture、Clarity、Dehaze 的明确残差任务 | 同八字段 |
| G1/G2/G3/G4 | 总体诊断、重评、转阶段或交付复核；不是跨阶段编辑许可 | 通过合法分支执行 |

Tone 的六项为 Exposure、Contrast、Highlights、Shadows、Whites、Blacks。
Color 的八色为 red/orange/yellow/green/aqua/blue/purple/magenta，各有 hue/sat/lum。
分级仅 shadows/midtones/highlights 的 hue/sat/lum，另有 blending/balance。
Calibration 仅 red/green/blue 的 Hue/Sat 六项。
Profile、Point Color、RGB 单通道曲线、分级 global 轮、Calibration ShadowsTint 不在当前范围。
全局暗角、颗粒、裁切、旋转、透视、光学修正也不因原生存在而自动开放。

## 2. 单位与容易混淆的参数

全局 `light.exposure` 为 EV；局部 exposure 也是 EV，但作用阶段和算法不等同。
其他多数 −100…100 控件是原生标度，不是像素百分比，也不能跨控件比较“强度”。
全局 WB 需带实际 source/mode/scale；合格 Kelvin 源与 relative 源不能混用。
修改 WB 数值时须形成有效 custom 状态，不能保留 asShot 又假定自定义温度已生效。
局部 temp/tint 各 −100…100，为相对冷暖/绿—品红校正，绝不是 Kelvin。

分级 hue 是 0…360° 环形变量；HSL hue 的滑杆值不是角度。
HSL 按色域而非对象选择，同色不同物体可能同时改变。
Master 曲线对 RGB 使用同一映射，不保证完全保色；不冒称物理亮度曲线。
曲线点 x/y 为 0…1 的整组坐标；当前操作仅 Master，reset 明确限定 master/parametric。
三个参数曲线分界在提交时必须严格有序，不只检查单项范围。

## 3. Linear 与 Radial 几何

两类蒙版贯穿三分支；形状是空间权重，不是精确语义分割。
Linear 常用于宽范围渐变，Radial 常用于局部受光/主体关系，但不由标签自动选位置。
坐标以 upright 完整画布归一化，x 向右、y 向下。

- Linear：DSH 明示 start/end；start 侧权重最大，向 end 衰减。
- Radial：明示 center、rx、ry、angle、feather。
- center 是归一化全图位置，rx/ry 是图像**长边**的比例，不是分别按宽高的比例。
- 半径为正；超过画面长边的半径可能有意为之，不任意加无依据上界。
- angle 正值在 y 向下画布中顺时针，feather 使用原生 0…100 标度。
- 当前仅正常、非反转的简单组件，不混 Add/Subtract/Intersect 或自动/AI 选区。

观察 ROI 不自动变成蒙版；几何由 DSH 显式给出，并检查羽化、邻域和区外保护。
跨阶段改变几何、可见性或删除蒙版，会同时影响已有影调和 WB，需关联复核。

## 4. 八个可调局部字段

| 字段 | 范围 | 当前语义 |
|---|---|---|
| exposure | −4…4 EV | 局部整体曝光 |
| contrast | −100…100 | 局部对比 |
| highlights / shadows | 各 −100…100 | 局部亮部/暗部层次 |
| whites / blacks | 各 −100…100 | 局部亮端/暗端支撑 |
| temp / tint | 各 −100…100 | 相对 WB 两轴 |

Tone 只改前六项，Color 只改后两项，Joint 在同八项内小组联合修正。
局部 saturation/hue、texture/clarity/dehaze、细节与颜色叠加不作为摄影旋钮开放。
全局 HSL 可用不表示局部 HSL 可用；原生同名字段也不等于同一实际响应。
同一物理蒙版可延续到下一阶段，只 patch 当前字段，不清零已接受旧值。
Amount 初始固定 100，不另作可反复调节的审美旋钮。

## 5. 真实 ID 与 all-20 中性初始化

新建蒙版可能继承工具记忆，因此不能把“默认通常为零”当作实际中性。
`mask_plan` 表达明确 create 或 adjust；不与另一套 values/settings/commands 混包。
事务内部依次执行：

```text
恢复并读回明确基版
→ 创建明示形状/几何
→ 从返回 activeMask 与实际读回绑定新 ID
→ 验证仅新蒙版改变、可见/正常组件与几何匹配
→ all-20 局部字段中性初始化并读回
→ 写 DSH 明确选择的本阶段八字段子集并读回
→ 由外层 worker 实际渲染并建立子版
```

20 字段为：exposure、temp、tint、contrast、highlights、shadows、whites、blacks、
texture、clarity、dehaze、hue、saturation、sharpness、noise、moire、defringe、color_hue、color_sat、amount。
初始化除 amount=100 外均为 0；含四个平时不开放的细节字段。
这是只用于新建的私有固定初始化，不是允许 DSH 任意写 20 字段。
已有蒙版只更新所选字段，不能为“安全”重新中性化并抹去已接受效果。
模板名/任务别名不是原生 ID；不猜 ID、不预写预计 ID、不复制多套蒙版满足阶段整齐。

## 6. 原子记录与失败处理

每个内部步骤在持久 trace 中先记 pending，再依据真实返回和读回结清。
selection 还需检查控制状态 activeMask；只看 DevelopSettings 不足以证明选中了蒙版。
读回验证 exact patch 与其余状态保留，并保护 crop/geometry/orientation/optics。
命令返回 pending/started、读回不符或执行结果不明时保留证据，不重放创建或调整。
只有明确尚未写入的可更正合同拒绝，才走结构化未执行反馈。

## 7. 后果、partial 与实际导出

每轮围绕一个主要假设，修改后看真实父/子，检查目标区、羽化邻域及全图牵连。
全局参数即使为局部问题选用，仍是全图影响，不改称局部修复。
DSH 可以保留、修改、撤回、选早期已观察版或承认剩余问题。
流程 complete、JPEG 已确认、目标 partial 与作者认可是不同维度。
允许 partial 交付不代表省略实际导出；确认必须基于当前实际 JPEG 的观察。
G4 返修会废除旧确认，重新导出并再次观察后才能确认新交付。

## 8. 源码与原职责相对来源

`src/photo_stage_operations.py`：精确阶段集、单位/路径验证、真实 ID 事务及中性初始化。
`src/photo_capability_catalog.py` / `src/photo_native_commands.py`：原生命令、几何和结果合同。
`src/photo_stage_schema.py` / `src/dsh_stage_worker.py`：动态公开合同与外层版本/渲染编排。
任务理由与后果身份见[工作记忆](memory-and-task-routing.md)，图像资格见[观察与色彩](observation-and-color.md)。

原职责相对来源（只归因，不链接排除原件）：
`01-当前设计/2026-11-12-当前实验简化蒙版操作规程.md` 提供两类/八字段/跨阶段保留设计；
`01-当前设计/2026-11-13-统一DSH分级Harness当前架构与状态.md` 提供当前开放范围和 all-20 初始化；
`02-综合调研/后端能力依据/2026-11-12-后端菜单控件参数语义核查与分级Harness依据.md` 提供单位和原生语义。
