# 发布到 GitHub 的清单

> **状态：准备完毕，尚未发布。** 没有 `git init`、没有远端、没有推送。
> 这份文件是发布前的核对表，逐条对应「已做 / 待你确认」。

---

## 1. 命名：全英文（已做）

| 位置 | 之前 | 现在 |
|---|---|---|
| 仓库名（建议） | `DSH控制台` | **`dsh-console`** |
| 发布包 | `DSH及控制台-Linux.zip` | **`DSH-Console-Linux.zip`** |
| 包内目录 | `DSH及控制台-Linux/` | **`DSH-Console-Linux/`** |
| 图形启动器 | `启动DSH控制台.sh/.bat` | **`Start-DSH-Console.sh/.bat`** |
| 终端启动器 | `启动DSH终端界面.sh/.bat` | **`Start-DSH-Terminal.sh/.bat`** |
| 网页启动器 | `启动DSH网页界面.sh/.bat` | **`Start-DSH-Web-UI.sh/.bat`** |
| 无黑框启动器 | `启动DSH控制台.vbs` | **`Start-DSH-Console-Silent.vbs`**（Windows 改为优先用 `.exe`，见下） |
| **Windows 原生启动器（新增）** | 无 | **`Start-DSH-Console.exe` / `Start-DSH-Web-UI.exe` / `Start-DSH-Terminal.exe` / `Start-DSH-Console-Debug.exe`** |
| 装桌面图标 | `安装桌面快捷方式.sh` / `创建桌面快捷方式.bat` | **`Install-Desktop-Shortcut.sh` / `Create-Desktop-Shortcut.bat`** |
| 桌面条目模板 | `DSH控制台.desktop` | **`tools/dsh-console.desktop.in`**（仓库里是模板；`install-desktop.sh` 生成项目内可双击的 `dsh-console.desktop`，后者已 gitignore） |
| 包内说明 | `使用说明.md` | **`README-Portable.md`**（顶部加了英文快速上手） |
| 版本清单 | `版本信息.json` | **`version.json`** |
| 可执行位清单 | `可执行文件清单.txt` | **`exec-manifest.txt`** |
| Windows 补装 | `补装Windows运行时.bat/.ps1` | **`Install-Windows-Runtime.bat/.ps1`** |
| 捐赠数据目录 | `捐赠通道/微信收款码.png` … | **`donate/wechat-qr.png` · `alipay-qr.jpg` · `donors.json`** |

英文名里 **DSH 就是 DeepSeek Harness 的简称**，所以对外一律写 `DSH Console` / `dsh-console`。
中文只保留在**文档正文、代码注释和应用界面**里（这些是给人读的，不是标识符）。

## 2. 仓库名与 About（搜索权重最高的两处）

**仓库名：`dsh-console`**

**About 用中文**（GitHub 的 About 只能填一种语言，按你的要求用中文）。它是**单行**输入框，
换行会被吃掉，所以直接整段复制下面这一行（实测 **153 字符**，上限 350）：

```
绿色免安装的 DeepSeek Harness（DSH）桌面控制台：Python + PySide6 原生 GUI，解压即用、不污染系统。一键启停 harness、查看账单与 token 消耗、管理插件与技能、用内置 Chromium 浏览器打开界面、支持在线升级。Linux / Windows 便携包。
```

> 为什么这样写：
> * **中文正文 + 英文专有名词**（`DeepSeek Harness` / `DSH` / `Python` / `PySide6` / `Chromium` /
>   `harness` / `token`）——GitHub 搜索对 CJK 一样能命中，而这些英文词是别人真正会搜的；
> * 开头一句就把「绿色免安装 / 解压即用 / 不污染系统 / 这是什么」说完，列表里被截断也看得懂；
> * **不写"开源""免费"**——那由 License 说明，占字数不值当。

英文版留作备查（将来若要面向英文用户，换成这段即可）：

```
A portable, zero-install PySide6 desktop console for DSH (DeepSeek Harness): start/stop the service, track token billing, browse plugin & skill markets, upgrade the harness. Unzip and run on Linux & Windows — nothing is written to your system.
```

> **Topics 保持英文**（见下节）：About 用中文之后，英文关键词的搜索覆盖就靠 Topics 和
> README 里的英文小节来兜，两者都不要改成中文。

## 3. Topics（最多 20 个，直接决定搜不搜得到）

```
deepseek  deepseek-harness  dsh  harness  pyside6  qt6  python  portable
green-software  no-install  desktop-app  gui  billing  token-usage
cost-tracking  plugin-manager  skills  ai-tools  linux  windows
```

