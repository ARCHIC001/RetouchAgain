# v20 版本承接、运行前提与追加式维护

> 发布派生版/非历史原稿。本页为通用设计和源码说明，不包含实验输出、收费许可或原机恢复资格。

## 1. 如何阅读本次更新

当前源码构建 `dsh-stage-runtime-v20-20261114`，资源 `stage-resource/v20`，外层协议 `dsh-stage-runtime/v2`，动态阶段合同 `stage-contract/v3`。运行构建号、摄影版本号和冻结图像底座版本是不同概念，不能互相替代。

既有 README／docs 是保留的说明底稿，本次不修改其原有文字；截至 v20 的增补以本页与以下三份为准：

- [WB：视觉基准、实际读回、局部复用](white-balance-v20.md)
- [Tone：归因、neutral-first、一次窄前置WB和边界交接](tone-guidance-v20.md)
- [记忆：typed依赖、活跃链、逐focus复核与稳定引用](memory-review-v20.md)

例如旧文给出的 Tone 顺序由新增 neutral-first 说明承接；原字符串软依赖仍兼容，但 typed hard／soft 的证据要求见新说明。历史解释保留不代表系统强制模型执行每个模块。

## 2. 可发布源码不等于可直接收费运行

仓库包括当前算法和共享依赖，以及独立合成测试；不含外部 DSH 安装、原生后端、Adobe DNG Converter、第三方安装、现场配置、冻结教材或实验资格原件。克隆无法继承源文件 mtime、输入资格、已安装依赖和授权，不应创建假资格记录绕过检查。

Python 环境需按实际功能安装 Pillow／NumPy；特殊源格式可能另需 tifffile／imagecodecs。公开检查使用合成夹具，不连接真实模型或 Native。可移植入口使用 `VISHARNESS_PYTHON`，必要时配置 `VISHARNESS_DNG_CONVERTER`。`VISHARNESS_FULL_AUTHOR_ROOT` 仅用于明确要求的原尺寸目标路径，默认作者仍是已授权示例中的普通短边512 JPG，经实际ICC转换；o前缀图不是作者目标。

唯一正式阶段运行入口为 [`dsh_stage_run.mjs`](../src/dsh_stage_run.mjs)，它本身不授予收费许可。真实运行仍需独立新scope、实际输入资格、正确图像角色及明确受监督授权；默认保持付费禁用和停止保护。不要把合成通过、文档更新或仓库下载当成恢复收费的许可。

## 3. 已知拒绝、未知结果和闭合续接

执行前已知拒绝必须有严格 no-effect 证明：未开始 Native、无 pending、版本／账本／摄影状态未变。工程错误不能仅凭名字推断为没执行。Native restore／apply／render／finish前持久 execution fence；未知执行或计量保留 pending／lease／circuit并先对账，不能盲重放。

[`photo_stage_close_proof.py`](../src/photo_stage_close_proof.py) 从实际 request、receipt、workflow提交、Native账本、attempt、监督窗口及关闭记录重建闭合证明，不修改这些原件。只有精确 provider action 已结清、用量已知、Native claims completed、无活动标记时，才可作为新scope的续接依据。

[`photo_stage_continuation.py`](../src/photo_stage_continuation.py) 在新scope验证实际 selected settings、文件和完整来源链，继承 bank／债务／版本／once记录；不重做已成功动作，不重新算基线render。旧state的working标签不是可以随意改成paused的理由；有强闭合证明时按实际记录判断。磁盘源码更新不冒热加载，变更构建应先闭合旧scope再独立冻结新scope。

## 4. 离线测试与资格界限

完整可运行测试入口以 [`publication-scope.json`](../publication-scope.json) 的 `synthetic_test_entrypoints` 为准。旧 README 中的五个入口继续保留，新增入口覆盖WB、Tone、working context、anchors以及发布用的阶段／close-proof合成检查；发布派生阶段测试明确排除依赖现场答卷的用例，不能把合成数量说成完整原机验收。

示例：

```bash
python -I -B -X utf8 src/check_photo_white_balance.py
python -I -B -X utf8 src/check_photo_tone_guidance.py
python -I -B -X utf8 src/check_photo_working_context.py
python -I -B -X utf8 src/check_photo_review_anchors.py
```

测试不提供摄影质量保证，不证明模型能准确判断目标残差；后果支持、工作流 complete、实际JPEG核验、goal partial／complete和作者认可必须分开记录。G4看到的是实际JPEG按观察契约派生的图，不因导出母版是全分辨率便声称模型收到全分辨率图。

## 5. 资源与维护说明

生成分配为65,536；32–64K／128K上下文只是指导，50总观察及分支份额也是规划，不是固定动作清单或硬次数。模型、传输、合法控件、未知结果和明显费用异常保护仍有效。实际成本应跨修复scope累计，不因换目录清零。

发布只手动进行。维护者的本机导出工具不随仓库上传；默认dry-run不写发布目标，但仍写本机审计。已有远端说明以固定提交字节为底稿，只在末尾追加或新增文件；更新分支提交前检查最终Git blob保留原文前缀，远端前移则重新核对，不强推。公开／私有状态和仓库名称应以当前仓库事实和用户授权为准，不能照搬旧初始化示例改变可见性。

源码去身份属于发布派生差异，不回写冻结底座原件。所有实验结果、请求／回执、费用明细、轨迹、原机资格、凭据及第三方安装保持排除；照片例外仅为已有 `DSC_3734` 三文件，不隐含更多素材的发布许可。
