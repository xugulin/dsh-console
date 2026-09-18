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
    const { useEffect, useState } = React
    const h = React.createElement

    const PANEL_ID = 'display-panel'
    /** 显示器地址：DSH Console 自建的 MJPEG 虚拟显示服务。 */
    const VIEWER = 'http://127.0.0.1:8099/'
    /** 探测间隔：显示器是外部进程，随时可能起停。 */
    const POLL_MS = 3000

    /** 文案（照 dsh-browser-panel 的空态结构写）。 */
    const TEXT = {
      empty: '显示器还没有打开',
      emptyHint: '打开后这里就是我在虚拟显示上的实时画面（只读，约 1.5 帧/秒）。'
        + '它跑在 headless Wayland 上，和你的桌面互不干扰；我在这上面做的 GUI 测试你都能直接看到。',
      open: '重新检测',
      checking: '正在检查显示器…',
      retryHint: '需要打开时告诉我一声（AI 会拉起虚拟显示服务），或自己执行：'
        + ' DSH_VIEW_PORT=8099 python3 /tmp/dshview/view.py',
    }

    /** 样式只注入一次。**背景保持 transparent**，这样底色就是面板自己的 ——
     *  也就是和「对话」页同一套底色（用户明确要求一致）。 */
    const CSS = `
      .ddp-root { width: 100%; height: 100%; display: flex; background: transparent; }
      .ddp-frame { width: 100%; height: 100%; border: 0; display: block; background: #0b0b0c; }
      .ddp-empty {
        flex: 1; display: flex; flex-direction: column; align-items: center;
        justify-content: center; gap: 10px; padding: 24px; text-align: center;
        background: transparent; color: inherit;
      }
      .ddp-title { font-size: 15px; font-weight: 600; }
      .ddp-note { font-size: 12.5px; line-height: 1.75; opacity: .72; max-width: 560px; }
      .ddp-hint { font-size: 11.5px; opacity: .5; max-width: 620px; word-break: break-all; }
      .ddp-primary {
        margin-top: 2px; padding: 7px 16px; font: inherit; font-size: 12.5px;
        border-radius: 8px; cursor: pointer; color: inherit;
        border: 1px solid var(--dsh-border, rgba(255,255,255,.18));
        background: var(--dsh-surface-alt, rgba(255,255,255,.06));
      }
      .ddp-primary:hover { border-color: var(--dsh-accent, #4c8dff); }
    `
    function ensureStyle() {
      if (document.getElementById('ddp-style')) return
      const el = document.createElement('style')
      el.id = 'ddp-style'
      el.textContent = CSS
      document.head.appendChild(el)
    }

    function Display() {
      const [state, setState] = useState('checking')   // checking | ready | empty
      const [nonce, setNonce] = useState(0)

      useEffect(() => { ensureStyle() }, [])

      useEffect(() => {
        let alive = true
        const probe = async () => {
          try {
            const res = await fetch(VIEWER, { cache: 'no-store' })
            if (alive) setState(res.ok ? 'ready' : 'empty')
          } catch (error) {
            if (alive) setState('empty')
          }
        }
        void probe()
        const timer = setInterval(() => { void probe() }, POLL_MS)
        return () => { alive = false; clearInterval(timer) }
      }, [nonce])

      if (state === 'ready') {
        return h('div', { className: 'ddp-root' },
          h('iframe', {
            key: nonce,
            className: 'ddp-frame',
            src: VIEWER,
            title: 'DSH 显示器',
          }))
      }
      if (state === 'checking') {
        return h('div', { className: 'ddp-root' },
          h('div', { className: 'ddp-empty' }, h('div', { className: 'ddp-note' }, TEXT.checking)))
      }
      return h('div', { className: 'ddp-root' },
        h('div', { className: 'ddp-empty' },
          h('div', { className: 'ddp-title' }, TEXT.empty),
          h('div', { className: 'ddp-note' }, TEXT.emptyHint),
          h('button', {
            type: 'button', className: 'ddp-primary',
            onClick: () => { setState('checking'); setNonce((n) => n + 1) },
          }, TEXT.open),
          h('div', { className: 'ddp-hint' }, TEXT.retryHint)))
    }

    const apply = (ctx) => {
      try {
        // ⚠️ 必须先 `slots.inject(slot, …)` **等声明**，再在里面 register ——
        // 直接 register 会被拒（slot ... is not declared）。写法同 dsh-browser-panel。
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
