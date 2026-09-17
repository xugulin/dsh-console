"""已安装技能：发现、读取、启用/停用。

技能布局（与 dsh-skill-hub 的 provider 完全一致，见其 ``src/provider.ts``）：

======================  ==========================  ========
来源 key                 根目录                        可写
======================  ==========================  ========
``user-dsh``             ``~/.dsh/skills``             ✅
``user-agents``          ``~/.agents/skills``          ✅
``project-dsh``          ``<项目>/.dsh/skills``         ❌ 只读
``project-agents``       ``<项目>/.agents/skills``      ❌ 只读
======================  ==========================  ========

一个技能要么是**目录**（内含 ``SKILL.md``），要么是**扁平文件**（``<name>.md``）。
``SKILL.md`` 用 YAML frontmatter 声明 ``name`` / ``description`` / ``sets``。

**停用机制**：把发现文件重命名加 ``.disabled`` 后缀（``SKILL.md`` →
``SKILL.md.disabled``），**不删除任何内容**；启用就是改回来。控制台走的是
同一套约定，并把变更同步进技能中枢的 sidecar（``~/.dsh/dsh-skill-hub.json``
的 ``disabled`` 数组），这样 DSH 界面里的技能面板与控制台看到的状态一致。

调用次数与最近使用来自 sidecar 的 ``skillStats.lastTotals``（由中枢的后台扫描维护）。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import sources

DSH_HOME = sources.DSH_HOME
SKILL_HUB_STATE = sources.SKILL_HUB_STATE

DISABLED_SUFFIX = ".disabled"

#: 构建发现根（key, 标签, 根路径, 是否可写, 排序权重）
ROOT_SPECS = (
    ("project-dsh", "项目 · .dsh", 0, False),
    ("project-agents", "项目 · .agents", 1, False),
    ("user-dsh", "用户 · ~/.dsh", 2, True),
    ("user-agents", "用户 · ~/.agents", 3, True),
)


@dataclass(slots=True)
class Skill:
    """一个已发现（或已停用）的技能。"""

    name: str
    description: str
    path: Path            # 发现文件路径（停用时是 *.disabled）
    root_key: str
    root_label: str
    root_path: Path
    sets: list[str] = field(default_factory=list)
    enabled: bool = True
    writable: bool = False
    size: int = 0
    mtime: float = 0.0
    calls: int = 0
    last_used: float = 0.0

    @property
    def kind(self) -> str:
        return "目录" if self.path.name == "SKILL.md" or self.path.name == "SKILL.md.disabled" else "单文件"

    @property
    def source_kind(self) -> str:
        return "个人" if self.name.startswith("personal-") else "本地"

    @property
    def size_text(self) -> str:
        n = self.size
        if n >= 1024 * 1024:
            return f"{n / 1048576:.1f} MB"
        if n >= 1024:
            return f"{n / 1024:.1f} KB"
        return f"{n} B"

    @property
    def last_used_text(self) -> str:
        if not self.last_used:
            return "从未"
        secs = max(0, time.time() - self.last_used / 1000 if self.last_used > 1e11 else 0)
        if self.last_used > 1e11:  # epoch ms
            import datetime

            dt = datetime.datetime.fromtimestamp(self.last_used / 1000)
            delta = (datetime.datetime.now() - dt).total_seconds()
        else:
            delta = secs
        if delta < 3600:
            return f"{int(delta / 60)} 分钟前"
        if delta < 86400:
            return f"{int(delta / 3600)} 小时前"
        return f"{int(delta / 86400)} 天前"


_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 SKILL.md 的 YAML frontmatter。

    刻意不引入 PyYAML：这些文件只用标量、行内数组和简单的引号，
    手写解析足够，还能省一个依赖。
    """
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    meta: dict = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            items = [v.strip().strip("'\"") for v in value[1:-1].split(",")]
            meta[key] = [v for v in items if v]
        else:
            meta[key] = value.strip("'\"")
    return meta, text[m.end():]


