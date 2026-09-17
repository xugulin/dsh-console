"""会话元数据与历史：给 TUI 的"切换会话/工作区"用。

有两件事 ACP 不提供，得自己从 DSH 的存储里读：

1. **标题和时间**。``session/list`` 每条只回 ``{sessionId, cwd}``——61 个 UUID 排在那儿
   没法选。好在 DSH 自己有一份投影缓存 ``~/.dsh/storages/session_projcache/sessions/``，
   每个会话一个**未压缩**的小 JSON，里面有 ``title`` / ``createdAt`` / ``cwd``。读它很便宜。

2. **历史**。ACP 的 ``session/resume`` 明确"**不回放旧更新**"，恢复完界面就是一片空白。
   要让它有意义，只能去读会话日志（``~/.dsh/sessions/<cwd 编码>/<会话>/session.v3.jsonl.zstd``）
   自己重建。日志很大（实测这份 3787 条事件、解压后 3 MB 出头），所以**只在切会话时读一次**，
   而且放在后台线程里。

日志里 ``user/message`` 的 ``source.kind`` 有好几种（``user`` / ``plugin`` / ``goal`` /
``agent-message`` / ``subagent-settled``）。只有 ``user`` 才是人真正敲进去的，
其余是插件和子代理注入的噪音——重建历史时只认 ``user``，否则翻出来全是系统通告。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

#: 所有会话的根目录。
SESSIONS_DIR = Path.home() / ".dsh" / "sessions"

#: DSH 的会话投影缓存（未压缩 JSON，含标题与创建时间）。
PROJCACHE_DIR = Path.home() / ".dsh" / "storages" / "session_projcache" / "sessions"

#: 重建历史时最多取多少条（多了没人看，还得解压大文件）。
HISTORY_LIMIT = 60


@dataclass(slots=True)
class SessionMeta:
    """一个会话的元数据。标题可能为空（有些会话没生成过标题）。"""

    session_id: str
    cwd: str = ""
    title: str = ""
    created_at: float = 0.0

    @property
    def short(self) -> str:
        return self.session_id[:8]

    def label(self) -> str:
        return self.title or f"（无标题）{self.short}"

    def when(self) -> str:
        """相对时间，给人看的。"""
        if not self.created_at:
            return ""
        delta = max(0.0, time.time() - self.created_at)
        if delta < 90:
            return "刚刚"
        if delta < 3600:
            return f"{int(delta // 60)} 分钟前"
        if delta < 86400:
            return f"{int(delta // 3600)} 小时前"
        if delta < 86400 * 7:
            return f"{int(delta // 86400)} 天前"
        return time.strftime("%m-%d", time.localtime(self.created_at))


def cached_meta() -> dict[str, SessionMeta]:
    """读投影缓存，返回 ``{session_id: SessionMeta}``。

    读不到就返回空字典——没标题顶多难看一点，不该让整页挂掉。
    """
    out: dict[str, SessionMeta] = {}
    try:
        files = list(PROJCACHE_DIR.glob("*.json"))
    except OSError:
        return out
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        record = data.get("record") or {}
        identity = record.get("identity") or {}
        title = ((record.get("rows") or {}).get("title") or {}).get("val")
        created = identity.get("createdAt") or 0
        out[path.stem] = SessionMeta(
            session_id=path.stem,
            cwd=str(identity.get("cwd") or ""),
            title=str(title or ""),
            created_at=float(created) / 1000.0 if created else 0.0,
        )
    return out


def merge(acp_sessions: list[dict]) -> list[SessionMeta]:
    """把 ACP 的 ``session/list`` 结果和本地缓存合起来，按时间新到旧排。

    ACP 是"哪些能恢复"的权威；缓存负责补上标题和时间。缓存里没有的会话照样列出来
    （只是没标题），免得"能恢复却看不到"。
    """
    meta = cached_meta()
    out: list[SessionMeta] = []
    for s in acp_sessions:
        sid = str(s.get("sessionId") or "")
        if not sid:
            continue
        hit = meta.get(sid)
        if hit is None:
            out.append(SessionMeta(session_id=sid, cwd=str(s.get("cwd") or "")))
        else:
            hit.cwd = hit.cwd or str(s.get("cwd") or "")
            out.append(hit)
    # 有时间的排前面（新到旧），没时间的按 id 排后面，保证顺序稳定
    out.sort(key=lambda m: (m.created_at, m.session_id), reverse=True)
    return out


def workspaces(sessions: list[SessionMeta]) -> list[tuple[str, int]]:
    """从会话里归纳出工作区：``[(cwd, 会话数)]``，按会话数从多到少。"""
    counts: dict[str, int] = {}
    for s in sessions:
        if s.cwd:
            counts[s.cwd] = counts.get(s.cwd, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def find_log(session_id: str) -> Path | None:
    """找到某个会话的日志文件。

    目录名有两种：``<sid>`` 和 ``session-<sid>``（实测都存在），所以两个都试。
    """
    if not session_id:
        return None
    for pattern in (f"*/{session_id}/session.v3.jsonl.zstd",
                    f"*/session-{session_id}/session.v3.jsonl.zstd"):
        try:
            for hit in SESSIONS_DIR.glob(pattern):
                return hit
        except OSError:
            continue
    return None


def _text_of(content: list) -> str:
    return "".join(
        str(c.get("text") or "") for c in content if isinstance(c, dict) and c.get("type") == "text"
    ).strip()


def _args_brief(raw: str, limit: int = 72) -> str:
    """把工具调用的参数压成一行摘要。

    优先挑"人话"字段（说明 / 命令 / 路径）。**绝不回落到把整个参数 dump 出来**——
    实测 ``write`` 的 ``content`` 会把整篇文件内容糊在历史里，一行几十 K。
    """
    try:
        args = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        return str(raw or "").replace("\n", " ")[:limit]
    if not isinstance(args, dict):
        return str(args).replace("\n", " ")[:limit]
    for key in ("description", "command", "file_path", "path", "pattern", "query", "url"):
        value = args.get(key)
        if value:
            return str(value).replace("\n", " ")[:limit]
    # 没有可读字段就只报键名和长度，别把内容倒出来
    bits = []
    for k, v in list(args.items())[:3]:
        if isinstance(v, str) and len(v) > 24:
            bits.append(f"{k}({len(v)} 字符)")
        else:
            bits.append(f"{k}={v}")
    return ", ".join(bits)[:limit]


def history(session_id: str, limit: int = HISTORY_LIMIT) -> tuple[str, list[tuple[str, str]]]:
    """重建一个会话的历史，返回 ``(标题, [(kind, text)])``。

    ``kind`` 是 TUI 的条目类型：``user`` / ``agent`` / ``tool``。
    只取最后 ``limit`` 条——翻几十轮之前的对话没有意义，解压和排版却是真花钱。
    """
    path = find_log(session_id)
    if path is None:
        return "", []
    try:
        from compression import zstd

        raw = zstd.decompress(path.read_bytes()).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - 日志损坏/权限问题都不该让界面炸
        return "", []

    title = ""
    entries: list[tuple[str, str]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        data = event.get("data") or {}
        if kind == "session/title":
            title = str(data.get("title") or "") or title
        elif kind == "user/message":
            if (data.get("source") or {}).get("kind") != "user":
                continue          # 插件/子代理注入的通告，不算对话
            text = _text_of(data.get("content") or [])
            if text:
                entries.append(("user", text))
        elif kind == "assistant/message":
            for item in (data.get("message") or {}).get("content") or []:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text":
                    text = str(item.get("text") or "").strip()
                    if text:
                        entries.append(("agent", text))
                elif item.get("type") == "tool-call":
                    entries.append(("tool", f"{item.get('name', 'tool')}  "
                                            f"{_args_brief(item.get('arguments') or '')}"))
    return title, entries[-limit:]
