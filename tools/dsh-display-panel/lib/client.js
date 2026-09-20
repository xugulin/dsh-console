/**
 * dsh-display-panel — 浏览器半边：在「对话 / 轨迹 / 浏览器」那一排里加一个「显示器」标签。
 *
 * 构建标记：2026-09-20b（面板会把它显示出来，便于判断浏览器里加载的是哪一版 ——
 * 插件 JS 会被浏览器与宿主缓存，排查时"到底跑的哪一版"必须先确定）。
 *
 * 每个 harness 会话看**自己那一路**显示：/s/<sessionId>/ —— 互不可见、互不污染。
 */
window.__ModuleLoader__.load({
  id: 'dsh-display-panel',
  factory: (require) => {
    const React = require('react')
    const { useEffect, useState } = React
    const h = React.createElement

    const PANEL_ID = 'display-panel'
    const BUILD = '2026-09-20b'
    /** 显示器服务地址；每个会话在 /s/<sessionId>/ 下有自己的一路。 */
    const VIEWER = 'http://127.0.0.1:8099'
    /** 探测间隔：显示器是外部进程，随时可能起停。 */
    const POLL_MS = 3000

    const TEXT = {
      empty: '显示器还没有打开',
      emptyHint: '打开后这里就是我在虚拟显示上的实时画面（约 7~8 帧/秒，可点击、可输入）。'
        + '每个会话有自己独立的一台显示，别的会话看不到这里的画面。',
      open: '重新检测',
      checking: '正在检查显示器…',
      retryHint: '需要打开时告诉我一声（AI 会拉起这台显示），或自己执行：'
        + ' systemctl --user start dsh-display-viewer',
      noSession: '这里拿不到会话 id，无法确定这个面板对应哪台显示。',
    }

    /** 样式只注入一次。**背景保持 transparent**，底色就是面板自己的（与「对话」一致）。 */
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
      .ddp-build { font-size: 10.5px; opacity: .35; }
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

    function Display(props) {
      const sid = props && props.sessionId ? String(props.sessionId) : ''
      const base = sid ? VIEWER + '/s/' + encodeURIComponent(sid) + '/' : ''
      // 防缓存：显示器服务可能重启，显示号也会变；iframe 必须每次拿新页面
      const cacheBust = '?v=' + Date.now()
      const [state, setState] = useState('checking')   // checking | ready | empty
      const [nonce, setNonce] = useState(0)

      useEffect(() => {
        ensureStyle()
        try { console.log('[dsh-display-panel] build', BUILD, 'sid', sid) } catch (e) {}
      }, [sid])

      useEffect(() => {
        if (!base) { setState('empty'); return undefined }
        let alive = true
        const probe = async () => {
          try {
            const res = await fetch(base + cacheBust, { cache: 'no-store' })
            if (alive) setState(res.ok ? 'ready' : 'empty')
          } catch (error) {
            if (alive) setState('empty')
          }
        }
        void probe()
        const timer = setInterval(() => { void probe() }, POLL_MS)
        return () => { alive = false; clearInterval(timer) }
      }, [base, nonce])

      if (!sid) {
        return h('div', { className: 'ddp-root' },
          h('div', { className: 'ddp-empty' },
            h('div', { className: 'ddp-note' }, TEXT.noSession),
            h('div', { className: 'ddp-build' }, 'build ' + BUILD)))
      }
      if (state === 'ready') {
        return h('div', { className: 'ddp-root' },
          h('iframe', {
            key: nonce,
            className: 'ddp-frame',
            src: base + cacheBust,
            title: 'DSH 显示器 · ' + sid + ' · ' + BUILD,
          }))
      }
      if (state === 'checking') {
        return h('div', { className: 'ddp-root' },
          h('div', { className: 'ddp-empty' },
            h('div', { className: 'ddp-note' }, TEXT.checking),
            h('div', { className: 'ddp-build' }, 'build ' + BUILD)))
      }
      return h('div', { className: 'ddp-root' },
        h('div', { className: 'ddp-empty' },
          h('div', { className: 'ddp-title' }, TEXT.empty),
          h('div', { className: 'ddp-note' }, TEXT.emptyHint),
          h('button', {
            type: 'button', className: 'ddp-primary',
            onClick: () => { setState('checking'); setNonce((n) => n + 1) },
          }, TEXT.open),
          h('div', { className: 'ddp-hint' }, TEXT.retryHint),
          h('div', { className: 'ddp-build' }, 'build ' + BUILD)))
    }

    const apply = (ctx) => {
      try {
        // ⚠️ 必须先 slots.inject 等声明再 register；inject 里带上 sessionId —— 面板才知道
        // 该显示**哪个会话**的那台显示（写法同 dsh-browser-panel）。
        ctx.slots.inject('conversation.view', () =>
          ctx.slots.register(
            {
              name: 'conversation.view', id: PANEL_ID, order: 60, label: () => '显示器',
              inject: (sessionId) => ({ sessionId: typeof sessionId === 'string' ? sessionId : '' }),
            },
            (props) => h(Display, props),
          ),
        )
      } catch (error) {
        console.warn('[dsh-display-panel] slot registration failed:', error)
      }
    }

    return { apply, inject: ['slots'] }
  },
})
