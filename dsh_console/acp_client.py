"""ACP v1 客户端：把 DSH 的 ``acp`` profile 当成 stdio 服务端来驱动。

DSH 自带的前端只有 web（浏览器）；TUI 和桌面 GUI 得自己写。与其去猜 web 的表层协议，
不如用 harness 明确为「外部客户端」准备的那条路——``dsh --profile acp``：

  * 它实现的是**标准 ACP v1**（Zed 的 agent-client-protocol），不是私有协议；
  * 传输是 **换行分隔的 JSON-RPC 2.0**（一行一条消息，见 ACP SDK 的 line-buffer）；
  * stdout 只走协议流量，日志走 stderr，所以解析 stdout 不会被日志污染。

实测过的消息形状（本机 dsh 0.1.5-rc.1）::

    → {"jsonrpc":"2.0","id":1,"method":"initialize","params":{
         "protocolVersion":1,
         "clientCapabilities":{"fs":{"readTextFile":false,"writeTextFile":false}}}}
    ← {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":1,"agentInfo":{...},...}}

    → {"jsonrpc":"2.0","id":2,"method":"session/new",
       "params":{"cwd":"/abs/path","mcpServers":[]}}
    ← {"jsonrpc":"2.0","id":2,"result":{"sessionId":"...","configOptions":[...]}}

    → {"jsonrpc":"2.0","id":3,"method":"session/prompt",
       "params":{"sessionId":"...","prompt":[{"type":"text","text":"..."}]}}
    ← {"jsonrpc":"2.0","id":3,"result":{"stopReason":"end_turn"}}

    # 期间服务端推 session/update 通知（无 id）：
    ← {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"...",
         "update":{"sessionUpdate":"agent_thought_chunk","messageId":"...",
                   "content":{"type":"text","text":"..."}}}}
    ← {"sessionUpdate":"agent_message_chunk",   # 正文
       "sessionUpdate":"tool_call",             # {toolCallId,title,kind,status,rawInput}
       "sessionUpdate":"tool_call_update",      # {toolCallId,status,content}
       "sessionUpdate":"usage_update"}          # {used,size}

服务端还可能反向发起 ``session/request_permission``（带 id 的请求），必须应答，
否则 agent 会一直等下去——见 :meth:`AcpClient._handle_server_request`。

本模块只用标准库：subprocess + json + threading。
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import portable

#: profile 名。ACP 服务器由 ``dsh --profile acp`` 拉起。
PROFILE = "acp"

#: 单条请求的默认超时（秒）。``session/prompt`` 可能跑很久，调用方自己传超时。
DEFAULT_TIMEOUT = 60.0

#: 进程启动 + initialize 握手的时间上限。首次启动 profile 要 pnpm 准备依赖，给足。
START_TIMEOUT = 180.0


class AcpError(RuntimeError):
    """ACP 层的错误：进程起不来、协议出错、请求超时或被服务端拒绝。"""


class AcpProcessError(AcpError):
    """服务端进程异常退出。"""


@dataclass
class ToolCall:
    """一条工具调用的累积状态（由 tool_call / tool_call_update 拼出来）。"""

    tool_call_id: str
    title: str = ""
    kind: str = ""
    status: str = ""
    raw_input: dict = field(default_factory=dict)
    output: str = ""

    @property
    def is_done(self) -> bool:
        return self.status in ("completed", "failed")


def _flatten_content(content: Any) -> str:
    """把 ACP 的 content 结构拍平成纯文本。

    ``tool_call_update`` 的 content 是 ``[{"type":"content","content":{"type":"text",
    "text":...}}]`` 这种嵌套；直接 ``str()`` 会把结构打给用户看，太丑。
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        kind = content.get("type")
        if kind == "text":
            return str(content.get("text") or "")
        if "content" in content:
            return _flatten_content(content["content"])
        if "text" in content:
            return str(content["text"])
        return ""
    if isinstance(content, list):
        return "".join(_flatten_content(c) for c in content)
    return str(content)


