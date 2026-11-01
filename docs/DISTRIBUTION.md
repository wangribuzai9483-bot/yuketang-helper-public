# GitHub 与 Windows 发布

公开版采用 MIT，源码与 Windows 测试包发布在 [yuketang-helper-public](https://github.com/wangribuzai9483-bot/yuketang-helper-public)。原开发仓库继续保持私有，公开版使用经清理的源码快照和独立提交历史，不复制旧提交中的邮箱或实际课程链接。

## 本机直接运行

发布包解压后运行 `雨课堂学习助手.exe`，不要单独移动 exe 或删除 `_internal`。GUI 使用 PyInstaller 的 windowed 构建，不需要命令行窗口；开发用 `启动雨课堂助手.cmd` 和 `start.ps1` 仍保留。

打包版用户数据保存在 `%LOCALAPPDATA%\YuketangHelper`，不随程序文件更新被替换，也不进入 ZIP。源码版继续使用原目录配置和 `.runtime`。当前不自动导入源码版登录状态、密钥或记录。发布版优先使用系统已有的 Microsoft Edge/Chrome，没有捆绑完整浏览器。Chromium 选项需要另外准备对应 Playwright 浏览器，不保证新电脑直接可用。

## 重新构建

在项目终端运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe tools/build_windows.py
```

每次建立新的 `dist/<时间戳>/` 与 `build/<时间戳>/`，不覆盖原发布包。构建脚本只收集应用代码、JS、OCR PowerShell 和界面素材。生成应用目录、ZIP、SHA-256 校验文件；最新路径在 `dist/latest-build.json`。正常开发启动不需要重新安装打包工具。

## 源码与 GitHub 发布

源码与本机运行数据分开管理。公开库只接收按清单导出并审查的文件；不要直接镜像包含旧提交或本机数据的开发目录。公开版提交使用 GitHub noreply 邮箱。

GitHub 连接器授权与本机 Git 凭据是独立的。终端 `git push` 需要在本机登录 GitHub，不要把 Token 写入脚本、远程地址或仓库。

`.gitignore` 排除个人配置、`.runtime`、虚拟环境、网页源码、截图、临时工作、备份、日志、数据库、快捷方式、构建和发布目录。上传时保留 `.gitignore`，只纳入应用代码、测试、构建脚本、示例配置和文档。`tools/export_source.py` 可以生成按清单导出的候选源码 ZIP；它不初始化 Git，也不上传文件。导出前检查扫描报告，不能把排除规则当作已经完成的人工审核。

可执行 ZIP 放在 GitHub Releases 作为发布附件；源码仓库用于代码和版本历史，不提交 `.venv`、完整运行环境或个人发布测试数据。重新构建时同时包含项目 `LICENSE`、`THIRD_PARTY_NOTICES.md` 和依赖许可文件。

## 维护与验证边界

- 原创代码采用 MIT；保留所分发依赖自身的许可文件与声明，素材来源见第三方声明。
- 审查源码、文档和提交历史中是否残留个人页面内容或真实密钥；如果历史中曾提交凭据，需要单独处理历史和凭据，不能只新增忽略规则。
- 公开版本仍需在没有 Python 的另一台 Windows 电脑验收启动、系统 Edge、登录、图片资源和本机 OCR。本机冻结程序初始化、资源与驱动检查不能代替真实课程回归。
- 当前构建没有代码签名或安装器；如面向更多用户分发，再考虑签名、版本号、安装器及更新流程。

参考：[PyInstaller windowed 构建](https://pyinstaller.org/en/stable/usage.html)、[Playwright 打包说明](https://playwright.dev/python/docs/library#pyinstaller)、[GitHub 敏感数据处理](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)。
