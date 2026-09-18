# dsh-display-panel

在 DSH Web UI 的「对话 / 轨迹 / 浏览器」那一排里增加一个 **显示器** 标签，
用 iframe 显示一个外部画面（默认 `http://127.0.0.1:8099/`）。

用途：DSH Console 在 headless Wayland 上做 GUI 相关的工作时，把画面固定成一个标签页，
人随时切过去看，而不是占用「浏览器」标签。

## 安装

放到 profile 的 `node_modules/` 下，并在 `cordis.patch.yml` 里插入一行花名册条目。
卸载：删掉目录与那一行，然后刷新页面。