class AcpClient:
    """一个 ACP 会话通道。

    典型用法::

        c = AcpClient(on_update=print)
        c.start()                      # 拉起进程 + initialize
        sid = c.new_session("/path")   # 新建会话
        c.prompt(sid, "你好")          # 阻塞直到这一轮结束
        c.stop()

    回调都在**读取线程**里执行，界面层要把它们转投到自己的事件循环（GUI 用信号，
    TUI 用队列），不要在里面直接改界面状态。
    """

    def __init__(
        self,
        dsh_bin: str | None = None,
        profile: str = PROFILE,
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: dict[str, str] | None = None,
        on_update: Callable[[dict], None] | None = None,
        on_permission: Callable[[dict], str | None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
        on_exit: Callable[[int], None] | None = None,
        permission_policy: str = "allow",
        popen_extra: dict[str, Any] | None = None,
    ) -> None:
        # 便携包优先：包里的 dsh 既不在 PATH 上，也不是 npm 的全局前缀
        self.dsh_bin = dsh_bin or portable.dsh_bin() or shutil.which("dsh") or "dsh"
        self.profile = profile
        self.cwd = str(cwd) if cwd else os.getcwd()
        self._env = env
        self._popen_extra = dict(popen_extra or {})
        self.on_update = on_update
        self.on_permission = on_permission
        self.on_stderr = on_stderr
        self.on_exit = on_exit
        #: 没接 on_permission 时怎么办：allow = 选第一个允许项，deny = 一律拒绝。
        self.permission_policy = permission_policy

        self.proc: subprocess.Popen[str] | None = None
        self.agent_info: dict = {}
        self.capabilities: dict = {}
        self._next_id = 0
        self._lock = threading.Lock()
        self._pending: dict[int, queue.Queue] = {}
        self._reader: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._closed = threading.Event()
        self.stderr_tail: list[str] = []
        #: 所有 session/update 的原始记录（调试与回放用）
        self.updates: list[dict] = []
        self.tool_calls: dict[str, ToolCall] = {}

    # ------------------------------------------------------------ 生命周期
    def start(self, timeout: float = START_TIMEOUT) -> dict:
        """拉起服务端进程并完成 initialize 握手。"""
        if self.proc is not None:
            raise AcpError("已经启动过了")
        env = dict(os.environ if self._env is None else self._env)
        # 强制无缓冲，否则 stdout 的管道缓冲会把消息憋在子进程里
        env.setdefault("PYTHONUNBUFFERED", "1")
        # Windows 上 ``.cmd``/``.bat`` **不能被 CreateProcess 直接执行**（要经过 cmd.exe），
        # 直接 Popen 会报"不是有效的 Win32 应用程序"。npm 装的 dsh 在 Windows 上正好就是
        # 一个 dsh.cmd，便携包里也是，所以这条分支是必须的。
        base = [self.dsh_bin]
        if os.name == "nt" and str(self.dsh_bin).lower().endswith((".cmd", ".bat")):
            base = ["cmd", "/c", str(self.dsh_bin)]
        try:
            self.proc = subprocess.Popen(
                [*base, "--profile", self.profile],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=self.cwd,
                env=env,
                # 必须让 harness 待在**自己的会话**里。
                #
                # 默认情况下子进程和前端同属一个前台进程组，于是终端里的 Ctrl+C
                # 会被内核发给整组——想"取消这一轮"结果把 harness 一起 SIGINT 打死
                # （实测退出码 -2）。TUI 前端虽然自己关掉了 ISIG 兜住这一点，但那是
                # 前端各自的补丁；把子进程隔离出去才是根上的修法，GUI 与将来任何
                # 前端都自动受益。
                #
                # 代价是前端被 SIGKILL 时子进程会变孤儿——但 stop() 会关掉 stdin，
                # stdio 服务端读到 EOF 自己就会退出，不会长期残留。
                start_new_session=True,
                **self._popen_extra,
            )
        except OSError as exc:
            raise AcpProcessError(f"拉不起 {self.dsh_bin}：{exc}") from exc

        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()

        result = self.request(
            "initialize",
            {
                "protocolVersion": 1,
                # 客户端不提供文件系统/终端能力：agent 会退回自己的工具
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False,
                },
            },
            timeout=timeout,
        )
        self.agent_info = result.get("agentInfo") or {}
        self.capabilities = result.get("agentCapabilities") or {}
        return result

    def stop(self, timeout: float = 10.0) -> None:
        """关闭通道。先关 stdin 让服务端自己收尾，超时再 terminate。"""
        self._closed.set()
        proc = self.proc
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.proc = None

    def __enter__(self) -> "AcpClient":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # ------------------------------------------------------------ 收发
    def _read_stdout(self) -> None:
        proc = self.proc
        assert proc is not None and proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                # 协议外的东西（某些插件会往 stdout 打字）——记到 stderr 尾巴里
                self._note_stderr(f"[stdout 非 JSON] {line[:400]}")
                continue
            try:
                self._dispatch(msg)
            except Exception as exc:  # noqa: BLE001 - 读取线程不能死
                self._note_stderr(f"[分发失败] {type(exc).__name__}: {exc}")
        code = proc.poll()
        if self.on_exit is not None:
            try:
                self.on_exit(code if code is not None else -1)
            except Exception:
                pass

    def _read_stderr(self) -> None:
        proc = self.proc
        assert proc is not None and proc.stderr is not None
        for line in proc.stderr:
            self._note_stderr(line.rstrip())

    def _note_stderr(self, line: str) -> None:
        self.stderr_tail.append(line)
        del self.stderr_tail[:-200]          # 只留尾巴，别把内存吃光
        if self.on_stderr is not None:
            try:
                self.on_stderr(line)
            except Exception:
                pass

    def _dispatch(self, msg: dict) -> None:
        if "id" in msg and ("result" in msg or "error" in msg):
            with self._lock:
                waiter = self._pending.pop(msg["id"], None)
            if waiter is not None:
                waiter.put(msg)
            return
        if "method" in msg and "id" in msg:
            # 服务端反向请求：必须应答
            threading.Thread(
                target=self._handle_server_request, args=(msg,), daemon=True
            ).start()
            return
        if msg.get("method") == "session/update":
            update = (msg.get("params") or {}).get("update") or {}
            self._track_tool_call(update)
            self.updates.append(update)
            if self.on_update is not None:
                try:
                    self.on_update(update)
                except Exception as exc:  # noqa: BLE001
                    self._note_stderr(f"[on_update 抛错] {type(exc).__name__}: {exc}")
            return
        # 其它通知（例如 $/progress）先记下来
        self._note_stderr(f"[未处理的通知] {json.dumps(msg, ensure_ascii=False)[:200]}")

    def _track_tool_call(self, update: dict) -> None:
        """把 tool_call / tool_call_update 拼成一条完整记录，供界面直接渲染。"""
        kind = update.get("sessionUpdate")
        tcid = update.get("toolCallId")
        if not tcid:
            return
        if kind == "tool_call":
            self.tool_calls[tcid] = ToolCall(
                tool_call_id=tcid,
                title=str(update.get("title") or ""),
                kind=str(update.get("kind") or ""),
                status=str(update.get("status") or ""),
                raw_input=update.get("rawInput") or {},
            )
        elif kind == "tool_call_update":
            tc = self.tool_calls.get(tcid)
            if tc is None:
                tc = self.tool_calls[tcid] = ToolCall(tool_call_id=tcid)
            if update.get("status"):
                tc.status = str(update["status"])
            if update.get("title"):
                tc.title = str(update["title"])
            if update.get("rawInput"):
                tc.raw_input = update["rawInput"]
            text = _flatten_content(update.get("content"))
            if text:
                tc.output = (tc.output + text) if tc.output else text

    def _handle_server_request(self, msg: dict) -> None:
        """应答服务端反向请求。

        目前只会遇到 ``session/request_permission``：agent 要跑一个需要授权的工具，
        列出一组选项让我们挑一个。**不应答 agent 就会一直卡在那里**，所以这里凡是
        不认识的请求也必须回一个错误，不能装死。
        """
        method = msg.get("method")
        rid = msg.get("id")
        params = msg.get("params") or {}
        if method == "session/request_permission":
            option_id = None
            if self.on_permission is not None:
                try:
                    option_id = self.on_permission(params)
                except Exception as exc:  # noqa: BLE001
                    self._note_stderr(f"[on_permission 抛错] {type(exc).__name__}: {exc}")
                    option_id = None
            if option_id is None:
                option_id = self._pick_permission(params)
            if option_id is None:
                self._reply(rid, {"outcome": {"outcome": "cancelled"}})
            else:
                self._reply(rid, {"outcome": {"outcome": "selected", "optionId": option_id}})
            return
        self._reply_error(rid, -32601, f"客户端不支持 {method}")

    def _pick_permission(self, params: dict) -> str | None:
        """默认策略：allow 挑第一个允许项，deny 一个都不挑（= 取消）。"""
        if self.permission_policy == "deny":
            return None
        for opt in params.get("options") or []:
            kind = str(opt.get("kind") or "")
            if kind.startswith("allow"):
                return str(opt.get("optionId"))
        options = params.get("options") or []
        return str(options[0].get("optionId")) if options else None

    def _reply(self, rid: Any, result: Any) -> None:
        self._send({"jsonrpc": "2.0", "id": rid, "result": result})

    def _reply_error(self, rid: Any, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})

    def _send(self, obj: dict) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None or proc.stdin.closed:
            raise AcpProcessError("服务端进程已经不在了")
        try:
            proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise AcpProcessError(f"写入失败：{exc}") from exc

    def request(self, method: str, params: dict, timeout: float | None = DEFAULT_TIMEOUT) -> Any:
        """发一条请求并等结果（同步）。"""
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            waiter: queue.Queue = queue.Queue()
            self._pending[rid] = waiter
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            msg = waiter.get(timeout=timeout) if timeout else waiter.get()
        except queue.Empty:
            with self._lock:
                self._pending.pop(rid, None)
            raise AcpError(f"{method} 超时（{timeout}s）") from None
        if "error" in msg:
            err = msg["error"] or {}
            raise AcpError(f"{method} 失败：{err.get('message') or err}")
        return msg.get("result") or {}

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    # ------------------------------------------------------------ 会话操作
    def new_session(self, cwd: str | os.PathLike[str] | None = None, **kw: Any) -> str:
        """新建会话，返回 sessionId。"""
        params = {"cwd": str(cwd or self.cwd), "mcpServers": []}
        params.update(kw)
        return self.request("session/new", params)["sessionId"]

    def load_session(self, session_id: str, cwd: str | os.PathLike[str] | None = None) -> dict:
        """恢复一个已持久化的会话（ACP 的 ``session/resume``）。"""
        return self.request(
            "session/resume", {"sessionId": session_id, "cwd": str(cwd or self.cwd), "mcpServers": []}
        )

    def list_sessions(self, cwd: str | os.PathLike[str] | None = None) -> list[dict]:
        params: dict[str, Any] = {}
        if cwd:
            params["cwd"] = str(cwd)
        out: list[dict] = []
        cursor = None
        while True:
            page = self.request("session/list", {**params, **({"cursor": cursor} if cursor else {})})
            out.extend(page.get("sessions") or [])
            cursor = page.get("nextCursor")
            if not cursor:
                return out

    def close_session(self, session_id: str) -> None:
        try:
            self.request("session/close", {"sessionId": session_id})
        except AcpError:
            pass  # 关闭失败不值得往界面上抛

    def set_config_option(self, session_id: str, option_id: str, value: str) -> dict:
        return self.request(
            "session/set_config_option",
            {"sessionId": session_id, "configId": option_id, "value": value},
        )

    def prompt(
        self,
        session_id: str,
        text: str,
        timeout: float | None = None,
        on_done: Callable[[str, Exception | None], None] | None = None,
    ) -> str:
        """发一轮提示词，阻塞到本轮结束，返回 stopReason。

        想不阻塞就用 :meth:`prompt_async`。
        """
        result = self.request(
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]},
            timeout=timeout,
        )
        return str(result.get("stopReason") or "")

    def prompt_async(
        self,
        session_id: str,
        text: str,
        on_done: Callable[[str, Exception | None], None] | None = None,
        timeout: float | None = None,
    ) -> threading.Thread:
        """后台线程发一轮提示词；``on_done(stop_reason, error)`` 在同一线程里回调。"""

        def run() -> None:
            try:
                reason = self.prompt(session_id, text, timeout=timeout)
                err: Exception | None = None
            except Exception as exc:  # noqa: BLE001
                reason, err = "", exc
            if on_done is not None:
                on_done(reason, err)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t

    def cancel(self, session_id: str) -> None:
        """取消当前提示词。用 notify，不等结果。"""
        try:
            self.notify("session/cancel", {"sessionId": session_id})
        except AcpError:
            pass

    # ------------------------------------------------------------ 便利方法
    def wait_ready(self, timeout: float = START_TIMEOUT) -> "AcpClient":
        self.start(timeout=timeout)
        return self

    def stderr_text(self, limit: int = 20) -> str:
        return "\n".join(self.stderr_tail[-limit:])


def find_dsh() -> str | None:
    """找一个可用的 dsh 可执行文件。"""
    return shutil.which("dsh")


def default_workspace() -> Path:
    """ACP 会话的默认工作目录：控制台自己的项目根。

    直接用 ``$HOME`` 会让 agent 在一个空荡荡的家里乱转，用项目根更实际。
    """
    return Path(__file__).resolve().parent.parent
