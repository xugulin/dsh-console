# Windows：显示器服务启动脚本

这 4 个脚本由**社区用户**编写（Windows 实测通过），随便携包一起发出：

| 文件 | 用途 |
|---|---|
| `启动显示器服务.bat` | 启动服务；已在跑就不重复起（两个进程抢 8099 只会互相报错） |
| `启动显示器服务-Silent.vbs` | 无窗口版，供自启使用 |
| `停止显示器服务.bat` | **按 8099 端口**精确停止（不做 `pkill -f` 那种模糊匹配） |
| `开机自启用-DSH显示器服务.vbs` | 放进「启动」文件夹即可登录自启 |

它们按便携包的目录结构写死了两个路径：`runtime\win\python\pythonw.exe` 与
`app\tools\dsh-display-viewer.py` —— 与 `build_bundle.py` 的布局一致，
所以**照原样打包即可**，不需要改路径。

**规范位置**：<https://github.com/xugulin/dsh-display-panel/tree/main/service/windows>
（这里是随包发出的副本；改动请以插件仓库为准，再由 `write_launchers` 复制过来。）
