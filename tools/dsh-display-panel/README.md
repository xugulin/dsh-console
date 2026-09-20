# 已独立发布，这里只留指针

「显示器」面板插件（含它依赖的显示器服务）已经**独立成一个仓库**维护：

## → https://github.com/xugulin/dsh-display-panel

```sh
git clone https://github.com/xugulin/dsh-display-panel
cd dsh-display-panel && bash scripts/install-service.sh
```

**为什么从这里搬走**：早先它的源码放在控制台仓库的 `tools/` 下，同时又要作为
DSH 插件安装到 `~/.dsh/profiles/web/`，两边各一份 → 改一边忘另一边就会不同步。
独立成仓库后：插件、显示器服务、systemd 单元、安装脚本、踩坑记录都在一处。

## 本仓库里仍保留的相关文件

| 文件 | 说明 |
|---|---|
| `tools/dsh-display-viewer.py` | 显示器服务的**开发副本**（规范位置在插件仓库 `service/`） |
| `tools/dsh-display-selftest.py` | 显示器功能自测（12 项） |
| `tools/systemd/` | systemd 单元模板与用法（规范位置在插件仓库 `service/`） |
| `tools/dsh-display-start.sh`、`tools/dsh-display-reset.sh` | 早期运维脚本（Wayland 底座时代，现由 systemd 单元取代） |

> ⚠️ 改动显示器/插件相关代码时，**以插件仓库为准**，改完再同步这里，别只改一边。