def _project_root(cwd: Path | None = None) -> Path | None:
    """向上找一个"项目根"（含 ``.dsh`` 或 ``.git`` 的目录），找不到就退回 cwd。

    标记与技能中枢的 ``findProjectRoot`` 保持一致（只有 ``.dsh`` / ``.git``，
    **不含** ``.agents``）。

    ⚠️ 关键修正：**必须排除 ``$HOME``**。因为 ``~/.dsh`` 就是 DSH 主目录，
    从任何家目录下的路径往上走都会命中它，于是 ``<项目>/.dsh/skills`` 会解析成
    ``~/.dsh/skills``——和 user-dsh 根**是同一个目录**，同一个技能被列出两遍
    （实测确实如此）。这里显式跳过家目录及其祖先，再做一次路径去重。
    """
    home = Path.home().resolve()
    start = Path(cwd or os.getcwd()).resolve()
    current = start
    for _ in range(32):
        if current != home and current != current.parent:
            if any((current / marker).exists() for marker in (".dsh", ".git")):
                return current
        elif current == current.parent:
            break
        current = current.parent
    return start


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _display_name(meta: dict, path: Path) -> str:
    """技能名：优先 frontmatter 的 name，否则按文件/目录名推断。"""
    declared = str(meta.get("name") or "").strip()
    if declared:
        return declared
    if path.name.startswith("SKILL.md"):        # 目录式：用所在目录名
        return path.parent.name
    # 扁平式：foo.md / foo.md.disabled
    stem = path.name
    if stem.endswith(DISABLED_SUFFIX):
        stem = stem[: -len(DISABLED_SUFFIX)]
    if stem.endswith(".md"):
        stem = stem[:-3]
    return stem


def _read_skill_file(path: Path, root_key: str, root_label: str, root_path: Path,
                     writable: bool) -> Skill | None:
    enabled = not path.name.endswith(DISABLED_SUFFIX)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        stat = path.stat()
    except OSError:
        return None
    meta, _body = parse_frontmatter(text)
    sets = meta.get("sets") or []
    if isinstance(sets, str):
        sets = [sets]
    return Skill(
        name=_display_name(meta, path),
        description=str(meta.get("description") or ""),
        path=path,
        root_key=root_key,
        root_label=root_label,
        root_path=root_path,
        sets=[str(s) for s in sets],
        enabled=enabled,
        writable=writable,
        size=stat.st_size,
        mtime=stat.st_mtime,
    )


def _is_skill_file(entry: Path) -> bool:
    """扁平式技能文件：``<name>.md`` 或 ``<name>.md.disabled``。"""
    return entry.is_file() and (
        entry.name.endswith(".md") or entry.name.endswith(".md" + DISABLED_SUFFIX)
    )


def scan(cwd: Path | None = None) -> list[Skill]:
    """扫描全部技能根，返回已启用 + 已停用的技能。"""
    user_dsh = DSH_HOME / "skills"
    user_agents = DSH_HOME.parent / ".agents" / "skills"
    roots: list[tuple[str, str, Path, bool]] = []

    project = _project_root(cwd)
    if project is not None:
        proj_dsh = project / ".dsh" / "skills"
        proj_agents = project / ".agents" / "skills"
        # 与用户根重合时跳过，避免同一个技能被列出两遍
        if not _same_path(proj_dsh, user_dsh):
            roots.append(("project-dsh", f"项目 · {project}", proj_dsh, False))
        if not _same_path(proj_agents, user_agents):
            roots.append(("project-agents", f"项目 · {project}", proj_agents, False))

    roots.append(("user-dsh", "用户 · ~/.dsh", user_dsh, True))
    roots.append(("user-agents", "用户 · ~/.agents", user_agents, True))

    found: list[Skill] = []
    for key, label, root, writable in roots:
        if not root.is_dir():
            continue
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                for candidate in (entry / "SKILL.md", entry / "SKILL.md.disabled"):
                    if candidate.is_file():
                        s = _read_skill_file(candidate, key, label, root, writable)
                        if s is not None:
                            found.append(s)
            elif _is_skill_file(entry):
                s = _read_skill_file(entry, key, label, root, writable)
                if s is not None:
                    found.append(s)
    _attach_stats(found)
    found.sort(key=lambda s: (s.root_key, not s.enabled, s.name))
    return found


