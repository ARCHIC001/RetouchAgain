# 仓库导出、检查与同步指南

本文档面向仓库维护者，说明如何从本地研究工作区筛选并导出源码与文档、执行发布前静态检查，并手动推送到 GitHub。

## 1. 设置路径与仓库变量

在本地终端中设定研究工作区 `<SOURCE_ROOT>`、独立的导出目录 `<PUBLISH_ROOT>`（两者需为不同路径）以及目标仓库 `<OWNER>/RetouchAgain`。以 PowerShell 为例：

```powershell
$SourceRoot = '<SOURCE_ROOT>'
$PublishRoot = '<PUBLISH_ROOT>'$Repo = '<OWNER>/RetouchAgain'
```

## 2. 执行导出与静态检查

导出脚本 `tools/github_export.py` 与清单 `tools/github-export-manifest.json` 保留在源工作区本地，不随发布仓库上传。

```powershell
# 1. 先用 dry-run 预览即将导出的文件清单与冲突情况
& '<PYTHON>' -I -B -X utf8 "$SourceRoot/tools/github_export.py" --target $PublishRoot

# 2. 确认无误后，加上 --apply 写入导出目录
& '<PYTHON>' -I -B -X utf8 "$SourceRoot/tools/github_export.py" --target $PublishRoot --apply

# 3. 运行静态发布检查（校验文件范围、语法、本地依赖及 5 个合成测试）
& '<PYTHON>' -I -B -X utf8 "$SourceRoot/tools/check_github_publication.py" --tests
```

导出器按清单精确放行源码、文档以及 `DSC_3734` 三文件示例（按二进制存储），不会修改源工作区内容。

## 3. 提交前快速复核

在导出目录提交前，建议检查：
- `README.md` 与 `docs/` 内的相对链接均指向仓库内有效文件。
- 未误带实验日志、本地环境配置、API 密钥或未公开的照片素材。

## 4. 初始化并推送到 GitHub

```powershell
# 检查或完成 GitHub CLI 登录
gh auth status
gh auth login --web --git-protocol https
```

首次建仓并初始化分支：

```powershell
Set-Location $PublishRoot
git init -b main
git switch -c publish/initial

# 明确添加审核后的路径，检查差异后提交
git add -- <APPROVED_PATH_1> <APPROVED_PATH_2>
git diff --cached --stat
git diff --cached
git commit -m 'Publish reviewed source and method snapshot'

# 创建仓库并关联远端（按需使用 --private 或 --public）
gh repo create $Repo --private --source . --remote origin
gh repo view $Repo --json nameWithOwner,visibility,url
git remote -v
```

确认无误后合并至 `main` 并推送：

```powershell
# 仅用于全新空仓库的首次推送；已有远端历史时请先 fetch 并使用快进合并
git branch main HEAD
git switch main
git push -u origin main
```

后续迭代遵循相同流程：**dry-run 预览 → 导出写入 → 静态测试 → 分支审核提交 → 合并推送**。更多设计细节见 [系统架构](architecture.md) 与 [通信协议与资源](protocol-and-resources.md)。


---

## v20追加：保留远端优化稿的手动同步

> 发布派生版/非历史原稿。此前初始化示例保留；已有仓库按本节执行增量维护，不重新初始化。

先核实仓库当前名称、可见性、默认分支和最新提交；从该提交建立更新分支并保全远端原始文字。已有README／说明文件只尾部追加，或新增独立说明后在旧文末尾加链接；不得以旧本地模板覆盖用户优化稿，不统一其换行／编码。提交前须对最终Git blob验证旧全文为完整字节前缀。

默认dry-run不写发布目标，但仍写本机audit；它不是完全无写操作。只有精确远端baseline可支持已审核重基线，未知语义差异不能当行尾冲突处理。新合成入口以 [publication-scope.json](../publication-scope.json) 为准，见 [v20说明](v20-update-and-usage.md)。

推送前再次确认远端未前移；前移就重新核对和保全，不强推。仓库公开／私有状态须有明确授权，不照搬首次创建示例改变现有可见性。更新仍完全手动，没有自动同步或定时推送。
