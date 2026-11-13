# 手动导出、审核与私有仓库同步

> 发布派生版/非历史原稿。本文是新的发布操作说明，不是原运行手册或任何一次上传的执行记录。

## 1. 所有路径和身份由操作者填写

`<SOURCE_ROOT>` 是原研究工作区，`<PUBLISH_ROOT>` 是独立导出目录，两者不能相同。
`<OWNER>` 是操作者选择的 GitHub 用户/组织，仓库名为 `visharness`。
命令仅作手动操作模板，本次文档编制不执行认证、建仓、commit 或 push。
不设置定时任务、Git hook、CI 自动同步，也不在启动时自动导出或调用模型。

开始前阅读[发布范围](scope-and-environment.md)。
导出仅允许经清单批准的原创算法代码和方法文档，不整目录复制研究工作区。
原照片、实验结果、第三方全文/安装、环境配置、授权 policy 和凭据均排除。
导出不会修改或删除原稿；发布稿身份始终保留，不冒充历史原文。

## 2. 先完成独立导出

在可信 shell 中填写变量，示意采用 PowerShell。

```powershell
$SourceRoot = '<SOURCE_ROOT>'
$PublishRoot = '<PUBLISH_ROOT>'
$Repo = '<OWNER>/visharness'
```

导出工具只保留在原工作区；默认清单为 `tools/github-export-manifest.json`。
工具、本机 manifest 和发布 state 都不上传，GitHub 镜像不自带导出工具。
`<PYTHON>` 表示操作者明确选择的解释器，下例须从可信工作目录执行。

```powershell
# 默认只读 dry-run；先检查清单、排除类别和目标冲突。
& '<PYTHON>' -I -B -X utf8 "$SourceRoot/tools/github_export.py" --target $PublishRoot
# 人工确认后，加 --apply 才写入独立导出目录。
& '<PYTHON>' -I -B -X utf8 "$SourceRoot/tools/github_export.py" --target $PublishRoot --apply
```

导出后运行原工作区的发布检查（不调用模型或 Native）：

```powershell
& '<PYTHON>' -I -B -X utf8 "$SourceRoot/tools/check_github_publication.py" --tests
```

该检查读取本机清单中的发布目标，核对文件范围、语法、静态本地依赖和五个合成测试入口；不会声称完整运行环境齐备。
`check_github_publication.py` 同样只在原工作区，不随镜像上传。不要用依赖现场教材的完整预检替代发布边界检查。
更新已有发布目录时检查删除/覆盖计划，不在原工作区执行清理。
下载/解压的第三方材料各自放入新的空目录，工具脚本与其目录隔离。
本次不安装第三方依赖，不运行照片管线，也不恢复付费策略。

## 3. 检查本次允许清单和内容

核对导出清单、文件职责和大小，不运行或重算哈希。
检查 README 和 docs 链接均在发布仓库内，正文没有指向被排除实验资产的链接。
确认没有真实请求/会话标识、照片场景样例、费用账目、本机身份路径或配置内容。
确认源码是经审核快照，而非误带整个安装目录、环境文件或生成物。
新增文件是否属于发布范围需显式判定；不因后缀是 `.py`/`.md` 就自动准入。

可在导出目录只读查看 Git 状态/差异；首次还没有 Git 仓库则先做内容检查。
环境检查的缺依赖结论是预期范围信息，不通过复制实验/教材/配置来“补齐绿灯”。
不把 clone 后的 mtime 改回原值以伪装旧冻结资格。

## 4. 使用已有 GitHub CLI 登录，不读取凭据

以下认证步骤只由操作者手动执行，不在导出工具中自动调用。

```powershell
gh auth status
# 如尚未登录，由操作者选择浏览器交互；不要把 token 放进命令或聊天。
gh auth login --web --git-protocol https
```

不运行 `gh auth token`，不读取 credential 文件，也不打印 token 或环境变量值。
私有仓库认证只解决访问权限，不是照片/第三方材料授权或付费模型授权。
若用户/组织不同，由操作者重新选择 `<OWNER>`，不能从本机用户名推断。

## 5. 首次初始化和建私有仓库

仅在独立发布目录完成，先检查该目录职责及全部文件。
下列步骤展示完整手动路径，不表示本次已经执行。

```powershell
Set-Location $PublishRoot
git init -b main
# 首次也在独立分支审核；不要在默认分支直接做发布提交。
git switch -c publish/initial
# 只选审核后的明确路径，不用 git add . 或 git add -A。
git add -- <APPROVED_PATH_1> <APPROVED_PATH_2>
git diff --cached --stat
git diff --cached
# 人工内容审核后才手动提交。
git commit -m 'Publish reviewed source and method snapshot'
# 此命令只建 private 仓库和 remote，不带 --push。
gh repo create $Repo --private --source . --remote origin
gh repo view $Repo --json nameWithOwner,visibility,url
git remote -v
```

`<APPROVED_PATH_*>` 应展开为本次导出清单中的明确路径，而非上级工作区。
仓库若已存在，跳过 create，核对 owner、remote 和 `visibility=PRIVATE`。
任何已有远端历史都先检查，不 force、不重写他人提交。

## 6. 显式决定是否 push

只有操作者已核对暂存差异和 private 状态后，才自行执行以下示例。

```powershell
# 全新空仓库：首次审核提交完成后，明确将同一提交建立为 main。
git branch main HEAD
git switch main
git push -u origin main
```

上述 `git branch main HEAD` 仅适用于首次空仓库、main 尚无提交的情形；已有 main 时不执行或覆盖。
后续更新应先在发布分支审核，再明确快进合并到 main 后手动推送。
已经同步过的仓库，先 fetch/检查远端，再从默认分支建立新的发布分支。
后续仍是“预览 → 导出 → 内容/环境检查 → 明确暂存 → 手动提交 → 手动 push”。
不配置自动 push、自动创建 PR、后台监控或自动发布。

## 7. 上传不触发运行

Git 操作不调用 `dsh_stage_run.mjs`、RAW 制备或模型 API。
不把环境检查失败当恢复收费、复制 policy 或解除停止标记的理由。
发现范围外文件时停止本次上传并修正导出清单，不删除原知识库材料。
若已上传范围外内容，先由操作者决定远端处置；不擅自强推改历史。
仓库未新增 License，不在同步时补写“全部内容自有”或默认开源许可。

## 8. 原职责相对来源

本页操作顺序来自本次“独立导出、人工审核、私有仓库手动发布”的范围要求。
原知识库 `04-管理与使用/项目接手索引.md` 提供资产与归档隔离职责，
`04-管理与使用/运行与验证.md` 提供环境检查不等于真实运行/收费授权的区别。
这些仅为文字归因，不链接未发布原稿、实验结果或配置。
发布机制与源码边界见[架构](architecture.md)和[资源协议](protocol-and-resources.md)。
