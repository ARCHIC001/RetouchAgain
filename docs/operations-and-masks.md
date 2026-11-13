# 阶段操作权限、参数语义与蒙版机制

为了让多轮调色收敛稳定，RetouchAgain 在每个阶段只向 Agent 开放当前阶段职责对应的控件子集，并对局部蒙版的创建与更新实施原子级控制。

## 1. 阶段权限投影

在每一轮请求中，worker 会根据当前阶段动态裁剪可用控件和动作 Schema：

| 阶段 | 开放的全局控件 | 开放的局部蒙版字段 |
|---|---|---|
| **Tone** | Light 六项（Exposure / Contrast / Highlights / Shadows / Whites / Blacks）；Master 点曲线；参数曲线（四区与三个分界点） | 前 6 项局部影调字段 |
| **Color** | 全局白平衡；Vibrance / Saturation；八色 HSL（hue / sat / lum）；三影调轮分级（shadows / midtones / highlights 及 blending / balance）；三原色 Calibration（Hue / Sat） | 局部 Temp / Tint |
| **Joint** | 上述全部已开放的影调与色彩控件；新增 Texture、Clarity、Dehaze | 全部 8 个局部字段 |
| **G1–G4** | 诊断、重评、阶段切换、选版与导出确认 | 通过对应编辑阶段执行 |

所有参数均为**绝对赋值**，具体取值范围由后端 `list_controls` 根据当前图像源动态提供。

## 2. 参数单位与原生语义

- **曝光（Exposure）**：全局 `light.exposure` 与局部 `exposure` 均以 **EV** 为单位。
- **相对滑杆**：Contrast、Highlights、Shadows 等 `-100…100` 控件为后端原生标度，各控件间的数值幅度不可直接跨项类比。
- **全局白平衡（WB）**：需携带源相关的 `source / mode / scale`，并在修改数值时同步切换为有效的 `custom` 状态（避免停留在 `asShot` 导致数值无效）。
- **局部 Temp / Tint**：范围为 `-100…100`，表示相对冷暖与绿—品红偏移，而非 Kelvin 色温。
- **色相与曲线**：色彩分级中的 `hue` 为 `0…360°` 环形角度，而 HSL 的 `hue` 滑杆为相对偏移量。Master 点曲线控制点 `x, y ∈ [0, 1]`，对 RGB 三通道应用同一映射；参数曲线的三个分界点需保持严格递增。

## 3. Linear 与 Radial 蒙版几何

局部调整统一使用 **Linear（线性渐变）** 与 **Radial（径向渐变）** 两类简单蒙版，覆盖局部受光、天空压暗、主体提亮等常见需求。所有几何参数基于 upright 完整画布归一化（`x` 向右，`y` 向下）：
- **Linear**：指定起点 `start` 与终点 `end`，从 `start` 向 `end` 线性衰减。
- **Radial**：指定中心 `center`、长短半轴 `rx / ry`（相对图像**长边**的比例）、旋转角 `angle`（`y` 向下坐标系中正值顺时针）以及羽化 `feather`（`0…100`）。

蒙版几何由 Agent 显式规划并传入，跨阶段修改已有蒙版的几何形状或可见性会同步影响其承载的影调和白平衡效果，从而触发关联复核。

## 4. 八个可调局部字段

| 字段 | 范围 | 说明 | 开放阶段 |
|---|---|---|---|
| `exposure` | `-4…4` EV | 局部整体曝光 | Tone、Joint |
| `contrast` | `-100…100` | 局部对比度 | Tone、Joint |
| `highlights` / `shadows` | `-100…100` | 局部亮部 / 暗部细节 | Tone、Joint |
| `whites` / `blacks` | `-100…100` | 局部亮端 / 黑端锚点 | Tone、Joint |
| `temp` / `tint` | `-100…100` | 局部相对色温 / 色调 | Color、Joint |

同一物理蒙版可跨阶段持续复用：在 Color 阶段对 Tone 建好的蒙版追加 `temp/tint` 时，状态机只 patch 新增字段，保留此前已生效的影调设置。局部 `amount` 默认固定为 `100`。

## 5. 新建蒙版的原子事务与 all-20 中性初始化

许多原生修图软件在新建蒙版时会继承上一次的滑杆记忆。为彻底消除残留参数干扰，系统在创建新蒙版时执行一套完整的原子事务：

```text
恢复并读回明确基版
  → 创建指定形状与几何的蒙版
  → 从 activeMask 与实际读回中绑定原生真实 ID
  → 校验新蒙版可见性、非反转状态与几何参数
  → 执行 all-20 中性初始化并读回验证（除 amount=100 外其余 19 项显式置 0）
  → 写入本轮选定的局部字段子集并读回验证
  → 触发全尺寸渲染并生成子版本
```

`all-20` 初始化覆盖：`exposure`, `temp`, `tint`, `contrast`, `highlights`, `shadows`, `whites`, `blacks`, `texture`, `clarity`, `dehaze`, `hue`, `saturation`, `sharpness`, `noise`, `moire`, `defringe`, `color_hue`, `color_sat`, `amount`。该归零步骤仅在**新建蒙版**时执行，调整已有蒙版时仅更新目标字段。

## 6. 持久 Trace 与结果状态

事务的每个内部步骤都会在持久 trace 中先记录为 `pending`，待原生返回与参数读回（含 `activeMask` 选中状态及画布几何一致性）校验通过后再结清。

在最终验收时，系统将流程完成（`complete`）、JPEG 导出确认、目标部分达成（`partial`）作为独立维度记录，支持在存在客观物理极限时以 `partial` 状态基于实测 JPEG 完成交付。

## 7. 对应源码模块

- `src/photo_stage_operations.py`：阶段控件校验、单位检查与蒙版原子事务。
- `src/photo_capability_catalog.py`、`src/photo_native_commands.py`：原生命令映射与几何参数契约。
- `src/photo_stage_schema.py`、`src/dsh_stage_worker.py`：动态合同生成与版本/渲染编排。
