# systemd 用户单元

> ⚠️ 规范位置：https://github.com/xugulin/dsh-display-panel（`service/` 目录下，含 install-service.sh 一键安装）—— 这里保留作为开发副本。

## dsh-display-viewer.service —— DSH 测试显示器服务

**为什么必须独立成一个服务**：早先 viewer 与它拉起的 Xvfb 是从 shell 里启动的，
而 harness 是 systemd 服务 —— **重启 harness 时 systemd 会按 cgroup 连带杀掉同一
cgroup 里的进程**（`setsid` 挡不住这个）。用户实测过：重启 harness 后显示器服务没了，
面板就显示"显示器还没有打开"。放进独立单元后它与 harness 互不影响。

安装（用户级，不需要 root）：

```sh
mkdir -p ~/.config/systemd/user
cp tools/systemd/dsh-display-viewer.service ~/.config/systemd/user/
# 按实际情况改里面的路径（仓库位置、python 解释器）
systemctl --user daemon-reload
systemctl --user enable --now dsh-display-viewer.service
```

常用操作：

```sh
systemctl --user status  dsh-display-viewer     # 状态
systemctl --user restart dsh-display-viewer     # 重启（画面会自动恢复，页面有重连）
journalctl --user -u dsh-display-viewer -f      # 看日志（注入失败也会打在这里）
```

服务地址：`http://127.0.0.1:8099/`（根是会话索引）；每个会话一路
`/s/<sessionId>/`；在该会话显示上跑程序用
`DISPLAY=:<号> QT_QPA_PLATFORM=xcb 程序`（号可向 `/s/<sessionId>/display` 查询）。