## 4. 已完成项

- [x] **`LICENSE`**：MIT，`Copyright (c) 2025 xugulin`
- [x] **`.gitignore`**：排除 `dist/`、`.build-cache/`、`__pycache__/`、`.venv/`、`.disk-probe`、`home/`、`run/`、`app.bak-*/`、`.github-preview.html`、`/dsh-console.desktop`（生成的启动器条目）
- [x] **README 分工**：`README.md` = 发布介绍，**默认显示中文**（截图 → `🇨🇳 简体中文` → `🇬🇧 English`），顶部 `**简体中文** · [English](#-english)` 点击切换、英文节顶部有 `⬆ 回到中文`；`docs/DEVELOPMENT.md` = 原 1700 行开发日志（图片相对路径已改为 `../shots/`）
- [x] **截图打码**：控制台页的账户余额已打码（马赛克），**其余真实数据保留**：今日花费、调用数、tokens、缓存命中率、24h 图都在；`midnight-dashboard.png` / `daylight-dashboard.png` 同样处理
- [x] **写死的本机路径**：见下节，全部修完
- [x] **两个平台的可移植包**：`dist/DSH-Console-Linux.zip`（308.8 MB）、`dist/DSH-Console-Windows.zip`（353.6 MB）
- [x] **独立运行实测**：见第 7 节——解压到干净目录、不装任何东西直接跑通，且真实家目录零改动
- [x] **版面预览**：`.github-preview.html`（用真 GFM 渲染 + GitHub 排版 CSS + 仓库头/Topics 的仿真），浏览器里已可查看

## 5. 写死的本机路径：全部修完

| 位置 | 之前 | 现在 |
|---|---|---|
| `main.py` | `DEFAULT_VENV` 写死 `/home/<用户名>/…/<venv>`（开发机上那个 uv 建的 venv） | 改为**可选**：只认 `$DSH_CONSOLE_VENV`，默认走 `<项目>/.venv` → PATH 上的 `python3.14`；报错信息也不再指向别人的路径 |
| `tools/find-python.sh` | 同上 | 同上，并新增 `DSH_CONSOLE_VENV` 用法提示 |
| `tools/build_bundle.py` | `SYS_SITE` 写死某个 venv 的 `site-packages` | 改为 `sysconfig.get_paths()["purelib"]`（用哪个解释器构建就取哪个环境），另外 6 个系统路径都加了 `DSH_BUILD_*` 环境变量出口 |
| `dsh-console.desktop` | `Exec`/`Icon` 写死绝对路径 | 改成模板 `tools/dsh-console.desktop.in`（`@HERE@` 占位符）；`install-desktop.sh` 用 `sed` 替换后写出**两份**：项目内可双击的 `dsh-console.desktop`（755）与 `~/.local/share/applications/` 下的启动器条目。**踩过的坑**：只留占位符不生成，用户双击项目里那个文件就完全没反应；文件不可执行时文件管理器还会拿编辑器打开它 |
| `dsh_console/gui.py`、`gui_native.py` | 报错信息里写死 venv 路径 | 改用 `sys.executable` / `DSH_CONSOLE_PYTHON` |
| `tui.py`、`run.sh`、`make_icon.py`、`screenshot.py`、`billing.py`、`main.py` 的文档与示例 | 提到本机 venv 路径 | 换成通用写法或 `~/…` 示例 |
| `docs/DEVELOPMENT.md`、`PUBLISHING.md` | 文档里还留着本机 venv 路径（5 + 4 处），等于教读者去用一条只在本机存在的路径 | 全部改成 `.venv` / `/path/to/dsh-console` |
| **打进 zip 的 profile**（`cordis.patch.yml` 的 `browserPath`、pnpm 的 `.modules.yaml`、`.pnpm-workspace-state-v1.json`） | 原样带上构建者的家目录 → **每个下载者都能看到 `/home/<用户名>`** | 新增 `build_bundle._scrub_home_paths()`：打包时把构建机家目录换成中性值 `/home/user`（只改这几个文本文件，不删文件、不改行为） |

> ⚠️ 最后两栏是**复查时**才发现的：**代码干净不等于产物干净**——文档和包里的 profile 都会
> 记下绝对路径。复验方式：在包里搜本机家目录（`grep -rlI "/home/<你的用户名>" dist/`）→ **0 命中**。
>（含 `runtime/`、`harness/`、`app/`、`home/` 全部子树）。

