/**
 * dsh-display-panel — 宿主半边（占位）。
 *
 * 这个面板不需要宿主侧能力：它只是把一个外部显示器（例如 DSH 控制台在 headless
 * Wayland 上跑的测试画面）用 iframe 嵌进 Web UI。宿主半边存在只是为了让花名册能加载
 * 这个插件、从而把客户端半边分发给浏览器。
 */
'use strict'

/** 面板显示哪个地址；可用 DSH_DISPLAY_PANEL_URL 覆盖。 */
const DEFAULT_URL = 'http://127.0.0.1:8099/'

module.exports = {
  name: 'dsh-display-panel',
  apply() {
    /* 无宿主侧行为 */
  },
}

module.exports.DEFAULT_URL = DEFAULT_URL
