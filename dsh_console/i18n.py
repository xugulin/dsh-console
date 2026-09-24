"""界面语言：内置**简体中文 / 英语（美国）**两种，首次启动默认中文。

## 语言影响三件不同的事

1. **Qt 自己的文案**（右键菜单、文件对话框、标准按钮……）——靠 Qt 的 `.qm` 翻译文件，
   用 :func:`install_translators` 装上 ``qtbase_<lang>.qm`` 与 ``qtwebengine_<lang>.qm``。
   **内置浏览器的右键菜单英文就是这一条缺失导致的**：项目此前从没装过翻译器。
2. **Chromium（QtWebEngine）的界面语言**——靠启动参数 ``--lang=zh-CN``，必须在
   **QApplication / web 引擎初始化之前**通过 ``QTWEBENGINE_CHROMIUM_FLAGS`` 设好
   （见 :func:`prepare_environment`）。它同时决定 ``navigator.language``，
   于是 harness 的 web UI 也会跟着变中文。
3. **本项目自己的界面文字**——靠 :func:`tr` 查表（``_EN`` 里是中文 → 英文的对照）。

## 为什么要自己查表而不是用 Qt 的 tr()

项目里的界面文案是直接写在代码里的中文字面量（293 处），没有 ``self.tr(...)`` 包裹。
用它自己的表可以让**已经写好的中文原文直接当 key**，逐页替换时不必一次改完：
没查到的串原样返回中文，不会出现空白或 ``%1`` 之类的占位符。

## 加一门语言 / 补一批翻译

* 新语言：往 ``LANGUAGES`` 加一项、建一张表，`tr` 里按当前语言选表即可。
* 补翻译：往 ``_EN`` 里加「中文原文: English」；键必须与代码里的字面量**一字不差**。
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt

from typing import TYPE_CHECKING

from . import config

if TYPE_CHECKING:                     # 只为类型注解，运行时不需要 Qt
    from PySide6.QtCore import QTranslator

#: 语言 key（存进 config.json 的 ``language``）
LANG_ZH = "zh_CN"
LANG_EN = "en_US"
#: 内置语言。第一项是默认值——**首次启动默认简体中文**。
LANGUAGES: tuple[tuple[str, str], ...] = (
    (LANG_ZH, "简体中文"),
    (LANG_EN, "English (US)"),
)
DEFAULT_LANG = LANG_ZH

#: Qt 翻译文件名里的语言段（qtbase_zh_CN.qm / qtbase_en.qm）
_QT_SUFFIX = {LANG_ZH: "zh_CN", LANG_EN: "en"}
#: Chromium 的 --lang 取值（BCP 47）
_CHROMIUM = {LANG_ZH: "zh-CN", LANG_EN: "en-US"}

#: 中文原文 → English。**键要和代码里的字符串一字不差。**
_EN: dict[str, str] = {
    "在系统浏览器中打开此页": "Open this page in system browser",
    "在系统浏览器中打开链接": "Open link in system browser",
    "翻译选中文字": "Translate selection",
    "（查不到）": "(unavailable)",
    "未查到 API key": "No API key found",
    "标签": "tag",
    "包含": "in",
    "关键字筛选（包含 / 说明 / 标签，本地匹配）…": "Filter by keyword (name / description / tag, matched locally)…",
    "合计": "Total",
    "小计": "Subtotal",
    "峰值": "Peak",
    "（详细拆分见「账单」页）": " (details on the Billing page)",
    "已安装 {v}；alpha 有 {n}。升级不会自动重启，见下方「运行状态」。": "Installed {v}; {n} available on alpha. Upgrading does not restart automatically — see Run status below.",
    "运行中的就是当前安装的版本（安装于 {a}，进程启动于 {b}）。": "Running the currently installed version (installed {a}, process started {b}).",
    "🌟 虚位以待 —— 还没有捐赠记录。": "🌟 Your name could be here — no donations yet.",
    "磁盘 {d} 个 · 网卡 {n} 个 · CPU 占用按 1 分钟负载折算": "{d} disks · {n} NICs · CPU usage from 1-minute load average",
    "已从官方 API 同步 {n} 个模型": "Synced {n} models from the official API",
    "已加载 {a} / {b}　源共 {c}　滑到底继续加载 ↓": "Loaded {a} / {b}　of {c} in source　scroll to the bottom to load more ↓",
    "共 {n} 个：{m} 个生效": "{m} of {n} active",
    "in progress": "in progress",
    "读取": "Loading",
    "启动系统 harness": "Start system harness",
    "启动内置 harness": "Start bundled harness",
    "已从官方 API 同步": "Synced from the official API:",
    "个模型": "models",
    "加速": "mirror",
    "CPU 占用按 1 分钟负载折算": "CPU usage from 1-min load",
    "网卡": "NICs",
    "UTC 周六/周日全天谷期": "UTC: all weekend is off-peak",
    "峰时段（本地时间）": "Peak hours (local time)",
    "计费，下一次切换：": "billing. Next switch:",
    "详细拆分见": "see the Billing page",
    "源共": "total in source",
    "滑到底继续加载": "scroll down to load more",
    "已加载": "Loaded",
    "补齐体检": "Check all",
    # ⚠️ 这张表由 "中文原文 → English" 组成，键必须与代码里的字面量**一字不差**。
    # 覆盖 17 个页面 + 浏览器 + 各服务模块的界面文案；新增文案时往这里补一行即可。
    # 未命中的串原样显示中文（见 tr / replace_all）——宁可中文，也不要空白。
    "下载": "Downloads",
    "个人": "Personal",
    "主频": "Clock",
    "亮色": "Light",
    "今天": "Today",
    "从未": "Never",
    "仓库": "Repository",
    "会话": "Session",
    "体积": "Size",
    "依赖": "Dependencies",
    "停止": "Stop",
    "停用": "Disable",
    "全价": "Full price",
    "全选": "Select all",
    "全部": "All",
    "关于": "About",
    "关闭": "Close",
    "内存": "Memory",
    "内核": "Kernel",
    "分类": "Category",
    "刷新": "Refresh",
    "前进": "Forward",
    "剪切": "Cut",
    "包名": "Package name",
    "区域": "Region",
    "升级": "Upgrade",
    "半价": "Half price",
    "卸载": "Uninstall",
    "发送": "Send",
    "取消": "Cancel",
    "可用": "Available",
    "名单": "List",
    "名字": "Name",
    "名称": "Name",
    "后退": "Back",
    "启动": "Start",
    "启用": "Enable",
    "命令": "Commands",
    "国际": "International",
    "复制": "Copy",
    "大小": "Size",
    "失败": "Failed",
    "安装": "Install",
    "导航": "Navigation",
    "就绪": "Ready",
    "已用": "Used",
    "引擎": "Engine",
    "当前": "Current",
    "形态": "Halves",
    "总量": "Total",
    "技能": "Skill",
    "排名": "Rank",
    "排序": "Sort",
    "接口": "Interface",
    "描述": "Description",
    "插件": "Plugin",
    "搜索": "Search",
    "放大": "Zoom in",
    "日志": "Logs",
    "日期": "Date",
    "星标": "Stars",
    "暖沙": "Warm Sand",
    "暗色": "Dark",
    "更新": "Update",
    "最新": "Latest",
    "未知": "Unknown",
    "本地": "Local",
    "本机": "Local",
    "条数": "Lines",
    "来源": "Source",
    "核心": "Cores",
    "档位": "Channel",
    "概览": "Overview",
    "模型": "Model",
    "樱雾": "Sakura Mist",
    "浏览": "Browse",
    "添加": "Add",
    "滚轮": "Wheel",
    "版本": "Version",
    "状态": "Status",
    "生态": "Ecosystem",
    "留言": "Message",
    "目录": "Directory",
    "直连": "Direct",
    "磁盘": "Disk",
    "类型": "Type",
    "粘贴": "Paste",
    "系统": "System",
    "级别": "Level",
    "缩小": "Zoom out",
    "规范": "Standards",
    "设置": "Settings",
    "评分": "Rating",
    "详情": "Details",
    "语言": "Language",
    "说明": "Description",
    "调用": "Calls",
    "账单": "Billing",
    "输出": "Output",
    "退出": "Quit",
    "重启": "Restart",
    "重装": "Reinstall",
    "金额": "Amount",
    "错误": "Error",
    "鼠标": "Mouse",
    "下载量": "Downloads",
    "不使用": "Not used",
    "不可用": "Unavailable",
    "不可达": "Unreachable",
    "主机名": "Hostname",
    "仅错误": "Errors only",
    "会话数": "Sessions",
    "使用量": "Usage",
    "停止中": "Stopping",
    "全不选": "Select none",
    "午夜蓝": "Midnight Blue",
    "单文件": "Single file",
    "占用率": "Usage",
    "可更新": "Update available",
    "可管理": "Manageable",
    "启动中": "Starting",
    "周下载": "Weekly downloads",
    "失败：": "Failed: ",
    "家目录": "Home directory",
    "峰时价": "Peak-hour price",
    "峰时段": "Peak hours",
    "工作区": "Workspace",
    "已停止": "Stopped",
    "已停用": "Disabled",
    "已启用": "Enabled",
    "已安装": "Installed",
    "已最新": "Up to date",
    "已缓存": "Cached",
    "总余额": "Total balance",
    "打印…": "Print…",
    "挂载点": "Mount point",
    "捐赠者": "Donors",
    "控制台": "Dashboard",
    "收款码": "Donation codes",
    "数据源": "Data source",
    "无 ✓": "No ✓",
    "无数据": "No data",
    "无标题": "Untitled",
    "星云紫": "Nebula Purple",
    "晨曦白": "Dawn White",
    "曜石黑": "Obsidian Black",
    "未安装": "Not installed",
    "未拉取": "Not fetched",
    "未探测": "Not probed",
    "未查询": "Not checked",
    "未缓存": "Not cached",
    "未装配": "Not wired",
    "未配置": "Not configured",
    "活跃度": "Activity",
    "深海青": "Deep Sea Teal",
    "目录源": "Catalog source",
    "若干包": "several packages",
    "许可证": "License",
    "谷时价": "Off-peak price",
    "谷时段": "Off-peak hours",
    "资源量": "Volume",
    "运行中": "Running",
    "都没有": "Neither",
    "（空）": "(empty)",
    "[就绪]": "[Ready]",
    "{d}天": "{d}d",
    "{m}分": "{m}m",
    "{s}秒": "{s}s",
    "上次运行": "Last run",
    "两者都有": "Both",
    "中国大陆": "Mainland China",
    "仅前端半": "Client half only",
    "仅宿主半": "Host half only",
    "今日消费": "Today's spend",
    "会话明细": "Session details",
    "体检全部": "Check all",
    "余额不足": "Insufficient balance",
    "充值余额": "Top-up balance",
    "全部插件": "All plugins",
    "内存占用": "Memory usage",
    "删到行尾": "Delete to the end of the line",
    "删除选中": "Remove selected",
    "刷新中…": "Refreshing…",
    "加载中…": "Loading…",
    "加载失败": "Load failed",
    "升级引擎": "Upgrade engine",
    "升级权限": "Upgrade permissions",
    "单元状态": "Unit status",
    "发布地址": "Release URL",
    "另存为…": "Save page…",
    "含前端半": "with client half",
    "含宿主半": "with host half",
    "启动前端": "Launch frontends",
    "启动失败": "Failed to start",
    "处理中…": "Working…",
    "复制图片": "Copy image",
    "复制路径": "Copy path",
    "外观主题": "Appearance",
    "安装位置": "Install location",
    "安装插件": "Install plugin",
    "安装详情": "Installation details",
    "安装路径": "Install path",
    "安装选中": "Install selected",
    "已是最新": "Up to date",
    "已采用：": "Applied: ",
    "市场目录": "Market catalog",
    "开机时间": "Boot time",
    "引擎版本": "Engine version",
    "当前时段": "Current period",
    "当前版本": "Current version",
    "恢复默认": "Restore defaults",
    "扫描失败": "Scan failed",
    "技能列表": "Skill list",
    "技能市场": "Skill Market",
    "技能总数": "Total skills",
    "拉取中…": "Fetching…",
    "捐赠支持": "Donate",
    "探测中…": "Probing…",
    "插件市场": "Plugin Market",
    "操作系统": "OS",
    "文件系统": "Filesystem",
    "新建会话": "New session",
    "新标签页": "New tab",
    "无前端半": "no client half",
    "无宿主半": "no host half",
    "更新失败": "Update failed",
    "更新选中": "Update selected",
    "最忙时段": "Busiest hour",
    "最新收录": "Recently added",
    "最新版本": "Latest version",
    "最近使用": "Last used",
    "最近发布": "Last published",
    "最近更新": "Recently updated",
    "有前端半": "Has client half",
    "有宿主半": "Has host half",
    "服务状态": "Service status",
    "未知原因": "unknown reason",
    "未知工具": "Unknown tool",
    "本地目录": "Local directory",
    "本机信息": "System Info",
    "本机区域": "Local region",
    "查看正文": "View content",
    "查询失败": "Lookup failed",
    "桌面环境": "Desktop environment",
    "检查元素": "Inspect",
    "检查失败": "Check failed",
    "检查更新": "Check for updates",
    "模型调用": "Model calls",
    "清空整行": "Clear the whole line",
    "源码仓库": "Source repository",
    "点击放大": "Click to enlarge",
    "点某个区": "Click an area",
    "点输入行": "Click the input line",
    "界面语言": "Interface language",
    "登录自启": "Start on login",
    "监听端口": "Listening port",
    "确认停止": "Confirm stop",
    "确认删除": "Confirm removal",
    "确认卸载": "Confirm uninstall",
    "确认安装": "Confirm install",
    "确认更新": "Confirm update",
    "确认重启": "Confirm restart",
    "筛选: ": "Filter: ",
    "累计发送": "Total sent",
    "累计接收": "Total received",
    "累计花费": "Total spend",
    "累计调用": "Total calls",
    "缓存命中": "Cache hit",
    "缓存大小": "Cache size",
    "网络接口": "Network interfaces",
    "美元 $": "USD $",
    "自动刷新": "Auto refresh",
    "警告以上": "Warning and above",
    "计价币种": "Currency",
    "读取中…": "Loading…",
    "账户余额": "Account balance",
    "赠送余额": "Granted balance",
    "输入: ": "Input: ",
    "运行时长": "Uptime",
    "运行状态": "Runtime status",
    "选中复制": "Select to copy",
    "重新加载": "Reload",
    "重置缩放": "Reset zoom",
    "需要重启": "Restart required",
    "验证超时": "Validation timed out",
    "（直连）": "(direct)",
    "（默认）": "(default)",
    "  ◂焦点": "  ◂Focus",
    "[处理中]": "[Working]",
    "{d} 天": "{d}d",
    "{h}小时": "{h}h",
    "{m} 分": "{m}m",
    "· 它  ": "· AI  ",
    "· 工具 ": "· Tool ",
    "· 我  ": "· Me  ",
    "↓ 一直按": "Hold ↓",
    "下一次进入": "Next switch at",
    "两份都升级": "Upgrade both",
    "人民币 ¥": "CNY ¥",
    "今日总消费": "Today's total",
    "保存并应用": "Save & apply",
    "先选中一行": "Select a row first",
    "全选可更新": "Select all updatable",
    "关闭标签页": "Close tab",
    "内置浏览器": "Built-in browser",
    "内置精选：": "Built-in picks: ",
    "切换会话…": "Switching session…",
    "切换工作区": "Switch workspace",
    "切换模型…": "Switching model…",
    "华为云镜像": "Huawei Cloud mirror",
    "发送与退出": "Send & quit",
    "另一个进程": "another process",
    "可执行文件": "Executable",
    "外观与主题": "Appearance & Themes",
    "安装前体检": "Pre-install checkup",
    "安装插件…": "Install plugin…",
    "安装期脚本": "Install-time script",
    "峰时段消费": "Peak-hour spend",
    "已全部加载": "All loaded",
    "已安装插件": "Installed plugins",
    "延迟/速度": "Latency/Speed",
    "开发者工具": "DevTools",
    "微信收款码": "WeChat Pay",
    "思考已隐藏": "Thinking hidden",
    "感谢者名单": "Thank-you list",
    "控制台版本": "Console version",
    "控制台缓存": "Console cache",
    "控制台自身": "Console itself",
    "控制台配置": "Console configuration",
    "插件与技能": "Plugins & Skills",
    "插件目录源": "Plugin catalog sources",
    "新建标签页": "New tab",
    "无余额信息": "No balance info",
    "更新控制台": "Update console",
    "未知处理器": "Unknown processor",
    "模型与价格": "Models & Pricing",
    "模型价目表": "Model price list",
    "正在切换…": "Switching…",
    "正在更新…": "Updating…",
    "正在检查…": "Checking…",
    "没等到地址": "No URL received",
    "浏览器版本": "Browser version",
    "滚动与焦点": "Scrolling & focus",
    "缓存命中率": "Cache hit rate",
    "缓存未命中": "Cache miss",
    "腾讯云镜像": "Tencent Cloud mirror",
    "自定义前缀": "Custom prefix",
    "花费（¥）": "Cost (¥)",
    "谷时段消费": "Off-peak spend",
    "输入行编辑": "Input line editing",
    "还没有记录": "No records yet",
    "进入峰时段": "Entering peak hours",
    "进入谷时段": "Entering off-peak hours",
    "（无描述）": "(no description)",
    "（无日志）": "(no logs)",
    "DSH 对话": "DSH Chat",
    "DSH 相关": "DSH details",
    "Qt 运行时": "Qt runtime",
    "[启动失败]": "[Failed to start]",
    "npm 官方": "npm (official)",
    "npm 镜像": "npm mirror",
    "run 目录": "run directory",
    "{d} 天前": "{d} days ago",
    "{h} 小时": "{h}h",
    "不按形态过滤": "Do not filter by halves",
    "会话花费汇总": "Session cost summary",
    "使用指定地址": "Using the specified URL",
    "使用现有地址": "Using the existing URL",
    "切换工作区…": "Switching workspace…",
    "回到最新内容": "Jump back to the latest content",
    "复制图片地址": "Copy image address",
    "复制访问地址": "Copy access URL",
    "复制链接地址": "Copy link address",
    "左右移动光标": "Move the cursor left / right",
    "已经启动过了": "Already started",
    "市场源设置…": "Market sources…",
    "异常重启次数": "Unexpected restarts",
    "打开所在目录": "Open containing directory",
    "按包名字母序": "Alphabetical by package name",
    "控制台自定义": "Custom (console)",
    "搜索接口可用": "Search API available",
    "支付宝收款码": "Alipay",
    "无 swap": "No swap",
    "更新到此版本": "Update to this version",
    "检查引擎更新": "Check for engine updates",
    "检查插件更新": "Check plugin updates",
    "模型调用次数": "Model calls",
    "焦点区翻一页": "Scroll the focused area one page",
    "装配行 id": "Wiring entry id",
    "跟随区域预设": "Follow region preset",
    "进程 PID": "Process PID",
    "采用各类最优": "Use best of each",
    "重启使其生效": "Restart to apply",
    "（正文为空）": "(no content)",
    "（默认分支）": "(default branch)",
    "，地址已就绪": ", and the URL is ready",
    "DSH 主目录": "DSH home",
    "DSH 控制台": "DSH Console",
    "Harness": "Harness",
    "{n} 个会话": "{n} sessions",
    "{sec} 秒": "{sec}s",
    "—（无法停用）": "— (cannot be disabled)",
    "↑回滚 N 行": "↑Scrolled back N lines",
    "　已是最新 ✓": " Up to date ✓",
    "今日花费（¥）": "Today's cost (¥)",
    "内置浏览器出错": "Built-in browser error",
    "内置浏览器详情": "Built-in browser details",
    "删光标前一个词": "Delete the word before the cursor",
    "删光标所在字符": "Delete the character at the cursor",
    "区域与网络线路": "Region & network route",
    "发现的来源根：": "Source roots found:",
    "启用 / 停用": "Enable / Disable",
    "已安装（重装）": "Installed (reinstall)",
    "找不到 npm": "npm not found",
    "按累计流量排序": "Sorted by total traffic",
    "控制台发布地址": "Console release URL",
    "显示 / 其它": "Display / Other",
    "未启用（直连）": "Not enabled (direct connection)",
    "查看网页源代码": "View page source",
    "检查控制台更新": "Check for console updates",
    "正在下载新包…": "Downloading the new package…",
    "正在恢复会话…": "Resuming session…",
    "正在替换代码…": "Replacing code…",
    "焦点切到那个区": "Switches focus to that area",
    "焦点：说明面板": "Focus: help panel",
    "状态：读取中…": "Status: loading…",
    "确认更新控制台": "Confirm console update",
    "自动发现与测速": "Auto-discovery & speed test",
    "自动发现并测速": "Auto-discover & speed test",
    "设置发布地址…": "Set release URL…",
    "详情 / 体检": "Details / Checkup",
    "距离下一次切换": "Next switch in",
    "（不使用加速）": "(no proxy)",
    "（没有可选项）": "(no options)",
    "（读不到型号）": "(model unavailable)",
    "DSH 终端界面": "DSH Terminal UI",
    "next（预览）": "next (preview)",
    "npm 通道：　": "npm channels: ",
    "两份都装了才能用": "Only available when both are installed",
    "会话 {sid}": "Session {sid}",
    "删光标前一个字符": "Delete the character before the cursor",
    "去技能市场装新的": "Install new from the Skill Market",
    "发布地址已保存。": "Release URL saved.",
    "启动 GUI 版": "Launch GUI",
    "启动 TUI 版": "Launch TUI",
    "失败：{msg}": "Failed: {msg}",
    "官方目录（站点）": "Official catalog (site)",
    "将执行：<br>": "Will run:<br>",
    "已清除发布地址。": "Release URL cleared.",
    "总 tokens": "Total tokens",
    "本机状态：未安装": "Local status: not installed",
    "正在读取工作区…": "Loading workspaces…",
    "没有可打开的地址": "No URL to open",
    "焦点区上下滚一行": "Scroll the focused area one line",
    "用内置浏览器打开": "Open in built-in browser",
    "用系统浏览器打开": "Open in system browser",
    "重启失败：{m}": "Restart failed: {m}",
    "<b>外观</b>": "<b>Appearance</b>",
    "DSH 内置浏览器": "DSH Built-in Browser",
    "GitHub 加速": "GitHub proxy",
    "alpha（内测）": "alpha (internal)",
    "bundle 数量": "Bundle count",
    "bundle 顺序": "Bundle order",
    "{label}中…": "{label} in progress…",
    "{verb} 失败": "{verb} failed",
    "今天还没有模型调用": "No model calls yet today",
    "今天还没有调用记录": "No calls yet today",
    "光标跳到点中的位置": "Moves the cursor to the clicked position",
    "关键字「{kw}」": "Keyword \"{kw}\"",
    "内置浏览器打开界面": "Open UI in built-in browser",
    "切换模型 / 配置": "Switch model / configuration",
    "升级内置浏览器引擎": "Upgrade built-in browser engine",
    "发布不足 30 天": "Published less than 30 days ago",
    "启动{label}": "Start {label}",
    "固定版本 / 分支": "Pinned version / branch",
    "复杂推理，能力优先": "Complex reasoning, capability first",
    "安装 {spec}": "Installing {spec}",
    "已经是要装的版本（": "Already at the version to install (",
    "打开 npm 页面": "Open npm page",
    "拉取 / 刷新目录": "Fetch / refresh catalog",
    "按花费从高到低排序": "Sorted by cost, highest first",
    "既有界面又注入宿主": "Has both a UI and host logic",
    "最近发布时间的倒序": "Most recent publish time first",
    "正在读取会话列表…": "Loading session list…",
    "正在读取包元信息…": "Reading package metadata…",
    "正在读取系统信息…": "Reading system info…",
    "滑到底继续加载 ↓": "Scroll to the bottom to load more ↓",
    "用这个路径新建会话": "Start a new session in this path",
    "直连（不使用加速）": "Direct (no proxy)",
    "确认安装 / 升级": "Confirm install / upgrade",
    "请填写要安装的包名": "Enter a package name to install",
    "负载 1/5/15": "Load 1/5/15",
    "账户余额（查不到）": "Account balance (unavailable)",
    "起不来：{exc}": "Failed to start: {exc}",
    "路径已复制到剪贴板": "Path copied to clipboard",
    "项目 · .dsh": "Project · .dsh",
    "默认模型，速度优先": "Default model, speed first",
    "），没有重复下载。": "), no re-download.",
    "GitHub 仓库源": "GitHub repository sources",
    "harness 内存": "Harness memory",
    "harness 单元": "Harness unit",
    "latest（稳定）": "latest (stable)",
    "profile 占用": "Profile size",
    "profile 目录": "Profile directory",
    "profile_存在": "profile exists",
    "体检失败：{msg}": "Checkup failed: {msg}",
    "保存失败：{exc}": "Save failed: {exc}",
    "内置 harness": "Bundled harness",
    "写入失败：{exc}": "Write failed: {exc}",
    "切换失败：{err}": "Switch failed: {err}",
    "升级失败：{msg}": "Upgrade failed: {msg}",
    "卸载失败：{msg}": "Uninstall failed: {msg}",
    "在新标签页中打开链接": "Open link in new tab",
    "安装失败：{msg}": "Install failed: {msg}",
    "已添加 {repo}": "Added {repo}",
    "找不到 dsh 命令": "dsh command not found",
    "拿不到可用的访问地址": "Could not get a usable URL",
    "指针在哪个区滚哪个区": "Scrolls the area the pointer is in",
    "探测失败：{msg}": "Probe failed: {msg}",
    "操作失败：{msg}": "Operation failed: {msg}",
    "更新 {label}": "Updating {label}",
    "更新失败：{msg}": "Update failed: {msg}",
    "服务端进程已经不在了": "The server process is gone",
    "检查失败：{msg}": "Check failed: {msg}",
    "正在打开内置浏览器…": "Opening the built-in browser…",
    "状态栏提示：在看历史": "Status bar hint: viewing history",
    "目录拉取失败：{m}": "Catalog fetch failed: {m}",
    "系统 harness": "System harness",
    "纯后台插件，没有界面": "Background-only plugin with no UI",
    "获取失败：{msg}": "Fetch failed: {msg}",
    "读取失败：{msg}": "Load failed: {msg}",
    "运行中（{src}）": "Running ({src})",
    "还没有可用的探测结果": "No probe results available yet",
    "重启 harness": "Restart harness",
    "重启失败：{msg}": "Restart failed: {msg}",
    "（没有匹配的配置项）": "(no matching configuration options)",
    "（没有取到版本信息）": "(no version information retrieved)",
    "（没有可恢复的会话）": "(no sessions to resume)",
    "（没有已安装的插件）": "(no plugins installed)",
    "，但暂未拿到访问地址": ", but no URL is available yet",
    "Chromium 引擎": "Chromium engine",
    "GitHub 加速前缀": "GitHub proxy prefix",
    "harness 已重启": "harness restarted",
    "npm view 失败": "npm view failed",
    "npm 上不存在这个包": "This package does not exist on npm",
    "patch 层托管停用": "Disabled via patch layer",
    "…（更早的内容已省略）": "…(earlier content omitted)",
    "全局目录可写，直接安装": "The global directory is writable; installing directly",
    "切换工作区（可敲路径）": "Switch workspace (you can type a path)",
    "切换模型 / 推理档位": "Switch model / reasoning effort",
    "包内 harness/": "harness/ inside the package",
    "卸载 {p.name}": "Uninstalling {p.name}",
    "官方目录（npm 包）": "Official catalog (npm package)",
    "已更新 {name}/": "Updated {name}/",
    "指针在哪个区就滚哪个区": "Scrolls whichever area the pointer is in",
    "显示 / 隐藏思考过程": "Show / hide the thinking process",
    "显示 / 隐藏说明面板": "Show / hide the help panel",
    "显示 / 隐藏这个面板": "Show / hide this panel",
    "正在启动 GUI 版…": "Starting the GUI…",
    "正在启动 web 版…": "Starting the web version…",
    "正在并发查询最新版本…": "Querying the latest versions in parallel…",
    "正在打开 TUI 版…": "Opening the TUI…",
    "用户 · ~/.dsh": "User · ~/.dsh",
    "留空 = 跟随区域预设": "Leave blank = follow region preset",
    "目录不存在：{cwd}": "Directory does not exist: {cwd}",
    "访问地址已复制到剪贴板": "Access URL copied to clipboard",
    "跑着→取消；空闲→退出": "Running → cancel; idle → quit",
    "（滚轮 / PgDn）": "(wheel / PgDn)",
    "Shift 拖动选中文本": "Shift-drag to select text",
    "{h} 小时 {m} 分": "{h}h {m}m",
    "{repo} 已在列表里": "{repo} is already in the list",
    "会话扫描失败：{msg}": "Session scan failed: {msg}",
    "切换会话（带标题和时间）": "Switch session (with title and time)",
    "加载更多失败：{msg}": "Failed to load more: {msg}",
    "包清单里没有「版本」字段": "The package manifest has no \"version\" field",
    "启动终端失败：{exc}": "Failed to launch the terminal: {exc}",
    "周下载量（默认排名依据）": "Weekly downloads (default ranking basis)",
    "命令面板（下面都在里面）": "Command palette (everything below is in here)",
    "官方目录（3632 个）": "Official catalog (3632)",
    "就是当前会话，没有切换。": "That is already the current session; nothing to switch to.",
    "已切换 {opt_id}": "Switched {opt_id}",
    "已移除 {s.repo}": "Removed {s.repo}",
    "恢复会话失败：{err}": "Failed to resume session: {err}",
    "扫描技能失败：{msg}": "Skill scan failed: {msg}",
    "技能市场 · 市场源设置": "Skill Market · Market source settings",
    "插件市场 · 数据源设置": "Plugin Market · Data source settings",
    "未声明源码仓库，无法审计": "No source repository declared; cannot be audited",
    "正在安装 {spec}…": "Installing {spec}…",
    "正在并发探测全部候选源…": "Probing all candidate sources in parallel…",
    "说明文档（README）": "Documentation (README)",
    "读取插件失败：{msg}": "Failed to read plugins: {msg}",
    "跳过（已是要装的版本）：": "Skipped (already at the version to install):",
    "重绘整屏（画面花了时用）": "Redraw the whole screen (when the display gets garbled)",
    "项目 · .agents": "Project · .agents",
    "[harness 已退出]": "[harness exited]",
    "[读不到文件] {exc}": "[Cannot read file] {exc}",
    "harness 版本与升级": "Harness version & upgrade",
    "npmmirror（阿里）": "npmmirror (Alibaba)",
    "{d // 30} 个月前": "{d // 30} months ago",
    "{d // 365} 年前": "{d // 365} years ago",
    "{m} 分 {sec} 秒": "{m}m {sec}s",
    "不要自动启动 web 服务": "Don't start the web service automatically",
    "会话 · 模型 · 工作区": "Session · Model · Workspace",
    "升级命令返回非零：<br>": "Upgrade command returned non-zero:<br>",
    "取不到 dist-tags": "Cannot fetch dist-tags",
    "可升级到 {newest}": "Update available: {newest}",
    "已保存，市场下次拉取即生效": "Saved; takes effect on the market's next fetch",
    "按关键字过滤（本地匹配）…": "Filter by keyword (local match)…",
    "有 ⚠️ 装包时会执行代码": "Yes ⚠️ runs code when the package is installed",
    "正在关闭 harness…": "Shutting down harness…",
    "正在启动 harness…": "Starting harness…",
    "正在处理 {label}…": "Processing {label}…",
    "正在重启 harness…": "Restarting harness…",
    "没有勾选任何可更新的插件。": "No updatable plugins are checked.",
    "焦点：对话区 ↔ 说明面板": "Focus: chat area ↔ help panel",
    "版本文件里没有「版本」字段": "The version file has no \"version\" field",
    "纯界面插件，不注入宿主逻辑": "UI-only plugin; injects no host logic",
    "输入一个目录路径，回车即用": "Type a directory path and press Enter",
    "选中一行后可用下方按钮操作": "Select a row to use the buttons below",
    "需要 sudo（免密可用）": "Requires sudo (passwordless available)",
    " · 还有 {more} 项": " · {more} more",
    "[无法读取日志] {exc}": "[Cannot read log] {exc}",
    "[读取日志失败] {msg}": "[Failed to read logs] {msg}",
    "dump-config 失败": "dump-config failed",
    "ghfast.top（备用）": "ghfast.top (backup)",
    "npm 搜索（≤250 条）": "npm search (≤250 results)",
    "{int(days)} 天前": "{int(days)} days ago",
    "{title} · 扫码支持": "{title} · Scan to support",
    "{verb}失败：{msg}": "{verb} failed: {msg}",
    "仅在本地统计，不影响服务运行": "Counted locally only; does not affect the running service",
    "包里没有内置 harness": "No bundled harness inside the package",
    "只有一个发布版本，成熟度未知": "Only one published version; maturity unknown",
    "将执行：<br><code>": "Will run:<br><code>",
    "已重启，新版本应当已经生效。": "Restarted; the new version should be in effect now.",
    "查到了响应但没解析出版本号。": "Got a response but could not parse a version number.",
    "正在 {cwd} 新建会话…": "Creating a new session in {cwd}…",
    "正在卸载 {p.name}…": "Uninstalling {p.name}…",
    "正在新建会话（{cwd}）…": "Creating a new session ({cwd})…",
    "版本文件读不出来：{exc}": "Cannot read the version file: {exc}",
    "用户 · ~/.agents": "User · ~/.agents",
    "系统里找不到 npm，装不了": "npm not found on this system, cannot install",
    "请填写 owner/repo": "Enter owner/repo",
    "运行中（非 systemd）": "Running (not systemd)",
    "这个会话没有可切换的配置项。": "This session has no configuration options to switch.",
    "项目 · {project}": "Project · {project}",
    "（这个会话没有可显示的历史）": "(this session has no history to show)",
    "npm 包 @{latest}": "npm package @{latest}",
    "{disabled} 个已停用": "{disabled} disabled",
    "{label}失败：{msg}": "{label} failed: {msg}",
    "不是有效的 zip：{exc}": "Not a valid zip: {exc}",
    "压缩包里没有版本信息，拒绝更新": "No version information in the archive; update refused",
    "只有便携模式才支持控制台自更新": "Console self-update is only supported in portable mode",
    "客户端不支持 {method}": "Client does not support {method}",
    "工作目录 {self.cwd}": "Working directory {self.cwd}",
    "打开内置浏览器失败：{exc}": "Failed to open the built-in browser: {exc}",
    "找不到命令 {args[0]}": "Command not found: {args[0]}",
    "找不到命令 {argv[0]}": "Command not found: {argv[0]}",
    "把图片放进上面这个目录即可显示": "Put the image in the directory shown above and it will appear here",
    "（无 — 还没有安装任何技能）": "(none — no skills installed yet)",
    "，占用端口 {st.port}": ", using port {st.port}",
    "{len(plugins)} 个": "{len(plugins)}",
    "↑ 上面还有 {above} 行": "↑ {above} more lines above",
    "↓ 下面还有 {below} 行": "↓ {below} more lines below",
    "体检失败：{det.error}": "Checkup failed: {det.error}",
    "写入技能中枢状态失败：{exc}": "Failed to write skill hub state: {exc}",
    "启动 GUI 版失败：{exc}": "Failed to start the GUI version: {exc}",
    "启动 GUI 版失败：{msg}": "Failed to start the GUI: {msg}",
    "官方模型，按 Flash 档计价": "Official model, priced at the Flash channel",
    "当前没有可用地址（服务未运行？）": "No URL available right now (is the service not running?)",
    "打开 TUI 版失败：{msg}": "Failed to open the TUI: {msg}",
    "正在读取 harness 信息…": "Reading harness info…",
    "退出（会收干净 harness）": "Quit (shuts harness down cleanly)",
    "\n\n…（README 过长已截断）": "\n\n… (README is too long and was truncated)",
    "systemctl show 失败": "systemctl show failed",
    "web 版已经在跑，正在打开界面…": "The web version is already running; opening the UI…",
    "{theme.name}　（当前）": "{theme.name}  (current)",
    "全部读本机文件与环境，不发网络请求": "Reads only local files and the environment; makes no network requests",
    "包里没有 plugins.json": "No plugins.json in the package",
    "官方目录的 npm 包（可走镜像）": "Official catalog npm package (can be fetched through a mirror)",
    "已用 {how} 打开 TUI 版": "Opened the TUI version with {how}",
    "搜索插件（留空=按排名列出全部）…": "Search plugins (leave empty to list everything by rank)…",
    "未知模型（按 Flash 档兜底）": "Unknown model (falling back to the Flash tier)",
    "正在向 PyPI 查最新引擎版本…": "Checking PyPI for the latest engine version…",
    "等不到 harness 的访问地址": "Could not get the Harness URL",
    " · 工作目录 {self.cwd}": " · Working directory {self.cwd}",
    "发布地址上的版本：{version}": "Version at the release URL: {version}",
    "多模态实验版，按 Flash 价计费": "Experimental multimodal model, billed at Flash rates",
    "已新开一个控制台窗口运行 TUI 版": "Opened a new console window running the TUI version",
    "无安装期脚本，装包时不会执行任意代码": "No install-time scripts; installing runs no arbitrary code",
    "源共 {self._total:,}": "Source total {self._total:,}",
    "行首 / 行尾（同 ^A / ^E）": "Start / end of line (same as ^A / ^E)",
    "许可证：{self.license}": "License: {self.license}",
    "详情 / 打开 profile 目录": "Details / open profile directory",
    "需要重启 harness 才会生效。": "Takes effect after restarting harness.",
    "（npm 上没有提供 README）": "(no README provided on npm)",
    "harness stderr 尾巴：\n": "harness stderr tail:\n",
    "npm 受欢迎度/质量/维护度三项均值": "Average of npm popularity/quality/maintenance",
    "{' '.join(args)} 失败": "{' '.join(args)} failed",
    "{c} 有 {rel.tags[c]}": "{c} has {rel.tags[c]}",
    "{label}：不可用（{note}）": "{label}: unavailable ({note})",
    "↑回滚 {self.scroll} 行": "↑Scrolled back {self.scroll} lines",
    "勾选要升级的插件，然后点「更新选中」。": "Check the plugins to upgrade, then click \"Update selected\".",
    "包的元数据里没有 tarball 地址": "No tarball URL in the package metadata",
    "启动 harness 失败：{exc}": "Failed to start harness: {exc}",
    "启动终端失败（{how}）：{exc}": "Failed to launch the terminal ({how}): {exc}",
    "技能正文 — {skill.name}": "Skill content — {skill.name}",
    "更多（开发者工具 / 缩放 / 退出）": "More (dev tools / zoom / quit)",
    "点击放大{title}（方便手机扫码）": "Click to enlarge {title} (easier to scan with a phone)",
    "磁盘上已安装的技能：启用停用、查看正文": "Skills installed on disk: enable or disable, view content",
    "重启 harness（会断开当前会话）": "Restart harness (drops the current sessions)",
    "{int(days * 24)} 小时前": "{int(days * 24)} hours ago",
    "{skill.name} 已经是停用状态": "{skill.name} is already disabled",
    "{skill.name} 已经是启用状态": "{skill.name} is already enabled",
    "❤️ 感谢你的捐赠，这是我不断打磨的动力": "❤️ Thanks for your support — it keeps me improving this",
    "。升级不会自动重启，见下方「运行状态」。": ". The upgrade does not restart anything automatically; see \"Runtime status\" below.",
    "切换会话（{len(items)} 个）": "Switch session ({len(items)})",
    "升级到哪个通道；也可以直接选某个具体版本": "Which channel to upgrade to; you can also pick a specific version",
    "已安装 {inf.installed}；": "Installed {inf.installed};",
    "已安装的插件：安装、卸载、更新、启用停用": "Installed plugins: install, uninstall, update, enable or disable",
    "已连接 {name} {version}": "Connected to {name} {version}",
    "市场详情 — {plugin.name}": "Market details — {plugin.name}",
    "插件详情 — {plugin.name}": "Plugin details — {plugin.name}",
    "正在准备 web 服务，好了会自动跳转。": "Preparing the web service — you will be redirected automatically when it is ready.",
    "目标文件已存在，未改动：{target}": "Target file already exists; nothing changed: {target}",
    "读不到 {pkg_json}：{exc}": "Cannot read {pkg_json}: {exc}",
    "错误：dsh 不是可执行文件：{dsh}": "Error: dsh is not an executable file: {dsh}",
    "（没有匹配 “{needle}” 的行）": "(no lines match “{needle}”)",
    "<br><br>✓ 已重启，新版本已生效。": "<br><br>✓ Restarted; the new version is now in effect.",
    "HTTP {exc.code}，已回退缓存": "HTTP {exc.code}; fell back to cache",
    "harness 还没就绪，先等一会儿再试。": "harness is not ready yet; wait a moment and try again.",
    "{int(delta / 60)} 分钟前": "{int(delta / 60)} minutes ago",
    "⚠️ {DONORS_FILE} 读不出来": "⚠️ Could not read {DONORS_FILE}",
    "先点「检查更新」，拿到新版本号之后再更新。": "Click \"Check for updates\" first, then update once you have the new version number.",
    "全部 {len(ok)} 个插件已更新完成": "All {len(ok)} plugins updated",
    "写入后配置校验失败，已自动回滚：{err}": "Configuration validation failed after writing; rolled back automatically: {err}",
    "命令超时：{' '.join(args)}": "Command timed out: {' '.join(args)}",
    "在终端里打开对话界面（需要一个终端模拟器）": "Opens the chat UI in a terminal (requires a terminal emulator)",
    "按 Ctrl+D（或 Ctrl+C）退出。": "Press Ctrl+D (or Ctrl+C) to quit.",
    "本轮已取消（{elapsed:.1f}s）": "This turn was canceled ({elapsed:.1f}s)",
    "环境变量 {ENV_NPM_MIRROR}": "Environment variable {ENV_NPM_MIRROR}",
    "解析 dist-tags 失败：{exc}": "Failed to parse dist-tags: {exc}",
    "评分构成（控制台自算，全部依据可观测信号）": "Rating breakdown (computed by the console, based on observable signals only)",
    "错误：--cwd 不是一个目录：{cwd}": "Error: --cwd is not a directory: {cwd}",
    "（harness 没有输出 stderr）": "(harness produced no stderr output)",
    "harness 启动失败：{message}": "Failed to start harness: {message}",
    "加载失败（地址可能已失效，试试「重新加载」）": "Load failed (the URL may have expired — try \"Reload\")",
    "可更新 {have}→{p.version}": "Update available {have}→{p.version}",
    "已恢复会话 {session_id[:8]}": "Resumed session {session_id[:8]}",
    "已请求取消本轮…（等 harness 收尾）": "Cancellation requested for this turn… (waiting for harness to finish up)",
    "未同步到官方模型列表（离线或未配置 key）": "Official model list not synced (offline or no API key configured)",
    "正在更新 {len(names)} 个插件…": "Updating {len(names)} plugins…",
    "超过 {timeout} 秒还没装完，已中止": "Install did not finish within {timeout} seconds; aborted",
    "<b>{label} 失败</b>：{exc}": "<b>{label} failed</b>: {exc}",
    "F2 命令 · Tab 切焦点 · F1 面板": "F2 Commands · Tab Switch focus · F1 Panel",
    "harness 版本、安装位置、运行状态与升级": "Harness version, install location, run status and upgrade",
    "{int(delta / 3600)} 小时前": "{int(delta / 3600)} hours ago",
    "{int(delta / 86400)} 天前": "{int(delta / 86400)} days ago",
    "{method} 超时（{timeout}s）": "{method} timed out ({timeout}s)",
    "{self.cores_logical} 逻辑": "{self.cores_logical} logical",
    "⚠️ 以下项由环境变量托管（改这里不会生效）：": "⚠️ The following are managed by environment variables (changing them here has no effect):",
    "排除 tmpfs / proc 等虚拟文件系统": "Excludes virtual filesystems such as tmpfs / proc",
    "正在启动 harness…（首次可能要十几秒）": "Starting Harness… (the first launch can take 10–20 seconds)",
    "环境变量 {ENV_GITHUB_PROXY}": "Environment variable {ENV_GITHUB_PROXY}",
    "系统里没有安装 harness（npm -g）": "No harness installed on this system (npm -g)",
    "DSH 内置浏览器 · {theme.name}": "DSH Built-in Browser · {theme.name}",
    "npm 搜索失败：HTTP {exc.code}": "npm search failed: HTTP {exc.code}",
    "上一轮还在跑，先 Ctrl+C 取消再切换会话。": "The previous turn is still running; press Ctrl+C to cancel it before switching sessions.",
    "上一轮还在跑，先 Ctrl+C 取消再新建会话。": "The previous turn is still running; press Ctrl+C to cancel it before starting a new session.",
    "两份都升级（串行执行，避免两个 npm 抢缓存）": "Upgrade both (run serially so the two npm runs do not fight over the cache)",
    "区域预设「{self.preset.name}」": "Region preset \"{self.preset.name}\"",
    "取不到 npm 版本信息：{rel.error}": "Could not get npm version info: {rel.error}",
    "带宿主装配行（dsh.bundle.patch）": "Ships a host wiring entry (dsh.bundle.patch)",
    "当前不是以便携包方式运行，没有可以安装的内置位置": "Not running from a portable package, so there is no bundled location to install into",
    "拉不起 {self.dsh_bin}：{exc}": "Failed to launch {self.dsh_bin}: {exc}",
    "按事件发生时刻的峰谷档位计费；只统计本机会话记录": "Billed at the peak or off-peak rate in effect when each event occurred; local session records only",
    "追加 Chromium 开关（分号分隔），调试用": "Extra Chromium switches (semicolon-separated), for debugging",
    "harness 进程退出了（退出码 {code}）": "The harness process exited (exit code {code})",
    "npm 退出码 {proc.returncode}": "npm exit code {proc.returncode}",
    "{res.message}（工作目录 {cwd}）": "{res.message} (working directory {cwd})",
    "↑↓ 选择 · Enter 确认 · Esc 取消": "↑↓ Select · Enter Confirm · Esc Cancel",
    "　可更新到 v{plugin.version} ⬆": " Update available: v{plugin.version} ⬆",
    "体检中 {done}/{total}：{name}": "Checking up {done}/{total}: {name}",
    "关键字筛选（包名 / 说明 / 标签，本地匹配）…": "Filter by keyword (package name / description / tags, matched locally)…",
    "内置浏览器不可用：QtWebEngine 导入失败": "Built-in browser unavailable: QtWebEngine failed to import",
    "在已加载的技能里筛选（包名 / 说明 / 标签）…": "Filter loaded skills (package name / description / tags)…",
    "已启动 GUI 版（PID {proc.pid}）": "Started the GUI version (PID {proc.pid})",
    "已声明源码仓库：{self.repository}": "Source repository declared: {self.repository}",
    "找不到系统安装的 @deepseek-ai/dsh": "Cannot find a system-installed @deepseek-ai/dsh",
    "搜索接口不可用（仅可用于安装包，市场列表拉不出来）": "Search API unavailable (packages can still be installed, but the market list cannot be fetched)",
    "正在升级引擎（可能要几分钟，会下载一两百 MB）…": "Upgrading the engine (may take a few minutes; downloads 100-200 MB)…",
    "用手机相机或微信 / 支付宝「扫一扫」对着屏幕扫码": "Scan the code on screen with your phone camera or WeChat / Alipay Scan",
    "读取失败：{type(exc).__name__}": "Load failed: {type(exc).__name__}",
    "选择后立即生效。深色主题共 4 套，浅色 3 套。": "Applies immediately. 4 dark themes and 3 light themes.",
    "🌟 <b>虚位以待</b> —— 还没有捐赠记录。": "🌟 <b>Your name could be here</b> — no donations yet.",
    "\n标签：{'、'.join(skill.sets)}": "\nTags: {'、'.join(skill.sets)}",
    "harness 进程已经退出（退出码 {code}）": "The harness process has already exited (exit code {code})",
    "{type(exc).__name__}，已回退缓存": "{type(exc).__name__}; fell back to cache",
    "引擎已经是最新（{mine or latest}）。": "The engine is already up to date ({mine or latest}).",
    "正在更新第 {i}/{total} 个：{name}": "Updating {i}/{total}: {name}",
    "直接打开这个地址（默认用 harness 当前地址）": "Open this URL directly (defaults to the current Harness URL)",
    "绕过 10 分钟缓存，重新向 npm 查询各通道版本": "Bypass the 10-minute cache and re-query npm for the version on each channel",
    "输入内容后按 Enter 发送；Ctrl+D 退出。": "Type a message and press Enter to send; Ctrl+D to quit.",
    "\n… 以及另外 {len(names) - 12} 个": "\n… and {len(names) - 12} more",
    "dsh 可执行文件路径，默认取 PATH 里的 dsh": "Path to the dsh executable; defaults to the dsh found in PATH",
    "带 Web GUI 界面（dsh.client 声明）": "Ships a Web GUI (declared via dsh.client)",
    "正在从 npm 拉取第一页（结果会缓存 15 分钟）…": "Fetching the first page from npm (results are cached for 15 minutes)…",
    "正在处理 {len(todo)} 份 harness…": "Processing {len(todo)} harness installation(s)…",
    "正在拉取官方目录（约 3MB，之后走 6 小时缓存）…": "Fetching the official catalog (~3 MB, cached for 6 hours afterwards)…",
    "选中一行后可用下方按钮操作；项目/内置来源的技能为只读": "Select a row to use the buttons below; skills from project/built-in sources are read-only",
    "（{self._unaudited} 个未体检已排除）": "({self._unaudited} unaudited excluded)",
    "<br><br>正在重启 harness 让新版本生效…": "<br><br>Restarting harness so the new version takes effect…",
    "[stdout 非 JSON] {line[:400]}": "[stdout is not JSON] {line[:400]}",
    "gh-proxy.com（dshmarket 国内默认）": "gh-proxy.com (dshmarket default in China)",
    "stderr {self.stderr_count} 行": "stderr {self.stderr_count} lines",
    "压缩包里没有 app/main.py，不像是本控制台的包": "The archive has no app/main.py, so it does not look like a package for this console",
    "微信 / 支付宝都可以。点一下图片放大，手机更容易扫上。": "WeChat or Alipay. Click an image to enlarge it — easier to scan with a phone.",
    "<br><br>已经是要装的版本、**跳过不重复下载**：": "<br><br>Already at the version to install, **skipping to avoid re-downloading**:",
    "DeepSeek V4 Flash Vision (实验)": "DeepSeek V4 Flash Vision (experimental)",
    "harness 进程在启动阶段退出（退出码 {code}）": "The harness process exited during startup (exit code {code})",
    "{reachable}/{len(results)} 可达": "{reachable}/{len(results)} reachable",
    "会话工作目录（agent 在这个目录里干活），默认当前目录": "Session working directory (where the agent does its work); defaults to the current directory",
    "便携包不完整，缺少：{'、'.join(missing)}": "The portable bundle is incomplete; missing: {'、'.join(missing)}",
    "包名 / 包名@版本 / github:user/repo": "Package name / package@version / github:user/repo",
    "在目录里筛选（名称 / 作者 / 中英文描述，本地匹配）…": "Filter the catalog (name / author / Chinese or English description, matched locally)…",
    "已从官方 API 同步 {len(models)} 个模型": "Synced {len(models)} models from the official API",
    "已安装 {inf.installed}，各通道都不比它新。": "Installed {inf.installed}; no channel is newer than it.",
    "本机状态：已安装 v{installed_version}": "Local status: installed v{installed_version}",
    "环境变量 {ENV_REGISTRY_URL}（替换列表）": "Environment variable {ENV_REGISTRY_URL} (replaces the list)",
    "目录源 → {best['catalog'].label}": "Catalog source → {best['catalog'].label}",
    "harness profile 名，默认 {PROFILE}": "harness profile name; defaults to {PROFILE}",
    "owner/repo 或 owner/repo@v1.2.3": "owner/repo or owner/repo@v1.2.3",
    "{len(plugins)} 个 npm 插件，全部已是最新": "{len(plugins)} npm plugins, all up to date",
    "{note}；当前已经是最新（{mine or '?'}）。": "{note}; currently up to date ({mine or '?'}).",
    "全局目录不可写且免密 sudo 不可用——请在终端里手动升级": "The global directory is not writable and passwordless sudo is unavailable — please upgrade manually in a terminal",
    "包含安装期脚本（{names}）——安装时会执行代码，需谨慎": "Contains install-time scripts ({names}) — they run code during install, be careful",
    "安装后会改动插件装配列表，需要重启 harness 才生效。": "Installing changes the plugin wiring list; restart harness for it to take effect.",
    "官方插件目录（awesome-dsh-plugin.com）": "Official plugin catalog (awesome-dsh-plugin.com)",
    "已刷新 {len(refreshed)} 个启动器/说明文件": "Refreshed {len(refreshed)} launcher/readme files",
    "正在等待 harness…（{self._tries} 秒）": "Waiting for Harness… ({self._tries}s)",
    "自定义加速前缀，如 https://gh-proxy.com": "Custom proxy prefix, e.g. https://gh-proxy.com",
    "{skill.name} 已启用（{target.name}）": "{skill.name} enabled ({target.name})",
    "共 {len(plugins)} 个：{active} 个生效": "{len(plugins)} total: {active} active",
    "应用更新需要一个 .zip 地址；.json 只能用来检查版本": "Applying an update requires a .zip URL; .json can only be used to check the version",
    "新会话 {sid[:8]} · 工作目录 {self.cwd}": "New session {sid[:8]} · Working directory {self.cwd}",
    "该实例不由 systemd 托管，systemctl 动不了它": "This instance is not managed by systemd, so systemctl cannot control it",
    "\n日志尾部（{log_path('gui')}）：\n{tail}": "\nLog tail ({log_path('gui')}):\n{tail}",
    "下载失败：{type(exc).__name__}: {exc}": "Download failed: {type(exc).__name__}: {exc}",
    "切换失败：{type(exc).__name__}: {exc}": "Switch failed: {type(exc).__name__}: {exc}",
    "单位：每 100 万 tokens。当前档位价格随峰谷实时切换。": "Units: per 1M tokens. Prices for the current channel switch live between peak and off-peak.",
    "名单应当是数组，或 {\"donors\": [...]} 这种形式": "The list must be an array, or in the form {\"donors\": [...]}",
    "格式应为 owner/repo 或 owner/repo@ref": "Format should be owner/repo or owner/repo@ref",
    "版本迭代 {self.version_count} 次，维护活跃": "{self.version_count} versions published; actively maintained",
    "GUI 版启动后立刻退出（退出码 {code}）。{detail}": "The GUI version exited immediately after starting (exit code {code}). {detail}",
    "{spec} 安装完成，**需要重启** harness 才会加载": "{spec} installed — **restart required** before harness loads it",
    "停止/重启会断开正在进行的会话；浏览器登录态在干净退出时才会落盘。": "Stopping or restarting drops any sessions in progress; the browser login state is only saved on a clean exit.",
    "内置浏览器不可用：{info.get('错误', '未知原因')}": "Built-in browser unavailable: {info.get('错误', '未知原因')}",
    "内置浏览器启动后立刻退出（退出码 {code}）。{detail}": "The built-in browser exited immediately after starting (exit code {code}). {detail}",
    "正在启动 {harness.source_label(key)}…": "Starting {harness.source_label(key)}…",
    "版本 <b>{version or '?'}</b>{where}": "Version <b>{version or '?'}</b>{where}",
    "用包里的 node/npm 装到 {where}，不需要 sudo": "Install into {where} with the node/npm from the package; no sudo required",
    "确定卸载 {p.name} 吗？需要重启 harness 才生效。": "Uninstall {p.name}? This takes effect after restarting harness.",
    "装到 {root}\n目标：{channel} → {target}": "Install into {root}\nTarget: {channel} → {target}",
    "这一轮出错：{type(err).__name__}: {err}": "This turn failed: {type(err).__name__}: {err}",
    "[分发失败] {type(exc).__name__}: {exc}": "[dispatch failed] {type(exc).__name__}: {exc}",
    "harness 还没就绪或上一轮还在跑，先 Ctrl+C 取消再来。": "harness is not ready yet, or the previous turn is still running; press Ctrl+C to cancel first.",
    "{int((time.time() - at) / 60)} 分钟前": "{int((time.time() - at) / 60)} minutes ago",
    "内置 harness：装进包内的 harness/，不需要 sudo": "Bundled harness: installs into the package's own harness/, no sudo required",
    "命令超时（>{timeout}s）：{' '.join(argv)}": "Command timed out (>{timeout}s): {' '.join(argv)}",
    "完成：{len(ok)} 个成功，{len(failed)} 个失败": "Done: {len(ok)} succeeded, {len(failed)} failed",
    "探测完成：{reachable}/{len(results)} 可达": "Probe finished: {reachable}/{len(results)} reachable",
    "系统 harness：需要写系统目录（免密 sudo 可用时才能升）": "System harness: needs write access to the system directory (can only be upgraded when passwordless sudo is available)",
    "网络不可用，显示缓存结果（{type(exc).__name__}）": "Network unavailable; showing cached results ({type(exc).__name__})",
    "补齐体检 ({known}/{len(self._sorted)})": "Finish checkup ({known}/{len(self._sorted)})",
    "dist-tags 形状意外：{type(raw).__name__}": "Unexpected dist-tags shape: {type(raw).__name__}",
    "{Path(exe).name}（经 tui-shell.sh 转交）": "{Path(exe).name} (handed off via tui-shell.sh)",
    "共 {len(donors)} 位捐赠者 · 数据来自 {where}": "{len(donors)} donors · data from {where}",
    "操作超时（>{_TIMEOUT}s）：{' '.join(args)}": "Operation timed out (>{_TIMEOUT}s): {' '.join(args)}",
    "直连 registry.npmjs.org，不使用 GitHub 加速": "Direct connection to registry.npmjs.org, no GitHub proxy",
    "读取工作区失败：{type(exc).__name__}: {exc}": "Failed to load workspaces: {type(exc).__name__}: {exc}",
    "\n日志尾部（{log_path('browser')}）：\n{tail}": "\nLog tail ({log_path('browser')}):\n{tail}",
    "DSH 控制台 TUI · profile={self.profile}": "DSH Console TUI · profile={self.profile}",
    "TUI 异常退出：{type(exc).__name__}: {exc}": "TUI exited with an error: {type(exc).__name__}: {exc}",
    "{'启用' if enable else '停用'} {s.name}…": "{'启用' if enable else '停用'} {s.name}…",
    "合计 {plugin.rating:.2f} / 5　　{detail}": "Total {plugin.rating:.2f} / 5  {detail}",
    "已用内置浏览器打开 harness 界面（PID {proc.pid}）": "Opened the harness UI in the built-in browser (PID {proc.pid})",
    "读取会话列表失败：{type(exc).__name__}: {exc}": "Failed to load session list: {type(exc).__name__}: {exc}",
    "[权限] {title} → 按默认策略自动允许（{count} 个选项）": "[Permission] {title} → auto-allowed by the default policy ({count} options)",
    "{harness.source_label(key)}没有安装，无法启动。": "{harness.source_label(key)} is not installed, so it cannot be started.",
    "{plugin.name} {verb}（patch 层热重载，无需重启）": "{plugin.name} {verb} (patch-layer hot reload, no restart needed)",
    "压缩包里找不到 {portable.MANIFEST}，不像是本控制台的包": "Cannot find {portable.MANIFEST} in the archive; it does not look like a package for this console",
    "同时作用于控制台界面、内置浏览器（含右键菜单）与网页界面；切换后立即生效。": "Applies to the console UI, the built-in browser (including its context menu) and the web UI. Takes effect immediately.",
    "已加载 {self._shown} / {len(self._view)}": "Loaded {self._shown} / {len(self._view)}",
    "未找到 API key（~/.dsh/.credentials.yaml）": "API key not found (~/.dsh/.credentials.yaml)",
    "配置技能市场的 GitHub 仓库源（与 DSH 技能中枢共享同一份列表）": "Configure the GitHub repository sources for the Skill Market (shares one list with the DSH skill hub)",
    "错误：PATH 里找不到 dsh，用 --dsh PATH 指定它的位置。": "Error: dsh was not found in PATH; use --dsh PATH to point to it.",
    "（{'、'.join(dropped)} 非法 https 前缀，已忽略）": " ({'、'.join(dropped)} invalid https prefixes, ignored)",
    "<br><br>harness 当前没在运行，不用重启；下次启动就是新版本。": "<br><br>harness is not running right now, so there is no need to restart; the next start will use the new version.",
    "—— 以下是这个会话的历史（最近 {len(hist)} 条，暗色显示）——": "—— Below is this session's history (most recent {len(hist)} entries, shown dimmed) ——",
    "区域 / npm 镜像 / GitHub 加速 / 目录源，并可自动发现测速": "Region / npm mirror / GitHub proxy / catalog source, with automatic discovery and speed test",
    "填 zip 或 json 地址；留空 = 用内置的 GitHub 发布地址：": "Enter a zip or json URL; leave blank = use the built-in GitHub release URL:",
    "完整管理（安装 / 卸载 / 更新 / 启用停用 / 体检）见左侧「插件」页": "Full management (install / uninstall / update / enable / disable / checkup) is on the Plugins page",
    "正在启动 harness…（首次运行要准备 profile，可能要等一会儿）": "Starting harness… (the profile has to be prepared on first run, so this may take a while)",
    "{method} 失败：{err.get('message') or err}": "{method} failed: {err.get('message') or err}",
    "原生窗口里跑 harness 自己的 web UI——界面与功能同 web 版": "Runs harness's own web UI in a native window — same UI and features as the web version",
    "本轮结束：{reason or '未知原因'}（{elapsed:.1f}s）": "Turn finished: {reason or '未知原因'} ({elapsed:.1f}s)",
    "[启动中 {time.monotonic() - self._t0:.0f}s]": "[Starting {time.monotonic() - self._t0:.0f}s]",
    "{len(plugins)} 个 npm 插件，{updatable} 个可更新": "{len(plugins)} npm plugins, {updatable} updatable",
    "包里缺少 {'、'.join(missing)}，装不了（重新解压一份完整的包）": "Missing {'、'.join(missing)} in the package, cannot install (extract a complete package again)",
    "如果这个控制台帮到了你，欢迎请我喝杯咖啡 ☕ 每一份支持我都会记在下面的名单里。": "If this console helps you, you're welcome to buy me a coffee ☕ Every bit of support is listed below.",
    "已切换到 {harness.source_label(key)}，正在重读状态…": "Switched to {harness.source_label(key)}, re-reading status…",
    "还没配置发布地址（config.json 的 consoleUpdateUrl）": "No release URL configured yet (consoleUpdateUrl in config.json)",
    "（harness 进程退出码 {self.harness_exit_code}）": "(harness process exit code {self.harness_exit_code})",
    "GitHub 加速 → {best['proxy'].label or '直连'}": "GitHub proxy → {best['proxy'].label or '直连'}",
    "只比较来源为 npm 的插件；GitHub 源与本地目录源的插件没有可比的版本号。": "Only plugins whose source is npm are compared; plugins from GitHub sources or local directories have no comparable version number.",
    "当前用 **{harness.source_label(current)}**。　": "Currently using **{harness.source_label(current)}**. ",
    "形态「{HALF_FILTERS.get(mode, ('', ''))[0]}」": "Halves \"{HALF_FILTERS.get(mode, ('', ''))[0]}\"",
    "读不到 ~/.dsh/dsh-skill-hub.json（技能中枢还没初始化？）": "Cannot read ~/.dsh/dsh-skill-hub.json (skill hub not initialized yet?)",
    "[on_update 抛错] {type(exc).__name__}: {exc}": "[on_update raised] {type(exc).__name__}: {exc}",
    "npm install -g @deepseek-ai/dsh（需要免密 sudo）": "npm install -g @deepseek-ai/dsh (requires passwordless sudo)",
    "systemd 单元 dsh-web 的实况；「代码是否已生效」用安装时间的先后判断": "Live state of the systemd unit dsh-web; whether the code is in effect is judged by comparing install times",
    "{name or '全部插件'} 更新完成，**需要重启** harness 才会生效": "{name or '全部插件'} updated — **restart required** before it takes effect",
    "📮 联系我：QQ 894597841 　·　 邮箱 894597841@163.com": "📮 Contact: QQ 894597841  ·  Email 894597841@163.com",
    "    … 其余 {len(body) - TOOL_OUTPUT_LINES} 行省略": "    … {len(body) - TOOL_OUTPUT_LINES} more lines omitted",
    "systemctl --user restart dsh-web（会中断正在进行的会话）": "systemctl --user restart dsh-web (drops sessions in progress)",
    "{name} 已卸载，**需要重启** harness 才会生效（其数据目录不会被删除）": "{name} uninstalled — **restart required** before it takes effect (its data directory is not deleted)",
    "{res.message}　日志：{frontends.log_path('gui')}": "{res.message} Log: {frontends.log_path('gui')}",
    "{st.last_run_text}（已停 {st.stopped_for_text}）": "{st.last_run_text} (stopped for {st.stopped_for_text})",
    "还没有任何技能。可以去「技能市场」安装，或在 DSH 的 设置 → 技能 里从市场导入。": "No skills yet. Install some from the Skill Market, or import them from the market under Settings → Skills in DSH.",
    "只有用户级技能（~/.dsh/skills、~/.agents/skills）可启用/停用": "Only user-level skills (~/.dsh/skills, ~/.agents/skills) can be enabled or disabled",
    "引擎升级完成，当前 <b>{now or '?'}</b>。重启控制台与内置浏览器后生效。": "Engine upgraded; now on <b>{now or '?'}</b>. Takes effect after restarting the console and the built-in browser.",
    "<span style=\"color:{self.theme.ok}\">已安装</span>": "<span style=\"color:{self.theme.ok}\">Installed</span>",
    "[on_permission 抛错] {type(exc).__name__}: {exc}": "[on_permission raised] {type(exc).__name__}: {exc}",
    "便携包应当自带它；源码运行时用 pip install PySide6-Addons 安装。": "The portable bundle should include it; when running from source, install it with pip install PySide6-Addons.",
    "DSH 控制台 TUI：在终端里跟 harness（dsh --profile acp）对话。": "DSH Console TUI: chat with harness (dsh --profile acp) in your terminal.",
    "[运行中 {time.monotonic() - self.busy_since:.1f}s]": "[Running {time.monotonic() - self.busy_since:.1f}s]",
    "web profile 的组成（{PROFILE_DIR}）；bundle 按顺序叠加成配置树": "What the web profile is made of ({PROFILE_DIR}); bundles stack in order into the configuration tree",
    "主题（{bt.mode_label(mode)} · {len(candidates)} 套）": "Theme ({bt.mode_label(mode)} · {len(candidates)} themes)",
    "从技能市场源里移除 {s.repo}？\n\n已安装的技能文件不会被删除，只是不再从该源检查更新。": "Remove {s.repo} from the skill market sources?\n\nInstalled skill files are not deleted; they simply stop being checked for updates from that source.",
    "停止 dsh-web 会断开所有正在进行的会话。\n浏览器登录态会在干净退出时保存，确定停止吗？": "Stopping dsh-web drops all sessions in progress.\nThe browser login state is saved on a clean exit. Stop it?",
    "发布地址上的版本：{version}（构建于 {data.get('构建时间', '?')}）": "Version at the release URL: {version} (built {data.get('构建时间', '?')})",
    "腾讯云 npm 镜像 + gh-proxy.com 加速（与 dshmarket 国内档一致）": "Tencent Cloud npm mirror + gh-proxy.com proxy (matches the dshmarket China channel)",
    "运行中的就是当前安装的版本（安装于 {pkg_text}，进程启动于 {run_text}）。": "The running process is the currently installed version (installed at {pkg_text}, process started at {run_text}).",
    "{note}；当前 {mine or '?'} —— <b>可以更新</b>，点「更新控制台」。": "{note}; current {mine or '?'} — <b>an update is available</b>; click \"Update console\".",
    "{plugin.name} 已经是{'已启用' if enabled else '已停用'}状态": "{plugin.name} is already {'已启用' if enabled else '已停用'}",
    "{skill.name} 已停用（重命名为 {target.name}，内容未删除，可随时恢复）": "{skill.name} disabled (renamed to {target.name}; content not deleted, can be restored anytime)",
    "官方目录收录 3632 个（远大于一次 npm 搜索）；可填 https 地址或 npm:包名。": "The official catalog lists 3,632 entries (far more than a single npm search); enter an https address or npm:package-name.",
    "使用量按周下载量取对数刻度；活跃度看最近发布时间；生态看被依赖数；规范看是否声明许可证与源码仓库。": "Usage is the weekly download count on a log scale; activity looks at the most recent publish date; ecosystem looks at how many packages depend on it; standards look at whether a license and source repository are declared.",
    "{self.cores_physical} 物理 / {self.cores_logical} 逻辑": "{self.cores_physical} physical / {self.cores_logical} logical",
    "并发探测全部候选源，按「可达性 30 + 延迟 25 + 资源量 30 + 官方 15」加权评分排序。": "Probes all candidate sources in parallel and ranks them by a weighted score (reachability 30 + latency 25 + volume 30 + official 15).",
    "未找到收款码图片\n{self.path.name}\n\n期望位置：\n{self.path.parent}": "Donation code image not found\n{self.path.name}\n\nExpected location:\n{self.path.parent}",
    "[未处理的通知] {json.dumps(msg, ensure_ascii=False)[:200]}": "[Unhandled notification] {json.dumps(msg, ensure_ascii=False)[:200]}",
    "{note}（已写入 {sources.SKILL_HUB_STATE.name}，技能中枢下次扫描生效）": "{note} (written to {sources.SKILL_HUB_STATE.name}; takes effect on the skill hub's next scan)",
    "插件装配列表已改变，需要重启 harness 才会生效。\n\n⚠️ 重启会断开正在进行的会话。是否现在重启？": "The plugin wiring list has changed; it takes effect after restarting harness.\n\n⚠️ Restarting drops any sessions in progress. Restart now?",
    "<br><br>profile 目录是独立的：删掉它就等于把这个浏览器恢复出厂，你自己的浏览器完全不受影响。": "<br><br>The profile directory is separate: deleting it is like factory-resetting this browser, and your own browser is completely unaffected.",
    "<span style=\"color:{self.theme.text_faint}\">未安装</span>": "<span style=\"color:{self.theme.text_faint}\">Not installed</span>",
    "下载完成（{zip_path.stat().st_size / 1048576:.1f} MB），正在校验…": "Download complete ({zip_path.stat().st_size / 1048576:.1f} MB), verifying…",
    "内置浏览器不可用：QtWebEngine 导入失败（{type(exc).__name__}: {exc}）": "Built-in browser unavailable: QtWebEngine failed to import ({type(exc).__name__}: {exc})",
    "<b>名单文件解析失败</b>，暂时按空名单显示。<br><code>{path}</code><br>{err}": "<b>Could not parse the donor list file</b>; showing an empty list for now.<br><code>{path}</code><br>{err}",
    "{cfg.preset.name}　（npm: {reg}　GitHub 加速: {proxy or '不使用'}）": "{cfg.preset.name} (npm: {reg} · GitHub proxy: {proxy or '不使用'})",
    "控制台与标准对话框已立即切换。内置浏览器在**下次打开**时套用新语言（Chromium 的语言在进程启动时读取）。": "The console and standard dialogs switched immediately. The built-in browser takes the new language the **next time you open it** (Chromium reads its locale at process start).",
    "错误：当前终端不支持 curses（TERM={os.environ.get('TERM', '')}）：{exc}": "Error: this terminal does not support curses (TERM={os.environ.get('TERM', '')}): {exc}",
    "{plugin.name} 不在 dsh.profile.bundles 里，本来就不会被加载；如需启用请先重新安装。": "{plugin.name} is not in dsh.profile.bundles, so it would never be loaded; to enable it, reinstall it first.",
    "上下文 {_fmt_tokens(self.used)}/{_fmt_tokens(self.size)} {pct}%": "Context {_fmt_tokens(self.used)}/{_fmt_tokens(self.size)} {pct}%",
    "重启 harness 会断开所有正在进行的会话（包括浏览器里开着的界面）。\n浏览器登录态会在干净退出时保存。确定重启吗？": "Restarting harness drops all sessions in progress (including the UI open in your browser).\nThe browser login state is saved on a clean exit. Restart it?",
    "保证 harness 在跑，然后用**系统默认浏览器**打开带 token 的界面。\n机器上没有浏览器时用左边那个内置的。": "Makes sure harness is running, then opens the token-bearing UI in the **default system browser**.\nIf the machine has no browser, use the built-in one on the left.",
    "并发拉取每个包的注册表详情，才能判断「宿主半 / 前端半」。\n实测 16 并发约 0.3 秒/个（不体检时该筛选不可用）。": "Fetches registry details for every package concurrently — that is the only way to tell \"host half / client half\".\nMeasured at roughly 0.3 s per package with 16 concurrent requests (this filter is unavailable without a checkup).",
    "无法确定 {plugin.name} 的装配行 id（它没有提供 cordis.patch.yml），因此不能安全地停用。": "Cannot determine the wiring entry id for {plugin.name} (it does not provide cordis.patch.yml), so it cannot be disabled safely.",
    "即将安装：{spec}\n\n建议先点「详情 / 体检」确认这个包不会在安装时执行代码。\n安装后需要重启 harness 才会生效。": "About to install: {spec}\n\nConsider clicking \"Details / Checkup\" first to confirm this package does not run code while installing.\nTakes effect after restarting harness.",
    "磁盘 {len(info.disks)} 个 · 网卡 {len(info.net)} 个 · CPU 占用按 1 分钟负载折算": "{len(info.disks)} disks · {len(info.net)} NICs · CPU usage derived from 1-minute load",
    "npm 搜索一次最多返回 250 条；官方目录收录 3632 个插件，带中文描述与分类，但需要先拉取一次（约 3MB，之后走缓存）。": "npm search returns at most 250 results per query; the official catalog covers 3632 plugins with Chinese descriptions and categories, but it has to be fetched once (~3 MB, cached afterwards).",
    "端口 {other} 被别的进程（PID {other_pid}）占着；换端口可以设环境变量 {PORTABLE_PORT_ENV}": "Port {other} is taken by another process (PID {other_pid}); set the {PORTABLE_PORT_ENV} environment variable to use a different port",
    "用随包携带的 Chromium（QtWebEngine）打开界面。\n独立 profile，不碰你自己的浏览器；机器上没装浏览器也能用。": "Opens the UI with the Chromium (QtWebEngine) bundled in the package.\nSeparate profile, never touches your own browser; works even with no browser installed.",
    "包里没有内置 harness。<br><span style=\"font-size:11.5px\">安装到 {where}</span>": "No bundled harness in the package.<br><span style=\"font-size:11.5px\">Install into {where}</span>",
    "控制台已重新应用 {new_version or before}（版本没变）。备份在 app.bak-{stamp}/。重启控制台生效。": "Console reapplied {new_version or before} (version unchanged). Backup is in app.bak-{stamp}/. Restart the console for it to take effect.",
    "计费公式：input×缓存未命中 + output×输出 + (缓存读+缓存写)×缓存命中，按每次调用发生时刻的档位计费；跨峰谷不漂移。": "Billing formula: input×cache miss + output×output + (cache read + cache write)×cache hit, billed at the channel in effect when each call was made; no drift across the peak/off-peak boundary.",
    "{source_label(source)}已经是要装的版本 {installed}，没有重复下载。（要强制重装就换个通道，或先卸载再装。）": "{source_label(source)} is already the version to install ({installed}); nothing was downloaded again. (To force a reinstall, switch channels, or uninstall and install again.)",
    "确定卸载 {p.name} 吗？\n\n· 只从 profile 移除，插件自己的数据目录不会被删除\n· 卸载后需要重启 harness 才生效": "Uninstall {p.name}?\n\n· It is only removed from the profile; the plugin's own data directory is not deleted\n· A harness restart is required for it to take effect",
    "峰时段（本地时间）：{pricing.local_windows_text()}　·　UTC 周六/周日全天谷期　·　谷时价 = 峰时价的一半": "Peak hours (local time): {pricing.local_windows_text()} · All day off-peak on Sat/Sun UTC · Off-peak price = half the peak price",
    "已重新安装 {after.installed}（{PKG}@{target} 就是这个版本，没有变化）。想让新代码生效需要重启 harness。": "Reinstalled {after.installed} ({PKG}@{target} is already that version — no change). Restart harness for the new code to take effect.",
    "<b>主题</b>　当前 <b>{active.name}</b>　（{bt.mode_label(mode)} · {len(pool)} 套）": "<b>Theme</b>  Current: <b>{active.name}</b>  ({bt.mode_label(mode)} · {len(pool)} themes)",
    "DSH 内置浏览器：用随包携带的 Chromium（QtWebEngine）打开 harness 界面，独立 profile，不碰你自己的浏览器。": "DSH Built-in Browser: opens the Harness UI with the bundled Chromium (QtWebEngine) in its own profile, without touching your own browser.",
    "没有升级权限：{before.permission_note}\n请在终端里执行：sudo npm install -g {PKG}@{target}": "No permission to upgrade: {before.permission_note}\nPlease run this in a terminal: sudo npm install -g {PKG}@{target}",
    "数据来自官方 npm registry 的 dsh-plugin 关键字。安装前请先看详情里的「安装前体检」——那会告诉你这个包在装的时候会不会执行代码。": "Data comes from the dsh-plugin keyword on the official npm registry. Before installing, check the \"Pre-install checkup\" in Details — it tells you whether the package runs code while installing.",
    "{source_label(source)}安装完成：{after.installed}（{PKG}@{target}）。需要重启 harness 才会生效。": "{source_label(source)} installed: {after.installed} ({PKG}@{target}). Takes effect after restarting harness.",
    "外观：{bt.mode_label(mode)} · {theme.name}\n左键：选外观与主题（{len(bt.THEMES)} 套）\n右键：直接切下一套": "Appearance: {bt.mode_label(mode)} · {theme.name}\nLeft-click: choose appearance and theme ({len(bt.THEMES)} themes)\nRight-click: switch to the next theme",
    "将把以下 {len(names)} 个插件升级到最新版：\n\n{preview}\n\n逐个串行执行（pnpm 需要独占 node_modules），可能需要一会儿。": "The following {len(names)} plugins will be upgraded to the latest version:\n\n{preview}\n\nThey run one at a time in sequence (pnpm needs exclusive access to node_modules), so this may take a while.",
    "没找到可用的终端模拟器。\n可以在自己的终端里直接运行：\n  {manual}\n或者指定终端：{TERMINAL_ENV}='xterm -e' ./run.sh": "No usable terminal emulator found.\nYou can run it directly in your own terminal:\n  {manual}\nOr specify a terminal: {TERMINAL_ENV}='xterm -e' ./run.sh",
    "类型：{'含宿主半' if det.has_bundle else '无宿主半'}　{'含前端半' if det.has_client else '无前端半'}": "Type: {'含宿主半' if det.has_bundle else '无宿主半'} {'含前端半' if det.has_client else '无前端半'}",
    "⚠️ <b>磁盘上的代码比正在运行的进程新</b>（安装于 {pkg_text}，进程启动于 {run_text}）——新版本还没生效，点上面的「重启使其生效」。": "⚠️ <b>The code on disk is newer than the running process</b> (installed at {pkg_text}, process started at {run_text}) — the new version is not in effect yet; click \"Restart to apply\" above.",
    "**包里自带的那份**和**系统里 npm 装的那份**是两个独立的安装，可以同时存在、分别安装、分别升级。升级只替换磁盘上的代码；正在跑的进程要用新版本必须重启。": "**The copy bundled in the package** and **the copy installed by npm system-wide** are two separate installations: they can coexist and be installed and upgraded independently. An upgrade only replaces the code on disk; a running process must be restarted to use the new version.",
    "依赖 {len(det.dependencies)} 个　·　版本迭代 {det.version_count} 次　·　包体 {det.unpacked_mb} MB": "{len(det.dependencies)} dependencies · {det.version_count} releases · {det.unpacked_mb} MB unpacked",
    "系统里没有安装 harness（npm -g）。<br><span style=\"font-size:11.5px\">安装到系统全局目录，需要 sudo</span>": "harness is not installed on the system (npm -g).<br><span style=\"font-size:11.5px\">Installs into the global system directory; sudo required</span>",
    "{skill.name} 在只读根（{skill.root_label}）下，只有用户级技能（~/.dsh/skills 与 ~/.agents/skills）可以启用/停用": "{skill.name} is under a read-only root ({skill.root_label}); only user-level skills (~/.dsh/skills and ~/.agents/skills) can be enabled/disabled",
    "快捷键：Enter 发送 · Ctrl+C 取消本轮/空闲时退出 · Ctrl+D 退出 · ↑↓/PgUp/PgDn 滚动 · Ctrl+L 重绘 · Ctrl+U 清空输入行": "Shortcuts: Enter to send · Ctrl+C to cancel this turn / quit when idle · Ctrl+D to quit · ↑↓/PgUp/PgDn to scroll · Ctrl+L to redraw · Ctrl+U to clear the input line",
    "支持三种写法：\n　· npm 包名：dsh-cost-meter\n　· 指定版本：dsh-cost-meter@1.7.23\n　· GitHub 源：github:user/repo": "Three formats are supported:\n · npm package name: dsh-cost-meter\n · specific version: dsh-cost-meter@1.7.23\n · GitHub source: github:user/repo",
    "区域口径与已安装的 dshmarket 插件一致；环境变量（DSHM_NPM_MIRROR / DSHM_GITHUB_PROXY / DSHM_REGISTRY_URL）优先级更高。": "Region definitions match the installed dshmarket plugin; environment variables (DSHM_NPM_MIRROR / DSHM_GITHUB_PROXY / DSHM_REGISTRY_URL) take precedence.",
    "便携包不完整，装不了内置 harness：node={node}、npm={npm_cli}、prefix={prefix}。请重新解压一份完整的包，或用「系统 harness」那一份。": "Incomplete portable package, cannot install the bundled harness: node={node}, npm={npm_cli}, prefix={prefix}. Please extract a complete package again, or use the \"System harness\" one.",
    "控制台已从 {before or '?'} 更新到 {new_version}。旧的 app/ 备份在 app.bak-{stamp}/，确认没问题后可以删掉；重启控制台即可用上新版本。": "Console updated from {before or '?'} to {new_version}. The old app/ is backed up in app.bak-{stamp}/; you can delete it once everything checks out. Restart the console to use the new version.",
    "随包携带的 Chromium（QtWebEngine），**独立 profile**——不读也不写你的浏览器数据。机器上没装任何浏览器也能用；控制台「控制台」页有「用内置浏览器打开」按钮。": "A bundled Chromium (QtWebEngine) with its **own profile** — it neither reads nor writes your browser data. It works even if no browser is installed on the machine; the \"Dashboard\" page of the console has an \"Open in built-in browser\" button.",
    "与技能中枢共享同一份列表（读写 ~/.dsh/dsh-skill-hub.json 的 marketSources），所以在控制台加/删源，DSH 的「设置 → 技能 → 市场」里也会同步变化。": "Shares the same list as the skill hub (reads and writes marketSources in ~/.dsh/dsh-skill-hub.json), so adding or removing a source in the console also changes it under Settings → Skills → Market in DSH.",
    "内置浏览器不可用：QtWebEngine 导入失败（{type(exc).__name__}: {exc}）。便携包应当自带它；源码模式可 pip install PySide6-Addons。": "Built-in browser unavailable: QtWebEngine import failed ({type(exc).__name__}: {exc}). The portable package should include it; in source mode you can pip install PySide6-Addons.",
    "同一个 harness，三种用法。TUI 与 GUI 是控制台自带的前端，都通过 dsh --profile acp（标准 ACP v1）连过去。用哪一份 harness 由上面的**服务状态**决定。": "One harness, three ways to use it. TUI and GUI are frontends built into the console, and both connect through dsh --profile acp (standard ACP v1). Which copy of harness is used is decided by **Service status** above.",
    "DeepSeek Harness 的技能类插件（往 ctx.skills 注册能力的包）。在 npm 的 dsh-plugin 集合内按 skill 检索后，仅保留带技能关键字或包名含 skill 的包。": "DeepSeek Harness skill plugins (packages that register capabilities with ctx.skills). Candidates are found by searching the npm dsh-plugin set for \"skill\", then kept only if they carry a skill keyword or have \"skill\" in the package name.",
    "启用/停用写的是 patch 层（cordis.patch.yml），宿主会热重载，**无需重启**；\n卸载与更新会改动 package.json 的插件装配列表，**必须重启** harness 才生效。": "Enabling/disabling writes to the patch layer (cordis.patch.yml) and the host hot-reloads it, so **no restart needed**;\nuninstalling and updating change the plugin wiring list in package.json, and **require a restart** of harness to take effect.",
    "引擎包 PySide6-Essentials：当前 <b>{mine}</b>，最新 <b>{latest}</b> —— 可以点「升级引擎」。（升级只写包内 runtime/，不影响系统 Python。）": "Engine package PySide6-Essentials: current <b>{mine}</b>, latest <b>{latest}</b> — you can click \"Upgrade engine\". (The upgrade only writes runtime/ inside the package and does not affect the system Python.)",
    "\n（npm 照例拦下了 {detail} 的安装脚本。本机现装版本同样没跑过这些脚本、工作正常，所以默认不放开；万一升级后原生模块出问题，再按 npm 的提示加 --allow-scripts 重装一次即可。）": "\n(npm blocked the install scripts for {detail} as usual. The version installed here never ran them either and works fine, so they stay blocked by default; if a native module has problems after the upgrade, just reinstall once with --allow-scripts as npm suggests.)",
    "已从 {installed or '?'} 升级到 {after.installed}（{PKG}@{target}）。需要重启 harness 才会生效——重启会中断正在进行的会话，请在方便的时候点「重启」。": "Upgraded from {installed or '?'} to {after.installed} ({PKG}@{target}). It takes effect after restarting harness — restarting drops any sessions in progress, so click \"Restart\" when convenient.",
    "评分由控制台自算（0–5）：使用量按周下载取对数、活跃度看最近发布、\n生态看被依赖数、规范看是否声明许可证与仓库。\n不用 npm 的 score.detail——实测它对 250 个包恒为 1.0，没有区分度。": "Ratings are computed by the console itself (0–5): usage from the log of weekly downloads, activity from the latest release,\necosystem from the number of dependents, hygiene from whether a license and repository are declared.\nnpm's score.detail is not used — it measured a constant 1.0 across 250 packages, so it has no discriminating power.",
    "「停用」把 SKILL.md 重命名为 SKILL.md.disabled，并把变更同步进技能中枢的\nsidecar（~/.dsh/dsh-skill-hub.json 的 disabled 数组），两边状态一致。": "\"Disable\" renames SKILL.md to SKILL.md.disabled and syncs the change into the skill hub's\nsidecar (the disabled array in ~/.dsh/dsh-skill-hub.json), keeping both sides in sync.",
    "当前按<b style='color:{color}'>{phase.label}</b>（{multiplier}）计费，下一次切换：{'进入峰时段' if phase.next_into_peak else '进入谷时段'}": "Currently billed at <b style='color:{color}'>{phase.label}</b> ({multiplier}); next switch: {'进入峰时段' if phase.next_into_peak else '进入谷时段'}",
    "当前生效：npm → <b>{reg}</b>（{reg_src}）　·　GitHub 加速 → <b>{proxy or '不使用'}</b>（{proxy_src}）　·　目录源 {len(cat)} 个（{cat_src}）": "Active now: npm → <b>{reg}</b> ({reg_src}) · GitHub proxy → <b>{proxy or '不使用'}</b> ({proxy_src}) · {len(cat)} catalog sources ({cat_src})",
    "峰时段 {pricing.fmt_cny(t.peak_cny)} · 谷时段 {pricing.fmt_cny(t.offpeak_cny)} · 缓存命中 {t.cache_hit_rate:.1f}%　（详细拆分见「账单」页）": "Peak {pricing.fmt_cny(t.peak_cny)} · Off-peak {pricing.fmt_cny(t.offpeak_cny)} · Cache hit {t.cache_hit_rate:.1f}% (see the Billing page for a detailed breakdown)",
    "TUI 需要 curses 模块，当前 Python 里没有。\n  · Windows：装一下 windows-curses（便携包已内置，无需操作）\n  · 或者改用图形界面：启动DSH控制台  /  网页界面：启动DSH网页界面\n": "The TUI needs the curses module, which this Python does not have.\n  · Windows: install windows-curses (already bundled in the portable package, nothing to do)\n  · Or use the graphical UI: Start DSH Console  /  Web UI: Start DSH Web UI\n",
    "内置浏览器 {info['浏览器版本']}　·　Chromium {info['chromium'] or '?'}　·　Qt {info['qt']}　·　profile {human_size(int(info['缓存大小']))}": "Built-in browser {info['浏览器版本']} · Chromium {info['chromium'] or '?'} · Qt {info['qt']} · profile {human_size(int(info['缓存大小']))}",
    "{len(items)} 个 / {len(meta.get('categories') or {})} 分类　更新于 {meta.get('updated') or '—'}　缓存 {catalog.cached_age_text()}": "{len(items)} items / {len(meta.get('categories') or {})} categories · updated {meta.get('updated') or '—'} · cached {catalog.cached_age_text()}",
    "<br><br>内置那份装进包内 <b>harness/</b>，不需要 sudo；系统那份装进系统全局目录，通常需要免密 sudo。<br><b>装完会自动重启 harness 让新版本生效</b>（会中断正在进行的会话）。<br><br>确定吗？": "<br><br>The bundled copy installs into <b>harness/</b> inside the package and needs no sudo; the system copy installs into the global system directory and usually needs passwordless sudo.<br><b>harness is restarted automatically after installing so the new version takes effect</b> (this drops sessions in progress).<br><br>Continue?",
    "</code><br><br>内置浏览器的引擎就是 QtWebEngine（Chromium），所以升级它 = 升级包内的 PySide6。便携包里这会写进 <b>runtime/</b>，**不会碰系统 Python**。<br><br>升级完需要重启控制台和内置浏览器。<br><br>确定吗？": "</code><br><br>The built-in browser's engine is QtWebEngine (Chromium), so upgrading it = upgrading the PySide6 inside the package. In the portable package this writes into <b>runtime/</b> and **does not touch the system Python**.<br><br>You need to restart the console and the built-in browser afterwards.<br><br>Continue?",
    "{skill.description or '（无描述）'}\n来源：{skill.root_label}　·　形态：{skill.kind}　·　状态：{'已启用' if skill.enabled else '已停用'}　·　大小：{skill.size_text}\n路径：{skill.path}": "{skill.description or '（无描述）'}\nSource: {skill.root_label} · Kind: {skill.kind} · Status: {'已启用' if skill.enabled else '已停用'} · Size: {skill.size_text}\nPath: {skill.path}",
    "将从<br><code>{url}</code><br>下载新包并替换 <b>app/</b> 与 <b>tools/</b>。<br><br>你的 <b>home/</b>（会话、配置、插件）、<b>harness/</b>、<b>runtime/</b> 都不会动，旧代码会备份成 app.bak-&lt;时间&gt;。<br><br>确定吗？": "A new package will be downloaded from<br><code>{url}</code><br>and will replace <b>app/</b> and <b>tools/</b>.<br><br>Your <b>home/</b> (sessions, configuration, plugins), <b>harness/</b> and <b>runtime/</b> will be left alone, and the old code will be backed up as app.bak-&lt;time&gt;.<br><br>Continue?",
    "· <b>{verb} {harness.source_label(src)}</b>（{installed or '未安装'} → {channel} → {target_version}）<br><code>{' '.join(harness.update_command(target_version, source=src))}</code>": "· <b>{verb} {harness.source_label(src)}</b> ({installed or '未安装'} → {channel} → {target_version})<br><code>{' '.join(harness.update_command(target_version, source=src))}</code>",
    "⚠️ harness 正在运行，但<b>不是</b> dsh-web.service 拉起来的（PID {st.listener_pid}，进程 {owner}{port_part}）。「启动 / 重启」会因端口被占而失败，「停止」也停不掉它——要接管得先在启动它的那个终端里结束进程。带 token 的地址只打印在它自己的终端里，这里取不到，「打开界面」因此不可用。": "⚠️ harness is running, but it was <b>not</b> started by dsh-web.service (PID {st.listener_pid}, process {owner}{port_part}). \"Start / Restart\" will fail because the port is taken, and \"Stop\" cannot stop it either — to take over, first end the process in the terminal that started it. The token-bearing URL is only printed in that terminal, so it is not available here, which is why \"Open UI\" is unavailable.",
    "<b>DSH 控制台</b> v{__version__}<br>Python + PySide6 桌面控制台，用于管理 DSH Harness。<br><br>服务单元：<code>dsh-web.service</code>（systemd user）<br>配置目录：<code>~/.dsh</code><br>启动 wrapper：<code>~/.local/bin/dsh-web</code><br>打开脚本：<code>~/.local/bin/dsh-open</code>": "<b>DSH Console</b> v{__version__}<br>Python + PySide6 desktop console for managing DSH Harness.<br><br>Service unit: <code>dsh-web.service</code> (systemd user)<br>Config directory: <code>~/.dsh</code><br>Startup wrapper: <code>~/.local/bin/dsh-web</code><br>Browser opener: <code>~/.local/bin/dsh-open</code>",
    "\n/* ⚠️ 这里**不要**再用 `* { font-family: … }`。\n   通配规则会让每个控件都单独去解析一次字体，中文字体还要走回退链——实测这是\n   换主题慢（2.3 秒/次）和滚动掉帧的主要来源之一。字体改成在 QApplication 上设一次\n   （见 build_font / MainWindow.apply_theme），控件自动继承。 */\nQWidget {\n    background: {t.bg};\n    color: {t.text};\n    outline: none;\n}\n/* 滚动页的内层容器与滚动区视口都要显式给背景色。\n   纯 QWidget 只有配合 WA_StyledBackground 才会画 QSS 背景，而视口默认用的是\n   调色板底色——两者不一致时，卡片区下方会露出一大片异色，中间一道横向断裂。 */\n/* 内容区的底色用**侧边栏色**，不要用 {t.bg}。\n   {t.bg} 是整套配色里最暗的一档（比卡片和侧边栏都暗），而页面留白是围着卡片一圈的——\n   那一圈就成了一目了然的\"黑框\"（用户圈出来问的就是它）。\n   换成侧边栏色之后，整个窗口外围是一个色调，卡片靠自己的 surface + 描边浮在上面。 */\n#PageBody, #PageScroll, #PageScroll > QWidget > QWidget, QStackedWidget, #RightPane {\n    background: {t.bg};\n    border: none;\n}\n/* 侧边栏那几层必须用**侧边栏色**，不能沿用页面色。\n   之前这里统一写成了 {t.bg}，于是品牌区（顶部）和页脚（底部）是侧边栏色、\n   中间的导航滚动区是更暗的页面色——看起来就像一根黑柱插在中间，\n   而且和右边页面之间也露出一条黑缝（用户截图里那两处）。 */\n/* 侧边栏用**抬升面**色（和卡片同一档），内容留白用底色那一档：\n   两者有明显对比，又都不\"发黑\"，中间靠 1px 描边分界——既不是黑柱也不是糊成一片。 */\n#Sidebar {\n    background: {t.bg};\n    border-right: 1px solid {t.border};\n}\n#SidebarNav, #SidebarScroll, #SidebarScroll > QWidget > QWidget {\n    background: {t.bg};\n    border: none;\n}\n/* 侧边栏里的滚动条：轨道要跟侧边栏同色，否则又是一条异色竖条 */\n#SidebarScroll QScrollBar:vertical {\n    background: {t.bg};\n    width: 8px;\n    margin: 0;\n}\n#SidebarScroll QScrollBar::handle:vertical {\n    background: {t.border};\n    border-radius: 4px;\n    min-height: 24px;\n}\n#SidebarScroll QScrollBar::add-line, #SidebarScroll QScrollBar::sub-line {\n    height: 0; background: none;\n}\n#SidebarScroll QScrollBar::add-page, #SidebarScroll QScrollBar::sub-page {\n    background: {t.bg};\n}\nQToolTip {\n    background: {t.surface_alt};\n    color: {t.text};\n    border: 1px solid {t.border};\n    border-radius: 6px;\n    padding: 6px 8px;\n}\n\n/* ---------- 顶部功能区 ----------\n   通栏一条，和下面的页面紧贴，靠 1px 下边线分界（harness 的顶栏就是这个做法）。 */\n#TopBar {\n    background: {t.bg};\n    border-bottom: 1px solid {t.border};\n}\n#TopBarTitle {\n    font-size: 13.5px;\n    font-weight: 600;\n    color: {t.text};\n    background: transparent;\n}\n#TopBarStatus {\n    font-size: 12px;\n    color: {t.text_dim};\n    background: transparent;\n}\n\n/* ---------- 侧边栏 ---------- */\n#Brand {\n    font-size: 17px;\n    font-weight: 700;\n    color: {t.text};\n    padding: 18px 18px 4px 18px;\n    background: transparent;\n}\n#BrandSub {\n    font-size: 11px;\n    color: {t.text_faint};\n    padding: 0 18px 16px 18px;\n    background: transparent;\n}\n#NavButton {\n    background: transparent;\n    border: none;\n    border-radius: 9px;\n    padding: 10px 14px;\n    margin: 3px 10px;\n    text-align: left;\n    font-size: 13.5px;\n    color: {t.text_dim};\n}\n#NavButton:hover {\n    background: {t.surface_alt};\n    color: {t.text};\n}\n#NavButton:checked {\n    background: {t.sidebar_active};\n    color: {t.accent};\n    font-weight: 600;\n}\n/* ---------- 侧边栏底部的版本按钮 ----------\n   它同时是\"当前 harness 版本\"的展示和进入 Harness 页的入口，所以比导航项更收敛：\n   字号小一号、用等宽感的分隔、默认低对比，悬停/选中才亮起来。 */\n#VersionButton {\n    background: {t.surface_alt};\n    border: 1px solid {t.border};\n    border-radius: 9px;\n    padding: 8px 12px;\n    margin: 6px 12px 2px 12px;\n    text-align: left;\n    font-size: 12px;\n    color: {t.text_faint};\n}\n#VersionButton:hover {\n    border-color: {t.accent};\n    color: {t.accent};\n}\n#VersionButton:checked {\n    border-color: {t.accent};\n    color: {t.accent};\n    font-weight: 600;\n}\n\n#SidebarFooter {\n    color: {t.text_faint};\n    font-size: 11px;\n    padding: 12px 18px;\n    background: transparent;\n}\n\n/* ---------- 卡片 ---------- */\n/* harness 风格的卡片：**不是浮动的圆角盒子**，而是一段扁平内容，\n   靠一条 1px 上边线和上面的内容分开。整页看起来是一整块，而不是一堆方块。 */\n#Card {\n    background: {t.bg};      /* 不透明：看起来和 transparent 一样（页面底就是它），\n                                但省掉了每次重绘都要合成父级背景的开销 */\n    border: none;\n    border-top: 1px solid {t.border};\n    border-radius: 0;\n}\n/* 每页**第一张**卡片不画上边线。\n   它头顶就是顶部功能区的 border-bottom，两条线只隔一个页面留白——\n   视觉上就是\"一长一短两根线\"，很碍眼。留长的那条（通栏的），去掉短的。 */\n#Card[firstCard=\"true\"] {\n    border-top: none;\n}\n/* 标题下加一条极淡的分隔线：卡片内部\"标题 / 说明 / 内容\"三段本来只靠留白分，\n   窗口一拉长就糊成一片。加条线，分区一眼可见又不抢眼。 */\n#CardTitle {\n    background: transparent;\n    padding-bottom: 2px;\n}\n#CardTitle {\n    font-size: 14px;\n    font-weight: 600;\n    color: {t.text};\n    background: transparent;\n}\n#CardHint {\n    font-size: 11.5px;\n    color: {t.text_faint};\n    background: transparent;\n}\n#PageTitle {\n    font-size: 21px;\n    font-weight: 700;\n    color: {t.text};\n    background: transparent;\n}\n#PageSubtitle {\n    font-size: 12.5px;\n    color: {t.text_dim};\n    background: transparent;\n}\n\n/* ---------- 指标 ---------- */\n#MetricValue {\n    font-size: 21px;\n    font-weight: 700;\n    color: {t.text};\n    background: transparent;\n}\n#MetricLabel {\n    font-size: 11.5px;\n    color: {t.text_faint};\n    background: transparent;\n}\n#MetricValueAccent { font-size: 21px; font-weight: 700; color: {t.accent}; background: transparent; }\n\n/* ---------- 状态徽章 ---------- */\n#Pill {\n    border-radius: 11px;\n    padding: 4px 13px;\n    font-size: 12px;\n    font-weight: 600;\n}\n\n/* ---------- 按钮 ---------- */\nQPushButton {\n    background: {t.surface_alt};\n    color: {t.text};\n    border: 1px solid {t.border};\n    border-radius: 9px;\n    padding: 9px 18px;\n    font-size: 13px;\n}\nQPushButton:hover { border-color: {t.accent}; color: {t.accent}; }\nQPushButton:pressed { background: {t.border}; }\nQPushButton:disabled { color: {t.text_faint}; border-color: {t.border}; background: {t.surface}; }\n\nQPushButton#Primary {\n    background: {t.accent};\n    color: {t.accent_text};\n    border: 1px solid {t.accent};\n    font-weight: 600;\n}\nQPushButton#Primary:hover { background: {t.accent_hover}; border-color: {t.accent_hover}; }\nQPushButton#Primary:disabled { background: {t.surface_alt}; color: {t.text_faint}; border-color: {t.border}; }\n\nQPushButton#Danger { color: {t.danger}; }\nQPushButton#Danger:hover { border-color: {t.danger}; background: {t.surface_alt}; }\n/* #Danger 是 ID 选择器，优先级高于 `QPushButton:disabled`（CSS 里 ID > 类型+伪类），\n   不单独写禁用态的话「停止」按钮在不可用时仍是鲜红色，看着像能点。 */\nQPushButton#Danger:disabled { color: {t.text_faint}; border-color: {t.border}; background: {t.surface}; }\nQPushButton#Ghost { background: transparent; }\n\n/* 分段切换（插件 / 技能 二合一页） */\nQPushButton#Segment {\n    background: {t.surface_alt};\n    border: 1px solid {t.border};\n    border-radius: 8px;\n    padding: 7px 16px;\n    margin-right: 6px;\n    color: {t.text_dim};\n}\nQPushButton#Segment:hover { color: {t.text}; }\nQPushButton#Segment:checked {\n    background: {t.accent};\n    border-color: {t.accent};\n    color: {t.accent_text};\n    font-weight: 600;\n}\n\n/* ---------- 输入 ---------- */\nQComboBox, QLineEdit, QSpinBox {\n    background: {t.surface_alt};\n    border: 1px solid {t.border};\n    border-radius: 8px;\n    padding: 7px 10px;\n    color: {t.text};\n    selection-background-color: {t.accent};\n    selection-color: {t.accent_text};\n}\nQComboBox:hover, QLineEdit:focus { border-color: {t.accent}; }\nQComboBox::drop-down { border: none; width: 22px; }\nQComboBox QAbstractItemView {\n    background: {t.surface};\n    border: 1px solid {t.border};\n    border-radius: 8px;\n    selection-background-color: {t.accent};\n    selection-color: {t.accent_text};\n    padding: 4px;\n}\n\n/* ---------- 表格 ---------- */\n/* 注意：FixedTable 用的是 QTableView + 自定义模型（渲染虚拟化），\n   所以选择器必须同时覆盖 QTableView —— 只写 QTableWidget 的话，\n   QTableView 会退回系统默认样式，隔行变成刺眼的白底。 */\nQTableView, QTableWidget, QTreeWidget {\n    background: {t.surface};\n    alternate-background-color: {t.surface_alt};\n    border: 1px solid {t.border};\n    border-radius: 10px;\n    gridline-color: {t.border};\n    selection-background-color: {t.surface_alt};\n    selection-color: {t.text};\n}\nQTableView::item, QTableWidget::item, QTreeWidget::item {\n    padding: 7px 8px;\n    border: none;\n    color: {t.text};\n}\nQTableView::item:selected, QTableWidget::item:selected { color: {t.text}; }\nQHeaderView::section {\n    background: {t.surface_alt};\n    color: {t.text_dim};\n    border: none;\n    border-bottom: 1px solid {t.border};\n    padding: 9px 8px;\n    font-size: 12px;\n    font-weight: 600;\n}\nQTableCornerButton::section { background: {t.surface_alt}; border: none; }\n\n/* ---------- 滚动条 ---------- */\nQScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }\nQScrollBar::handle:vertical {\n    background: {t.border}; border-radius: 5px; min-height: 28px;\n}\nQScrollBar::handle:vertical:hover { background: {t.text_faint}; }\nQScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }\nQScrollBar::handle:horizontal { background: {t.border}; border-radius: 5px; min-width: 28px; }\nQScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }\nQScrollBar::add-page, QScrollBar::sub-page { background: transparent; }\n\n/* ---------- 其它 ---------- */\nQPlainTextEdit, QTextEdit {\n    background: {t.surface};\n    border: 1px solid {t.border};\n    border-radius: 10px;\n    color: {t.text};\n    font-family: \"JetBrains Mono\", \"Fira Code\", \"Noto Sans Mono CJK SC\", monospace;\n    font-size: 12px;\n    selection-background-color: {t.accent};\n    selection-color: {t.accent_text};\n}\nQProgressBar {\n    background: {t.surface_alt};\n    border: none;\n    border-radius: 5px;\n    height: 8px;\n    text-align: center;\n    color: transparent;\n}\nQProgressBar::chunk { background: {t.accent}; border-radius: 5px; }\nQCheckBox { spacing: 8px; background: transparent; }\nQCheckBox::indicator {\n    width: 16px; height: 16px;\n    border: 1px solid {t.border};\n    border-radius: 4px;\n    background: {t.surface_alt};\n}\nQCheckBox::indicator:checked { background: {t.accent}; border-color: {t.accent}; }\nQSplitter::handle { background: {t.border}; }\n\n/* ---------- 捐赠页 ---------- */\n/* 感谢语：这一页唯一的大字，别再给它加卡片标题（会和大字抢层级） */\n#DonateThanks {\n    font-size: 19px;\n    font-weight: 700;\n    color: {t.text};\n    background: transparent;\n}\n#DonateSub {\n    font-size: 12.5px;\n    color: {t.text_dim};\n    background: transparent;\n}\n/* 联系方式：用强调色，让\"想支持的人怎么找到我\"一眼可见，但不抢感谢语的字号 */\n#DonateContact {\n    font-size: 12.5px;\n    color: {t.accent};\n    background: transparent;\n    padding-top: 3px;\n}\n/* 收款码：两张等大的框，靠 surface + 描边从页面底色上浮起来。\n   图片本身是**整张海报**（自带品牌底色和白卡），所以框内不再垫白底——\n   垫了会在海报的四个圆角处露出一圈白边。 */\n#QrBox {\n    background: {t.surface};\n    border: 1px solid {t.border};\n    border-radius: 12px;\n}\n#QrBox:hover { border-color: {t.accent}; }\n#QrTitle {\n    font-size: 13px;\n    font-weight: 600;\n    color: {t.text};\n    background: transparent;\n}\n#QrTip {\n    font-size: 11.5px;\n    color: {t.text_faint};\n    background: transparent;\n}\n#QrImage {\n    background: transparent;\n    border: none;\n}\n/* 图片缺失时的占位说明：用警示色 + 虚线框，一眼看出是\"缺文件\"而不是\"排版坏了\" */\n#QrMissing {\n    background: {t.surface_alt};\n    border: 1px dashed {t.border};\n    border-radius: 10px;\n    color: {t.warn};\n    font-size: 11.5px;\n}\n/* 感谢者名单 */\n#DonorList { background: transparent; }\n#DonorRow {\n    background: transparent;\n    border-bottom: 1px solid {t.border};\n}\n/* 最后一行不画线，否则名单底下会多出一条悬空的横线 */\n#DonorRow[lastRow=\"true\"] { border-bottom: none; }\n#DonorRank {\n    color: {t.text_faint};\n    font-size: 11.5px;\n    font-weight: 600;\n    background: transparent;\n}\n#DonorName {\n    color: {t.text};\n    font-size: 13.5px;\n    font-weight: 600;\n    background: transparent;\n}\n#DonorAmount {\n    color: {t.ok};\n    font-size: 12.5px;\n    font-weight: 600;\n    background: transparent;\n}\n#DonorDate {\n    color: {t.text_faint};\n    font-size: 11.5px;\n    background: transparent;\n}\n/* 留言缩进到和名字对齐（让开编号那一段） */\n#DonorMsg {\n    color: {t.text_dim};\n    font-size: 12.5px;\n    background: transparent;\n    padding-left: 26px;\n}\n#DonorEmpty {\n    color: {t.text_dim};\n    font-size: 12.5px;\n    background: transparent;\n    padding: 8px 2px;\n}\n\nQFrame#HLine { background: {t.border}; max-height: 1px; border: none; }\n": "\n/* ⚠️ Do **not** use `* { font-family: … }` here again.\n   A wildcard rule makes every widget resolve its own font separately, and CJK fonts\n   also walk the fallback chain — measured as one of the main causes of slow theme\n   switching (2.3 s each) and dropped frames while scrolling. Set the font once on\n   QApplication instead (see build_font / MainWindow.apply_theme); widgets inherit\n   it automatically. */\nQWidget {\n    background: {t.bg};\n    color: {t.text};\n    outline: none;\n}\n/* Both the inner container of a scrolling page and the scroll area viewport need an\n   explicit background color. A plain QWidget only paints a QSS background together\n   with WA_StyledBackground, while the viewport defaults to the palette base color —\n   when the two disagree, a large off-color patch shows below the cards, with a\n   horizontal break across it. */\n/* Use the **sidebar color** for the content background, not {t.bg}.\n   {t.bg} is the darkest step in the whole palette (darker than both the cards and\n   the sidebar), while the page padding forms a ring around the cards — that ring\n   became an obvious \"black frame\" (the very thing a user circled and asked about).\n   With the sidebar color the whole window exterior is one tone, and the cards\n   float on top of it via their own surface + border. */\n#PageBody, #PageScroll, #PageScroll > QWidget > QWidget, QStackedWidget, #RightPane {\n    background: {t.bg};\n    border: none;\n}\n/* The sidebar layers must use the **sidebar color**, not the page color.\n   This used to be all {t.bg}, so the brand area (top) and the footer (bottom) were\n   sidebar-colored while the nav scroll area in between was the darker page color —\n   it looked like a black column stuck in the middle, and a black seam also showed\n   between it and the page on the right (both spots in the user's screenshot). */\n/* The sidebar uses the **raised surface** color (the same step as cards) and the\n   content padding uses the base step: the two contrast clearly without either going\n   \"black\", divided by a 1px border — neither a black column nor a blur. */\n#Sidebar {\n    background: {t.bg};\n    border-right: 1px solid {t.border};\n}\n#SidebarNav, #SidebarScroll, #SidebarScroll > QWidget > QWidget {\n    background: {t.bg};\n    border: none;\n}\n/* Scrollbar inside the sidebar: the track must match the sidebar color, otherwise\n   it is another off-color vertical bar */\n#SidebarScroll QScrollBar:vertical {\n    background: {t.bg};\n    width: 8px;\n    margin: 0;\n}\n#SidebarScroll QScrollBar::handle:vertical {\n    background: {t.border};\n    border-radius: 4px;\n    min-height: 24px;\n}\n#SidebarScroll QScrollBar::add-line, #SidebarScroll QScrollBar::sub-line {\n    height: 0; background: none;\n}\n#SidebarScroll QScrollBar::add-page, #SidebarScroll QScrollBar::sub-page {\n    background: {t.bg};\n}\nQToolTip {\n    background: {t.surface_alt};\n    color: {t.text};\n    border: 1px solid {t.border};\n    border-radius: 6px;\n    padding: 6px 8px;\n}\n\n/* ---------- Top bar ----------\n   One full-width strip flush against the page below, divided by a 1px bottom border\n   (this is how the harness top bar does it). */\n#TopBar {\n    background: {t.bg};\n    border-bottom: 1px solid {t.border};\n}\n#TopBarTitle {\n    font-size: 13.5px;\n    font-weight: 600;\n    color: {t.text};\n    background: transparent;\n}\n#TopBarStatus {\n    font-size: 12px;\n    color: {t.text_dim};\n    background: transparent;\n}\n\n/* ---------- Sidebar ---------- */\n#Brand {\n    font-size: 17px;\n    font-weight: 700;\n    color: {t.text};\n    padding: 18px 18px 4px 18px;\n    background: transparent;\n}\n#BrandSub {\n    font-size: 11px;\n    color: {t.text_faint};\n    padding: 0 18px 16px 18px;\n    background: transparent;\n}\n#NavButton {\n    background: transparent;\n    border: none;\n    border-radius: 9px;\n    padding: 10px 14px;\n    margin: 3px 10px;\n    text-align: left;\n    font-size: 13.5px;\n    color: {t.text_dim};\n}\n#NavButton:hover {\n    background: {t.surface_alt};\n    color: {t.text};\n}\n#NavButton:checked {\n    background: {t.sidebar_active};\n    color: {t.accent};\n    font-weight: 600;\n}\n/* ---------- Version button at the bottom of the sidebar ----------\n   It both shows the current harness version and opens the Harness page, so it is more\n   subdued than the nav items: one size smaller, a mono-ish separator, and low contrast\n   by default — it lights up only on hover/selection. */\n#VersionButton {\n    background: {t.surface_alt};\n    border: 1px solid {t.border};\n    border-radius: 9px;\n    padding: 8px 12px;\n    margin: 6px 12px 2px 12px;\n    text-align: left;\n    font-size: 12px;\n    color: {t.text_faint};\n}\n#VersionButton:hover {\n    border-color: {t.accent};\n    color: {t.accent};\n}\n#VersionButton:checked {\n    border-color: {t.accent};\n    color: {t.accent};\n    font-weight: 600;\n}\n\n#SidebarFooter {\n    color: {t.text_faint};\n    font-size: 11px;\n    padding: 12px 18px;\n    background: transparent;\n}\n\n/* ---------- Cards ---------- */\n/* A harness-style card: **not a floating rounded box** but a flat block of content,\n   separated from what is above it by a single 1px top border. The page reads as one\n   piece rather than a pile of boxes. */\n#Card {\n    background: {t.bg};      /* Opaque: looks the same as transparent (it IS the page\n                                background), but skips compositing the parent on every\n                                repaint */\n    border: none;\n    border-top: 1px solid {t.border};\n    border-radius: 0;\n}\n/* The **first** card on each page draws no top border.\n   Right above it is the top bar's border-bottom, with only the page padding between\n   the two lines — visually \"one long line and one short line\", which is jarring.\n   Keep the long one (full width) and drop the short one. */\n#Card[firstCard=\"true\"] {\n    border-top: none;\n}\n/* A very faint divider under the title: the card's \"title / hint / content\" sections\n   were separated by whitespace alone and blurred together once the window got taller.\n   One line makes the sections obvious at a glance without shouting. */\n#CardTitle {\n    background: transparent;\n    padding-bottom: 2px;\n}\n#CardTitle {\n    font-size: 14px;\n    font-weight: 600;\n    color: {t.text};\n    background: transparent;\n}\n#CardHint {\n    font-size: 11.5px;\n    color: {t.text_faint};\n    background: transparent;\n}\n#PageTitle {\n    font-size: 21px;\n    font-weight: 700;\n    color: {t.text};\n    background: transparent;\n}\n#PageSubtitle {\n    font-size: 12.5px;\n    color: {t.text_dim};\n    background: transparent;\n}\n\n/* ---------- Metrics ---------- */\n#MetricValue {\n    font-size: 21px;\n    font-weight: 700;\n    color: {t.text};\n    background: transparent;\n}\n#MetricLabel {\n    font-size: 11.5px;\n    color: {t.text_faint};\n    background: transparent;\n}\n#MetricValueAccent { font-size: 21px; font-weight: 700; color: {t.accent}; background: transparent; }\n\n/* ---------- Status pill ---------- */\n#Pill {\n    border-radius: 11px;\n    padding: 4px 13px;\n    font-size: 12px;\n    font-weight: 600;\n}\n\n/* ---------- Buttons ---------- */\nQPushButton {\n    background: {t.surface_alt};\n    color: {t.text};\n    border: 1px solid {t.border};\n    border-radius: 9px;\n    padding: 9px 18px;\n    font-size: 13px;\n}\nQPushButton:hover { border-color: {t.accent}; color: {t.accent}; }\nQPushButton:pressed { background: {t.border}; }\nQPushButton:disabled { color: {t.text_faint}; border-color: {t.border}; background: {t.surface}; }\n\nQPushButton#Primary {\n    background: {t.accent};\n    color: {t.accent_text};\n    border: 1px solid {t.accent};\n    font-weight: 600;\n}\nQPushButton#Primary:hover { background: {t.accent_hover}; border-color: {t.accent_hover}; }\nQPushButton#Primary:disabled { background: {t.surface_alt}; color: {t.text_faint}; border-color: {t.border}; }\n\nQPushButton#Danger { color: {t.danger}; }\nQPushButton#Danger:hover { border-color: {t.danger}; background: {t.surface_alt}; }\n/* #Danger is an ID selector and outranks `QPushButton:disabled` (ID beats type +\n   pseudo-class in CSS); without its own disabled state the Stop button stays bright\n   red when it is unavailable, looking clickable. */\nQPushButton#Danger:disabled { color: {t.text_faint}; border-color: {t.border}; background: {t.surface}; }\nQPushButton#Ghost { background: transparent; }\n\n/* Segmented switch (combined Plugins / Skills page) */\nQPushButton#Segment {\n    background: {t.surface_alt};\n    border: 1px solid {t.border};\n    border-radius: 8px;\n    padding: 7px 16px;\n    margin-right: 6px;\n    color: {t.text_dim};\n}\nQPushButton#Segment:hover { color: {t.text}; }\nQPushButton#Segment:checked {\n    background: {t.accent};\n    border-color: {t.accent};\n    color: {t.accent_text};\n    font-weight: 600;\n}\n\n/* ---------- Inputs ---------- */\nQComboBox, QLineEdit, QSpinBox {\n    background: {t.surface_alt};\n    border: 1px solid {t.border};\n    border-radius: 8px;\n    padding: 7px 10px;\n    color: {t.text};\n    selection-background-color: {t.accent};\n    selection-color: {t.accent_text};\n}\nQComboBox:hover, QLineEdit:focus { border-color: {t.accent}; }\nQComboBox::drop-down { border: none; width: 22px; }\nQComboBox QAbstractItemView {\n    background: {t.surface};\n    border: 1px solid {t.border};\n    border-radius: 8px;\n    selection-background-color: {t.accent};\n    selection-color: {t.accent_text};\n    padding: 4px;\n}\n\n/* ---------- Tables ---------- */\n/* Note: FixedTable uses QTableView + a custom model (rendering virtualization),\n   so the selectors must cover QTableView as well — with only QTableWidget,\n   QTableView falls back to the system default style and alternate rows turn into a\n   harsh white. */\nQTableView, QTableWidget, QTreeWidget {\n    background: {t.surface};\n    alternate-background-color: {t.surface_alt};\n    border: 1px solid {t.border};\n    border-radius: 10px;\n    gridline-color: {t.border};\n    selection-background-color: {t.surface_alt};\n    selection-color: {t.text};\n}\nQTableView::item, QTableWidget::item, QTreeWidget::item {\n    padding: 7px 8px;\n    border: none;\n    color: {t.text};\n}\nQTableView::item:selected, QTableWidget::item:selected { color: {t.text}; }\nQHeaderView::section {\n    background: {t.surface_alt};\n    color: {t.text_dim};\n    border: none;\n    border-bottom: 1px solid {t.border};\n    padding: 9px 8px;\n    font-size: 12px;\n    font-weight: 600;\n}\nQTableCornerButton::section { background: {t.surface_alt}; border: none; }\n\n/* ---------- Scrollbars ---------- */\nQScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }\nQScrollBar::handle:vertical {\n    background: {t.border}; border-radius: 5px; min-height: 28px;\n}\nQScrollBar::handle:vertical:hover { background: {t.text_faint}; }\nQScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }\nQScrollBar::handle:horizontal { background: {t.border}; border-radius: 5px; min-width: 28px; }\nQScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }\nQScrollBar::add-page, QScrollBar::sub-page { background: transparent; }\n\n/* ---------- Misc ---------- */\nQPlainTextEdit, QTextEdit {\n    background: {t.surface};\n    border: 1px solid {t.border};\n    border-radius: 10px;\n    color: {t.text};\n    font-family: \"JetBrains Mono\", \"Fira Code\", \"Noto Sans Mono CJK SC\", monospace;\n    font-size: 12px;\n    selection-background-color: {t.accent};\n    selection-color: {t.accent_text};\n}\nQProgressBar {\n    background: {t.surface_alt};\n    border: none;\n    border-radius: 5px;\n    height: 8px;\n    text-align: center;\n    color: transparent;\n}\nQProgressBar::chunk { background: {t.accent}; border-radius: 5px; }\nQCheckBox { spacing: 8px; background: transparent; }\nQCheckBox::indicator {\n    width: 16px; height: 16px;\n    border: 1px solid {t.border};\n    border-radius: 4px;\n    background: {t.surface_alt};\n}\nQCheckBox::indicator:checked { background: {t.accent}; border-color: {t.accent}; }\nQSplitter::handle { background: {t.border}; }\n\n/* ---------- Donate page ---------- */\n/* Thank-you line: the only large text on this page — do not give it a card title\n   (it would fight the large text for hierarchy) */\n#DonateThanks {\n    font-size: 19px;\n    font-weight: 700;\n    color: {t.text};\n    background: transparent;\n}\n#DonateSub {\n    font-size: 12.5px;\n    color: {t.text_dim};\n    background: transparent;\n}\n/* Contact: accent color, so \"how to reach me\" is visible at a glance without\n   competing with the thank-you line's size */\n#DonateContact {\n    font-size: 12.5px;\n    color: {t.accent};\n    background: transparent;\n    padding-top: 3px;\n}\n/* Donation codes: two equal-size boxes floating above the page background via\n   surface + border. The images are **full posters** (with their own brand\n   background and white card), so no white backing inside the box —\n   a backing would show a white ring at the poster's four rounded corners. */\n\n#QrBox {\n    background: {t.surface};\n    border: 1px solid {t.border};\n    border-radius: 12px;\n}\n#QrBox:hover { border-color: {t.accent}; }\n#QrTitle {\n    font-size: 13px;\n    font-weight: 600;\n    color: {t.text};\n    background: transparent;\n}\n#QrTip {\n    font-size: 11.5px;\n    color: {t.text_faint};\n    background: transparent;\n}\n#QrImage {\n    background: transparent;\n    border: none;\n}\n/* Placeholder shown when the image is missing: warn color + dashed border, so it\n   reads as a missing file rather than broken layout */\n#QrMissing {\n    background: {t.surface_alt};\n    border: 1px dashed {t.border};\n    border-radius: 10px;\n    color: {t.warn};\n    font-size: 11.5px;\n}\n/* Thank-you list */\n#DonorList { background: transparent; }\n#DonorRow {\n    background: transparent;\n    border-bottom: 1px solid {t.border};\n}\n/* The last row draws no line, otherwise a stray horizontal line hangs below the\n   list */\n#DonorRow[lastRow=\"true\"] { border-bottom: none; }\n#DonorRank {\n    color: {t.text_faint};\n    font-size: 11.5px;\n    font-weight: 600;\n    background: transparent;\n}\n#DonorName {\n    color: {t.text};\n    font-size: 13.5px;\n    font-weight: 600;\n    background: transparent;\n}\n#DonorAmount {\n    color: {t.ok};\n    font-size: 12.5px;\n    font-weight: 600;\n    background: transparent;\n}\n#DonorDate {\n    color: {t.text_faint};\n    font-size: 11.5px;\n    background: transparent;\n}\n/* Indent the message to align with the name (clearing the rank column) */\n#DonorMsg {\n    color: {t.text_dim};\n    font-size: 12.5px;\n    background: transparent;\n    padding-left: 26px;\n}\n#DonorEmpty {\n    color: {t.text_dim};\n    font-size: 12.5px;\n    background: transparent;\n    padding: 8px 2px;\n}\n\nQFrame#HLine { background: {t.border}; max-height: 1px; border: none; }\n",

    # ---- 「强制修复端口占用」相关（v1.2.0）
    "强制修复端口占用": "Force-fix port conflict",
    "端口被占住、harness 起不来也停不掉时用这个。\\n会先找出占着端口的进程，TERM 优雅退出（等 8 秒），不退就 KILL；\\n再清 systemd 失败状态、重启服务、顺手把显示器服务和孤儿 Xvfb 收拾干净。\\n⚠️ 这会结束别的进程，正在进行的会话会断开。": "Use this when the port is taken and the harness can neither start nor stop.\\nIt finds the process holding the port and stops it: TERM first (8s grace), then KILL;\\nthen clears the systemd failed state, restarts the service, and tidies up the display\\nservice and orphaned Xvfb processes.\\n⚠️ This ends other processes; running sessions will be disconnected.",
    "⚠️ 它是控制台的祖先进程（控制台跑在它的进程树里），改由 systemd 延迟清理，避免把控制台一起带走": "⚠️ It is an ancestor of the console (the console runs inside its process tree), so cleanup is deferred to systemd to avoid taking the console down too",
    "端口 {port} 本来就没被占用": "Port {port} was not occupied in the first place",
    "端口 {port} 被 PID {holder.pid}（{holder.name}）占用，但那个进程已经不见了": "Port {port} was held by PID {holder.pid} ({holder.name}), but that process is already gone",
    "端口 {port} 仍被 PID {holder.pid}（{holder.name}）占着，TERM/KILL 都没用": "Port {port} is still held by PID {holder.pid} ({holder.name}); neither TERM nor KILL worked",
    "端口 {port} 现在空着，下面的是上一次的占用者": "Port {port} is free now; the holder below is from the previous run",
    "端口 {port} 被 PID {holder.pid}（{holder.name}）占用：{holder.cmdline}": "Port {port} is held by PID {holder.pid} ({holder.name}): {holder.cmdline}",
    "端口 {port} 被 PID {holder.pid}（{holder.name}）占用": "Port {port} is held by PID {holder.pid} ({holder.name})",
    "端口仍被 PID {again.pid}（{again.name}）占着，继续处理": "Port is still held by PID {again.pid} ({again.name}); continuing",
    "没有要结束的进程": "No process to end",
    "拒绝操作受保护的进程 PID {pid}（控制台自己或 init）": "Refusing to touch protected PID {pid} (the console itself or init)",
    "PID {pid} 已经退出": "PID {pid} has already exited",
    "没有权限结束 PID {pid}（属于别的用户）": "No permission to end PID {pid} (owned by another user)",
    "已结束 PID {pid}（TERM 优雅退出）": "Ended PID {pid} (graceful TERM)",
    "PID {pid} 发了 TERM 和 KILL 都还在（可能是 D 状态或别的用户）": "PID {pid} survived both TERM and KILL (uninterruptible sleep, or another user)",
    "已强制结束 PID {pid}（TERM 超时后用了 KILL）": "Force-ended PID {pid} (KILL after TERM timed out)",
    "PID {pid} 发了 TERM 和 KILL 都没退，端口 {port} 仍被占用": "PID {pid} survived TERM and KILL; port {port} is still occupied",
    "清理了 {killed} 个无主 Xvfb": "Cleaned up {killed} orphaned Xvfb processes",
    "显示器服务已在运行": "The display service is already running",
    "显示器服务未能拉起：{exc}": "Could not start the display service: {exc}",
    "端口是控制台自己占的，跳过": "The console itself holds the port; skipping",
    "该进程由 {unit}.service 托管，先 systemctl stop": "That process is managed by {unit}.service; stopping it via systemctl first",
    "已交给 systemd 延迟处理（单元 dsh-port-fix-{pid}）": "Handed off to systemd for deferred cleanup (unit dsh-port-fix-{pid})",
    "已交给后台脚本延迟处理": "Handed off to a background script for deferred cleanup",
    "无法安排延迟清理：{exc}": "Could not schedule deferred cleanup: {exc}",
    "已清除 systemd 的失败状态": "Cleared the systemd failed state",
    "已重启 {unit}.service": "Restarted {unit}.service",
    "已启动 {unit}.service": "Started {unit}.service",
    "启动 {unit}.service 失败：{exc}": "Failed to start {unit}.service: {exc}",
    "端口已空，但服务还没起来（看日志页找原因）": "The port is free, but the service has not come up (check the Logs page)",
    "已交给 systemd 延迟清理，几秒后自动重启": "Deferred to systemd; it will restart automatically in a few seconds",
    "已修复：端口 {want} 已空出，harness 正在运行（PID {self.pid}，端口 {self.port}）": "Fixed: port {want} is free and the harness is running (PID {self.pid}, port {self.port})",
    "已修复：端口 {want} 已空出，harness 正在运行（PID {self.pid}）": "Fixed: port {want} is free and the harness is running (PID {self.pid})",
    "端口 {want} 已空出，但 harness 没起来": "Port {want} is free, but the harness did not start",
    "端口 {want} 仍被占用": "Port {want} is still occupied",
    "（没有需要处理的步骤）": "(nothing needed to be done)",
    "结果：{rep.summary()}": "Result: {rep.summary()}",
    "带 token 的地址：{rep.url}": "Authenticated URL: {rep.url}",
    "harness PID：{rep.pid}": "harness PID: {rep.pid}",
    "强制修复失败：{msg}": "Force-fix failed: {msg}",
    "正在强制修复端口占用…": "Force-fixing the port conflict…",
    "正在抢占端口 {port}…": "Taking over port {port}…",
    "⚠️ 强烈建议先「停止」再修复？不用，直接点「确定」就行：\n强制修复会自己按顺序结束占用者并重启服务，不需要你先把服务停掉。": "⚠️ No need to press \"Stop\" first: force-fix ends the holder and restarts the service itself.",
    "⚠️ harness 正在运行，但<b>不是</b> dsh-web.service 拉起来的（PID {st.listener_pid}，进程 {owner}{port_part}）。「启动 / 重启」会因端口被占而失败，「停止」也停不掉它——点「<b>强制修复端口占用</b>」可以结束它并把服务接管回来（会断开它正在进行的会话）。带 token 的地址只打印在它自己的终端里，这里取不到，「打开界面」因此不可用。": "⚠️ The harness is running, but it was <b>not</b> started by dsh-web.service (PID {st.listener_pid}, process {owner}{port_part}). \"Start / Restart\" will fail because the port is taken, and \"Stop\" cannot stop it either — press \"<b>Force-fix port conflict</b>\" to end it and let the service take over (this disconnects its running sessions). The token-bearing URL is only printed in its own terminal, so it is not available here, which is why \"Open UI\" is unavailable.",
}

#: 用来判断"这段文案里有没有中文"
_CJK = re.compile(r"[\u4e00-\u9fff]")


def system_default() -> str:
    """**首次启动**用哪种语言：跟随计算机系统的语言设置。

    判定顺序：Qt 的 ``QLocale.system()``（Windows 上就是"系统显示语言"，最准）→
    环境变量 ``LANG`` / ``LC_ALL`` / ``LC_MESSAGES``（Linux）→ ``locale``。
    只要是中文（zh / zh_CN / zh_TW / zh_Hans…）就用简体中文，**其余一律英文**。
    """
    name = ""
    try:
        from PySide6.QtCore import QLocale

        name = QLocale.system().name() or ""
    except Exception:                        # noqa: BLE001 - 没有 Qt / 取不到就用环境变量
        pass
    if not name:
        import os

        for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
            value = (os.environ.get(var) or "").strip()
            if value:
                name = value.split(".")[0]
                break
    if not name:
        import locale

        try:
            name = locale.getlocale()[0] or ""
        except Exception:                    # noqa: BLE001
            name = ""
    return LANG_ZH if name.lower().startswith("zh") else LANG_EN


def current() -> str:
    """当前语言 key。

    **没设过就跟随系统语言**（中文系统 → 简体中文，其它 → English (US)）；
    用户在设置页选过一次之后就按他选的来。
    """
    value = (config.get(config.KEY_LANGUAGE) or "").strip()
    return value if value in dict(LANGUAGES) else system_default()


def set_current(key: str) -> None:
    """记住语言选择（下次启动沿用）。"""
    if key in dict(LANGUAGES):
        config.put(config.KEY_LANGUAGE, key)


def is_en() -> bool:
    return current() == LANG_EN


def tr(zh: str) -> str:
    """把界面上的中文原文翻成当前语言；查不到就原样返回。

    查不到时**返回中文**是刻意的：本项目正在逐页补翻译，没补到的地方宁可显示中文，
    也不该出现空白或 key 本身。
    """
    if current() == LANG_ZH:
        return zh
    return _EN.get(zh, zh)


#: 按"长键优先"排好的键表——短键先替换会把长键切碎（例如先换掉"会话"，
#: "会话花费"就再也匹配不上了）。
#: 英文译文 → 中文原文（懒建）。
#:
#: **切回中文全靠它**：如果程序**启动时就是英文**，`translate_widgets` 第一次看到的
#: 文案就是英文，存下来的"原文"也是英文——切回中文时从英文原文再算，结果还是英文
#: （用户实测就是这个现象：点「简体中文」后界面基本没变）。有了反向表就能把英文还原。
_EN_REVERSE: dict[str, str] = {}


def _reverse() -> dict[str, str]:
    if not _EN_REVERSE:
        for zh, en in _EN.items():
            _EN_REVERSE.setdefault(en, zh)     # 一对多时保留第一条，够用
    return _EN_REVERSE


def _keys_longest_first() -> list[str]:
    return sorted(_EN, key=len, reverse=True)


#: 带 ``{占位符}`` 的键编译成的正则：**渲染后**的文案（值已经填进去了）也能命中。
#: 例：键 ``"已加载 {n} / {total}"`` → 正则 ``已加载 (.*?) / (.*?)``，
#: 命中后把捕获到的值按位置填回译文。
_TPL_CACHE: list[tuple[re.Pattern[str], str, int]] = []


def _templates() -> list[tuple[re.Pattern[str], str, int]]:
    if not _TPL_CACHE:
        for zh, en in _EN.items():
            if "{" not in zh:
                continue
            parts = re.split(r"\{[^}]*\}", zh)
            if len(parts) < 2:
                continue
            rx = "".join(re.escape(p) + "(.*?)" for p in parts[:-1]) + re.escape(parts[-1])
            n = len(parts) - 1
            # 译文的占位符个数必须与原文一致，否则"按位置填值"会串位，
            # 产出「详细拆m见」这种半截乱码——宁可退化成子串替换，也不要乱码。
            if len(re.findall(r"\{[^}]*\}", en)) != n:
                continue
            # 固定文字太少的模板会匹配**任意**文本（实测：某个"只有占位符"的条目
            # 让每个按钮文字后面都多出一个" 个"）。要求至少 2 个字的固定文字。
            if len("".join(parts)) < 2:
                continue
            try:
                _TPL_CACHE.append((re.compile(rx, re.S), en, n))
            except re.error:              # 病态正则直接跳过，绝不让它拖垮界面
                continue
        _TPL_CACHE.sort(key=lambda t: -len(t[0].pattern))   # 长模板优先
    return _TPL_CACHE


#: 反向模板：英文译文编译的正则 → 中文原文（切回中文时给动态文案用）
_TPL_REV: list[tuple[re.Pattern[str], str, int]] = []


def _reverse_templates() -> list[tuple[re.Pattern[str], str, int]]:
    if not _TPL_REV:
        for zh, en in _EN.items():
            if "{" not in en:
                continue
            parts = re.split(r"\{[^}]*\}", en)
            if len(parts) < 2 or len(re.findall(r"\{[^}]*\}", zh)) != len(parts) - 1:
                continue
            if len("".join(parts)) < 2:       # 同上：模板必须有足够的固定文字
                continue
            rx = "".join(re.escape(p) + "(.*?)" for p in parts[:-1]) + re.escape(parts[-1])
            try:
                _TPL_REV.append((re.compile(rx, re.S), zh, len(parts) - 1))
            except re.error:
                continue
        _TPL_REV.sort(key=lambda t: -len(t[0].pattern))
    return _TPL_REV


def _fill(template: str, values: list[str]) -> str:
    """把捕获到的值按位置填回译文里的 ``{...}``（译文占位符顺序与原文一致）。"""
    out = template
    for v in values:
        out = re.sub(r"\{[^}]*\}", lambda _m, v=v: v, out, count=1)
    return out


def replace_all(text: str) -> str:
    """把一段文案里出现的所有中文条目替换成当前语言（中文模式原样返回）。

    用"子串替换"而不是"整串查表"是刻意的：界面上的文案大量是**拼出来的**
    （``f"共 {n} 位捐赠者"``、``f"{name}：{err}"``、带 HTML 的整段说明），
    整串查表几乎一条都命中不了；子串替换能把这些都覆盖掉。
    """
    if not text:
        return text
    if current() == LANG_ZH:
        # 中文模式：**整串**命中才还原（不做子串替换，避免误伤）。
        hit = _reverse().get(text)
        if hit:
            return hit
        # 带占位符的（"Running (System harness)"）走反向模板
        for rx, zh, n in _reverse_templates():
            m = rx.fullmatch(text)
            if m:
                return _fill(zh, [m.group(i + 1) for i in range(n)])
        return text
    out = text
    # ① 先跑"带占位符"的模板：动态文案（"已加载 80 / 80"、"运行中（系统 harness）"…）
    #    只有这一步能救——它们的值已经填进去了，整串查表永远命中不了。
    # ⚠️ 必须 fullmatch：search 会在**无关短语中间**命中模板（"详细拆分见…" 被切成
    # "详细拆m见…"就是这么来的）。整串匹配才安全，宁可少翻一条。
    m = None
    for rx, en, n in _templates():
        m = rx.fullmatch(out)
        if m:
            out = _fill(en, [m.group(i + 1) for i in range(n)])
            break
    # ② 再按整串/子串替换（长键优先）
    for zh in _keys_longest_first():
        if zh in out:
            out = out.replace(zh, _EN[zh])
    return out


def _tr_in_place(obj, getter, setter, key: str = "text") -> bool:
    """把一个控件的文案翻成当前语言；**首次翻译时先把原文存进动态属性**。

    存原文是"切回中文"能生效的关键：控件上的中文一旦被换成英文就再也找不回来了，
    所以必须留一份原件，每次都从原件重新翻译（而不是在译文上再翻一次）。

    ⚠️ ``key`` 必须**每个属性一个**：一个控件有 windowTitle / toolTip / text 好几处
    文案，早先它们共用同一个属性名，于是第一次调用（windowTitle，多数控件是空串）
    就把空串存成了"原文"，后面 text / toolTip 全被判成"没有原文"直接跳过——
    表现就是"翻了一半"。这个坑很小但很致命。
    """
    prop = f"_dsh_i18n_src_{key}"
    src = obj.property(prop)
    if src is None:
        try:
            src = getter()
        except Exception:                    # noqa: BLE001 - 控件已销毁之类
            return False
        obj.setProperty(prop, src)
    # 存的若是英文（英文模式下建的控件），先换回中文再当原文——否则切不回中文
    if isinstance(src, str) and src and not _CJK.search(src):
        zh = _reverse().get(src)
        if zh:
            obj.setProperty(prop, zh)
            src = zh
    if not isinstance(src, str) or not src:
        return False
    new = replace_all(src)
    try:
        if getter() != new:
            setter(new)
            return True
    except Exception:                        # noqa: BLE001
        pass
    return False


def translate_widgets(root) -> int:
    """遍历一棵控件树，把可见文案翻成当前语言。返回改了几处。

    为什么用"遍历"而不是逐处 ``self.tr(...)``：项目里的界面文案是直接写死的中文
    字面量（UI 层 290+ 处），逐个改既慢又必漏；遍历一次能把**已经建出来的**控件
    全覆盖。懒加载的页面在它第一次显示（``activate()``）时再走一遍即可。
    """
    from PySide6.QtWidgets import (
        QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit, QTabWidget, QTableWidget,
        QTableView, QWidget,
    )

    if root is None:
        return 0
    changed = 0
    widgets = [root]
    if isinstance(root, QWidget):
        widgets += root.findChildren(QWidget)
    for w in widgets:
        # 通用：窗口标题、工具提示、右键菜单里的动作
        changed += _tr_in_place(w, w.windowTitle, w.setWindowTitle, "wt")
        changed += _tr_in_place(w, w.toolTip, w.setToolTip, "tip")
        try:
            for act in w.actions():
                changed += _tr_in_place(act, act.text, act.setText, "atext")
                changed += _tr_in_place(act, act.toolTip, act.setToolTip, "atip")
        except Exception:                    # noqa: BLE001
            pass
        if isinstance(w, QLineEdit):
            # 占位提示（"搜索插件（留空=按排名列出全部）…"）也是界面文案；
            # 之前漏了 QLineEdit.placeholderText（实测：英文界面里输入框还是中文）。
            changed += _tr_in_place(w, w.placeholderText, w.setPlaceholderText, "ph")
        elif isinstance(w, QLabel):
            changed += _tr_in_place(w, w.text, w.setText, "text")
        elif isinstance(w, QAbstractButton):
            changed += _tr_in_place(w, w.text, w.setText, "text")
        elif isinstance(w, QGroupBox):
            changed += _tr_in_place(w, w.title, w.setTitle, "title")
        elif isinstance(w, QComboBox):
            for i in range(w.count()):
                # 下拉项要逐项翻译：setItemText 会重置当前项，所以先记下来
                cur = w.currentIndex()
                src = w.itemData(i, Qt.UserRole + 1) if hasattr(Qt, "UserRole") else None
                if src is None:
                    src = w.itemText(i)
                    w.setItemData(i, src, Qt.UserRole + 1)
                new = replace_all(src)
                if w.itemText(i) != new:
                    w.setItemText(i, new)
                    changed += 1
                w.setCurrentIndex(cur)
        elif isinstance(w, QTabWidget):
            for i in range(w.count()):
                src = w.tabBar().tabData(i)
                if src is None:
                    src = w.tabText(i)
                    w.tabBar().setTabData(i, src)
                new = replace_all(src)
                if w.tabText(i) != new:
                    w.setTabText(i, new)
                    changed += 1
        if isinstance(w, (QTableView, QTableWidget)):
            # 表格内容由模型（components._TableModel）按当前语言给出，这里让视图重画
            try:
                w.viewport().update()
            except Exception:            # noqa: BLE001
                pass
            hh, vh = w.horizontalHeader(), w.verticalHeader()
            for i in range(w.model().columnCount() if w.model() else 0):
                item = hh.model().headerData(i, Qt.Horizontal) if hh.model() else None
                if isinstance(item, str):
                    new = replace_all(item)
                    if new != item:
                        w.model().setHeaderData(i, Qt.Horizontal, new)
                        changed += 1
            for i in range(w.model().rowCount() if w.model() else 0):
                item = vh.model().headerData(i, Qt.Vertical) if vh.model() else None
                if isinstance(item, str):
                    new = replace_all(item)
                    if new != item:
                        w.model().setHeaderData(i, Qt.Vertical, new)
                        changed += 1
    return changed


def duration(days: int = 0, hours: int = 0, minutes: int = 0) -> str:
    """按时长**整串**拼出来：中文 ``3 天 5 小时18分`` / 英文 ``3d 5h 18m``。

    为什么要专门一个函数：原来是在 sysinfo 里把 ``f"{h} 小时"``、``f"{m} 分"`` 拼起来，
    运行时得到的是 ``"5 小时18分"``——**任何"整串模板"都对不上**，于是英文界面里
    残留中文（实测截图：Uptime 显示 "5 小时18m"）。时长这种"由代码拼出来、又要跟着
    语言变"的文案，就得在拼的时候问语言，而不是拼完再翻译。
    """
    if is_en():
        bits = []
        if days:
            bits.append(f"{days}d")
        if hours or days:
            bits.append(f"{hours}h")
        bits.append(f"{minutes}m")
        return " ".join(bits)
    bits = []
    if days:
        bits.append(f"{days} 天")
    if hours or days:
        bits.append(f"{hours} 小时")
    bits.append(f"{minutes}分")
    return "".join(bits)


def chromium_lang(lang: str | None = None) -> str:
    return _CHROMIUM.get(lang or current(), "zh-CN")


def prepare_environment() -> None:
    """**必须在 QApplication / QtWebEngine 初始化之前**调用。

    做两件事：
    * 把 ``--lang=zh-CN`` 追加进 ``QTWEBENGINE_CHROMIUM_FLAGS``（已是最后一件事的机会，
      引擎一起来就读不到了）；
    * 定下 ``QLocale``，让 Qt 的数字/日期格式和标准对话框跟着语言走。
    """
    import os

    from PySide6.QtCore import QLocale

    lang = current()
    flag = f"--lang={chromium_lang(lang)}"
    existing = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").strip()
    if "--lang=" not in existing:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (existing + " " + flag).strip()
    QLocale.setDefault(QLocale(lang))


def install_translators(app) -> list[QTranslator]:
    """给 QApplication 装上 Qt 自己的翻译（右键菜单、文件对话框等就靠它）。

    返回装着翻译器的列表——**调用方必须持有引用**：``QTranslator`` 一旦被回收，
    翻译立刻失效（这类"装完没反应"的坑很常见）。
    """
    from PySide6.QtCore import QLibraryInfo, QTranslator

    lang = current()
    suffix = _QT_SUFFIX.get(lang, "zh_CN")
    base = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
    keep: list[QTranslator] = []
    # qtbase: 标准对话框与按钮；qtwebengine: **内置浏览器的右键菜单**就在这里面
    for name in ("qtbase", "qtwebengine", "qt"):
        tr_obj = QTranslator(app)
        if tr_obj.load(f"{name}_{suffix}", base):
            app.installTranslator(tr_obj)
            keep.append(tr_obj)
    return keep