> 本机怎么还能跑？项目根建了个指向本机 uv 环境的软链 `.venv`（`ls -l .venv` 能看到实际指向；已在 `.gitignore` 里），
> 于是 `./run.sh`、桌面启动器都照旧工作，而**源码里一条绝对路径都没有**。别人 clone 下来
> 按 README 建自己的 `.venv` 即可。实测：清掉环境变量后 `find-python.sh` 挑出 `.venv/bin/python`。

## 6. 首次推送（供你复制，我没有执行）

```bash
cd /path/to/dsh-console
git init -b main
git add .
git commit -m "feat: DSH Console v1.1 — portable PySide6 console for DSH (DeepSeek Harness)"
git remote add origin git@github.com:xugulin/dsh-console.git
git push -u origin main
```

提交前先确认体积（不该是 GB）：

```bash
du -sh --exclude=dist --exclude=.build-cache .
```

## 7. 独立运行实测（发布前的最后确认）

把两个 zip 解压到**干净目录**（本次用的是 `~/dsh-standalone-test/`），不装任何东西直接跑。全部通过：

| 检查 | 结果 |
|---|---|
| `unzip` 后 `runtime/linux/python/bin/python3.14`、`node`、各 `.sh` 的可执行位 | **在**（决定"解压即用"成不成立；`zipfile` 解压不还原权限，启动器也会自己补，见下） |
| 包内自带的 Python / PySide6 / Node | **3.14.7 / 6.11.2 / v26.8.2**，全部来自包内，不借系统的 |
| `./Start-DSH-Console.sh --self-test` | **退出码 0**，末尾打印「7 套主题全部渲染 OK」 |
| `./Start-DSH-Console.sh`（GUI 真的建窗口） | 12 秒后掐掉，**退出码 124 = 一直在跑**，没崩 |
| 包内 `home/` | 被写入 **33 个文件**（`.dsh/settings.yaml`、`.npm/…`）→ `HOME` 确实被关进包里 |
| **`~/.dsh`（3135 项）** | 跑完前后指纹**完全一致**，一个字节没动 |
| **`~/.npm`（44342 项）** | 同上，**零改动** |
| `~/.config/dsh-console`、`~/.cache/dsh-console` | 同上，**零改动** |
| 用**最恶劣的方式**解压（Python `zipfile`，连启动器自己都没有可执行位） | `sh Start-DSH-Console.sh --self-test` **照样跑通**，且启动器把权限补了回来 |
| Linux 包内容 | `Start-DSH-Console/Terminal/Web-UI.sh` + `Install-Desktop-Shortcut.sh` + `dsh-console.desktop` + `icons/` + `app/`（含 `donate/`、`docs/`、`LICENSE`） |
| Windows 包内容 | `Start-DSH-Console.bat` + **`Start-DSH-Console-Silent.vbs`**（无黑框）+ `Start-DSH-Terminal/Web-UI.bat` + `Create-Desktop-Shortcut.bat` + `Install-Windows-Runtime.bat` + `icons/icon.ico` + 自带 `python.exe` / `pythonw.exe` / `node.exe`，且**没有混入任何 .sh** |

### 7.1 启动器实测：**Windows 的 .exe 用 wine 真跑过**

Windows 版新增了原生 `.exe` 启动器（源码 `tools/win_launcher/dsh_launcher.c`，由
`tools/build_win_launcher.py` 用 clang + lld-link 交叉编译成 PE，**不链 CRT**）。
本机有 wine 11.17，所以这一项不是"静态核对"，是**真的跑起来了**：

| 启动器 | 怎么验的 | 结果 |
|---|---|---|
| `Start-DSH-Console.exe --self-test` | wine，图形子系统（无控制台） | **退出码 0** |
| `Start-DSH-Console-Debug.exe --list-themes` | wine，控制台子系统 → 能看到输出 | 打印 **7 套主题**，退出码 0 |
| `Start-DSH-Terminal.exe --help` | wine | 打印的是 **TUI 自己的帮助**（`python -m dsh_console.tui`）→ 说明按文件名注入 `--tui` **生效** |
| `Start-DSH-Web-UI.exe --self-test` | wine | 退出码 0，参数正常转交 |
| `Start-DSH-Console-Silent.vbs` | wine wscript | 退出码 0（老方式仍然可用） |
| `Start-DSH-Console.bat --list-themes` | wine cmd | 退出码 0（排查用） |
| 故意把包内 `python.exe` 藏起来 | wine | 图形版返回 **1** 并走 `fail()` 分支 → 错误路径也是通的（真机上会弹原生对话框） |

