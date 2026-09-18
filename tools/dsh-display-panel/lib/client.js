/**
 * dsh-display-panel — 浏览器半边：在「对话 / 轨迹 / 浏览器」那一排里加一个「显示器」标签。
 *
 * 纯 JavaScript（和 dsh-browser-panel 一样）：模块加载器工厂不依赖构建步骤，
 * 除了宿主提供的 React 之外不依赖任何 npm 包。
 */
window.__ModuleLoader__.load({
  id: 'dsh-display-panel',
  factory: (require) => {
    const React = require('react')
    const h = React.createElement

    const PANEL_ID = 'display-panel'
    const URL = 'http://127.0.0.1:8099/'

    const Display = () =>
      h('div', { style: { width: '100%', height: '100%', background: '#0b0b0c' } },
        h('iframe', {
          src: URL,
          title: 'DSH 测试显示器',
          style: { width: '100%', height: '100%', border: '0', display: 'block' },
        }))

    const apply = (ctx) => {
      try {
        // ⚠️ 必须先 `slots.inject(slot, …)` **等声明**，再在里面 register。
        // 直接 register 会被拒：Error: slot "conversation.view" is not declared
        // (a parent entry's children table must declare it)。
        // 这是 dsh-browser-panel 的官方写法（它的注释：slots.inject waits for the
        // declaration instead）。
        ctx.slots.inject('conversation.view', () =>
          ctx.slots.register(
            { name: 'conversation.view', id: PANEL_ID, order: 60, label: () => '显示器' },
            () => h(Display),
          ),
        )
      } catch (error) {
        console.warn('[dsh-display-panel] slot registration failed:', error)
      }
    }

    return { apply, inject: ['slots'] }
  },
})