def _attach_stats(skills: list[Skill]) -> None:
    """把 sidecar 里的调用次数/最近使用贴到技能上。"""
    state = sources.read_skill_hub_state()
    totals = (state.get("skillStats") or {}).get("lastTotals") or []
    stats: dict[str, dict] = {}
    for item in totals:
        if isinstance(item, dict) and item.get("name"):
            stats[str(item["name"])] = item
    for s in skills:
        hit = stats.get(s.name)
        if hit:
            s.calls = int(hit.get("count") or 0)
            s.last_used = float(hit.get("lastUsed") or 0)


def read_body(skill: Skill) -> str:
    """读技能正文（去掉 frontmatter），供详情查看。"""
    try:
        text = skill.path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"[读不到文件] {exc}"
    _meta, body = parse_frontmatter(text)
    return body.strip() or text.strip()


def _update_sidecar_disabled(entry: dict | None, path: Path) -> None:
    """把一次启用/停用同步进技能中枢的 sidecar。

    只动 ``disabled`` 一个键，其余（统计快照、回收站、分组…）原样保留，并先备份。
    """
    state = sources.read_skill_hub_state()
    if not state:
        return
    try:
        SKILL_HUB_STATE.with_suffix(".json.dsh-console.bak").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass
    items = [d for d in (state.get("disabled") or []) if isinstance(d, dict)]
    items = [d for d in items if str(d.get("path") or "") != str(path)]
    if entry is not None:
        items.append(entry)
    state["disabled"] = items
    try:
        SKILL_HUB_STATE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def set_enabled(skill: Skill, enabled: bool) -> str:
    """启用/停用技能：重命名发现文件 + 同步 sidecar。

    与技能中枢完全一致的约定——**只改文件名，不删除内容**，随时可逆。
    """
    if not skill.writable:
        raise RuntimeError(
            f"{skill.name} 在只读根（{skill.root_label}）下，"
            "只有用户级技能（~/.dsh/skills 与 ~/.agents/skills）可以启用/停用"
        )
    path = skill.path
    if enabled:
        if not path.name.endswith(DISABLED_SUFFIX):
            return f"{skill.name} 已经是启用状态"
        target = path.with_name(path.name[: -len(DISABLED_SUFFIX)])
        if target.exists():
            raise RuntimeError(f"目标文件已存在，未改动：{target}")
        path.rename(target)
        _update_sidecar_disabled(None, path)
        skill.path = target
        skill.enabled = True
        return f"{skill.name} 已启用（{target.name}）"

    if path.name.endswith(DISABLED_SUFFIX):
        return f"{skill.name} 已经是停用状态"
    target = path.with_name(path.name + DISABLED_SUFFIX)
    if target.exists():
        raise RuntimeError(f"目标文件已存在，未改动：{target}")
    path.rename(target)
    _update_sidecar_disabled(
        {
            "name": skill.name,
            "description": skill.description,
            "path": str(target),
            "root": skill.root_key,
            "disabledAt": int(time.time() * 1000),
        },
        path,
    )
    skill.path = target
    skill.enabled = False
    return f"{skill.name} 已停用（重命名为 {target.name}，内容未删除，可随时恢复）"


def stats() -> dict:
    """汇总：总数、启用/停用、调用次数。"""
    items = scan()
    return {
        "total": len(items),
        "enabled": sum(1 for s in items if s.enabled),
        "disabled": sum(1 for s in items if not s.enabled),
        "writable": sum(1 for s in items if s.writable),
        "calls": sum(s.calls for s in items),
        "roots": len({s.root_key for s in items}),
    }


def open_dir() -> Path:
    """默认写入位置（新建技能用），与中枢一致：优先 ~/.dsh/skills。"""
    return DSH_HOME / "skills"