> 编译期踩到并修掉的两个坑（都写进源码注释了）：① 大栈帧会让编译器引用 `__chkstk`，
> 而无 CRT 没这个符号 → 缓冲区改 `static`；② 一开始从"目录路径"里取自己的文件名，
> 拿到的是**目录名**，于是 `--tui` 永远不注入 → 改成在截断路径**之前**先取文件名。

### 7.2 启动器实测：Linux 三个 `.sh` 都验过

| 启动器 | 结果 |
|---|---|
| `./Start-DSH-Console.sh --list-themes` | 打印 7 套主题，退出码 **0** |
| `./Start-DSH-Terminal.sh --help` | 打印 **TUI 自己的帮助** → `--tui` 注入生效，退出码 0 |
| `./Start-DSH-Web-UI.sh`（用 `DSH_CONSOLE_WEB_PORT=8936` 避开系统那台） | 日志「地址已就绪」，**8 秒后 HTTP 401**（服务活着）；收尾只杀便携包进程，系统 harness（3080）**未受影响** |

解压好的 Linux 包还留在 `~/dsh-standalone-test/Linux/DSH-Console-Linux/`
（约 830 MB），想亲手验一遍就直接 `cd` 过去跑 `./Start-DSH-Console.sh`；不用了就整个删掉。

> 本轮已经用 wine 把 Windows 的 `.exe` / `.bat` / `.vbs` 都真跑了一遍（见 7.1），
> 包里还自带 `pythonw.exe`，PATH 也由启动器改好。剩下的差异只有"wine 不是 Windows"：
> wine 没有显示驱动，所以"双击弹出窗口"这一步在真机上看才准。
> **发布前建议你在真机上双击一次 `Start-DSH-Console.exe`**，确认窗口正常出现即可。

## 8. Release 资产

| 平台 | 文件 | 大小 |
|---|---|---|
| Linux | `dist/DSH-Console-Linux.zip` | 308.8 MB |
| Windows | `dist/DSH-Console-Windows.zip` | 353.6 MB |

发布时打 **`v1.0`** 标签，资产命名为 `DSH-Console-1.1-Linux.zip`、`DSH-Console-1.1-Windows.zip`。

**"检查更新"已经默认可用**：便携包里的「检查控制台更新」在用户没配任何地址时，
会去读仓库根目录的 `version.json`（`updater.DEFAULT_VERSION_URL`），拿到新版本号后
按 `DSH-Console-<版本>-<平台>.zip` 拼出 Release 资产地址去下载。
所以**每次发版必须做两件事**：
1. 改 `dsh_console/__init__.py` 的 `__version__`，并同步仓库根的 `version.json`（版本/标签/资产）；
2. 打完包后建 `vX.Y.Z` 的 Release，把两个 zip 按 `DSH-Console-X.Y.Z-{Linux,Windows}.zip` 传上去。

想改用别的发布地址（比如自建镜像），在 `config.json` 里配 `consoleUpdateUrl` 即可——
它优先于内置默认值：填 `.zip` 地址就下它，填 `.json` 地址就只用来检查版本。

```json
{ "consoleUpdateUrl": "https://github.com/xugulin/dsh-console/releases/latest/download/DSH-Console-1.0-Linux.zip" }
```

## 9. 待你决定的两件小事

1. **原始的 `Screenshots/` 目录**（6 张你手拍的图）：README 用的是 `docs/screenshots/` 里
   裁好、打好码的 5 张，所以这个目录进不进仓库都行。想干净就删掉，或在 `.gitignore` 里加一行。
2. **应用界面目前是中文**。仓库名、包名、启动器已全英文，但界面文字没翻译——
   要面向英文用户的话，这是下一步该做的事（工作量不小，可以后续再说）。

---

## 附：可放心引用的硬数据

| 项目 | 数值 | 出处 |
|---|---|---|
| 控制台版本 | **1.1** | `dsh_console/__init__.py` |
| Python 要求 | ≥ 3.14（`compression.zstd` 进标准库） | `requirements.txt` |
| 依赖 | 仅 `PySide6-Essentials >= 6.11.2` | `requirements.txt` |
| 便携包大小 | Linux 308 MB / Windows 见 `dist/` | 实测 |
| 冷启动 | 约 95 ms、零后台任务 | `docs/DEVELOPMENT.md` 响应速度实测 |
| 市场列表渲染 | 69,600 ms → 25 ms | 同上 |
| 主题数 | 7 套（4 暗 3 亮） | `dsh_console/themes.py` |
| 不污染系统 | 实测 `~/.dsh`、`~/.npm` 零改动 | `docs/DEVELOPMENT.md` 踩坑 46 |
