"""静态检查：找出"用了但没定义"的名字。

为什么需要它：`compileall` 只证明**语法**对，`import` 只证明**模块级**能跑通。
而 `apply_screen_fit` 这类名字是在**函数体里**被引用、在**真正调用时**才炸——
`python -m dsh_console.gui` 一启动就 `NameError` 退出，但编译和导入都是绿的。
实测这个坑踩了三次（`_ThemeDialog`、`apply_screen_fit` ×2），所以做成自检项。

做法：逐文件解析 AST，收集
  * 模块级导入/定义的名字
  * 每个函数作用域内的参数、赋值、for/with/comprehension 目标、except 名、
    global/nonlocal、以及嵌套定义
再报告"加载了但哪一层都没绑定、也不在内建里"的名字。

**不追求 lint 级别的完备**（不做跨模块属性分析、不跟 import *），
目标只有一个：**在真机上点按钮之前，把这类必然崩溃的名字找出来**。
"""

from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__",
                                 "__spec__", "__loader__", "__builtins__", "WindowsError"}


class _Scope:
    def __init__(self, parent: "_Scope | None" = None) -> None:
        self.names: set[str] = set()
        self.parent = parent

    def bind(self, name: str) -> None:
        self.names.add(name)

    def has(self, name: str) -> bool:
        node: _Scope | None = self
        while node is not None:
            if name in node.names:
                return True
            node = node.parent
        return False


def _bind_target(node: ast.AST, scope: _Scope) -> None:
    """把赋值目标里的名字都绑进作用域。"""
    if isinstance(node, ast.Name):
        scope.bind(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for e in node.elts:
            _bind_target(e, scope)
    elif isinstance(node, ast.Starred):
        _bind_target(node.value, scope)
    # Attribute / Subscript 不是新名字


def _strip_annotations(tree: ast.Module) -> None:
    """把注解表达式从树里摘掉（模块有 PEP 563 时用）。"""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.returns = None
            for a in (node.args.posonlyargs + node.args.args + node.args.kwonlyargs):
                a.annotation = None
            if node.args.vararg:
                node.args.vararg.annotation = None
            if node.args.kwarg:
                node.args.kwarg.annotation = None
        elif isinstance(node, ast.AnnAssign):
            node.annotation = None


def _bind_self(node: ast.AST, scope: _Scope) -> None:
    """绑定**这条语句自己**引入的名字。

    关键：绑定必须发生在"处理这个节点时"，而不是"它作为别人的子节点时"。
    第一版把绑定写在 `_collect` 的子节点分支里，而函数体是 `_collect(stmt, …)`
    直接传进去的——语句自己引入的名字于是从没被绑上，`x = 1; print(x)` 都误报。
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        scope.bind(node.name)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            scope.bind((alias.asname or alias.name).split(".")[0])
    elif isinstance(node, ast.Assign):
        for tgt in node.targets:
            _bind_target(tgt, scope)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        _bind_target(node.target, scope)
    elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
        _bind_target(node.target, scope)     # 推导式的循环变量也是 comprehension
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        for item in node.items:
            if item.optional_vars is not None:
                _bind_target(item.optional_vars, scope)
    elif isinstance(node, ast.ExceptHandler):
        if node.name:
            scope.bind(node.name)
    elif isinstance(node, (ast.Global, ast.Nonlocal)):
        for n in node.names:
            scope.bind(n)


def _walk_body(stmts: list[ast.stmt], scope: _Scope, out: list[tuple[str, int]]) -> None:
    """按顺序走一串语句：先绑这条语句引入的名字，再查它里面的引用。"""
    for st in stmts:
        _bind_self(st, scope)
        _collect(st, scope, out)


def _bind_module_level(node: ast.AST, scope: _Scope) -> None:
    """先把**整个模块级**的名字都绑好，再去看函数体。

    为什么必须两遍：函数完全可以引用定义在它**后面**的模块级名字
    （`def f(): return LATER` / `LATER = 1`）。按源码顺序边走边绑的话，
    这种前向引用会被误报成"未定义"。
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        scope.bind(node.name)
        return                      # 不下钻函数/类体
    _bind_self(node, scope)
    for child in ast.iter_child_nodes(node):
        _bind_module_level(child, scope)


def _collect(node: ast.AST, scope: _Scope, out: list[tuple[str, int]]) -> None:
    """检查 ``node`` 内部的引用；遇到新作用域就开子作用域。"""
    # 这条节点自己引入的名字先绑上。对表达式是空操作；对
    # `except … as exc`（处理器的 name 不是 stmt，走不到 _walk_body）是必需的。
    _bind_self(node, scope)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        inner = _Scope(scope)
        for arg in (node.args.posonlyargs + node.args.args + node.args.kwonlyargs):
            inner.bind(arg.arg)
        if node.args.vararg:
            inner.bind(node.args.vararg.arg)
        if node.args.kwarg:
            inner.bind(node.args.kwarg.arg)
        _walk_body(node.body, inner, out)
        for dec in node.decorator_list:
            _collect(dec, scope, out)
        for dflt in (node.args.defaults + [d for d in node.args.kw_defaults if d]):
            _collect(dflt, scope, out)
        return
    if isinstance(node, ast.Lambda):
        inner = _Scope(scope)
        for arg in (node.args.posonlyargs + node.args.args + node.args.kwonlyargs):
            inner.bind(arg.arg)
        if node.args.vararg:
            inner.bind(node.args.vararg.arg)
        if node.args.kwarg:
            inner.bind(node.args.kwarg.arg)
        _collect(node.body, inner, out)
        return
    if isinstance(node, ast.ClassDef):
        inner = _Scope(scope)
        for base in node.bases:
            _collect(base, scope, out)
        _walk_body(node.body, inner, out)
        return
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        inner = _Scope(scope)
        for gen in node.generators:
            _bind_self(gen, inner)
            _collect(gen.iter, inner, out)     # iter 在**外层**求值
            for cond in gen.ifs:
                _collect(cond, inner, out)
        for field in ("elt", "key", "value"):
            sub = getattr(node, field, None)
            if sub is not None:
                _collect(sub, inner, out)
        return
    if isinstance(node, ast.Name):
        if isinstance(node.ctx, ast.Load) and not scope.has(node.id) \
                and node.id not in BUILTINS:
            out.append((node.id, node.lineno))
        return
    # 普通节点：把语句块交给 _walk_body（保证顺序绑定），其余递归
    for field, value in ast.iter_fields(node):
        if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
            _walk_body(value, scope, out)
        elif isinstance(value, ast.AST):
            _collect(value, scope, out)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, ast.AST):
                    _collect(item, scope, out)


def _has_future_annotations(tree: ast.Module) -> bool:
    """模块有没有 ``from __future__ import annotations``。

    有的话注解**不会在运行时求值**（PEP 563），所以注解里出现只用于类型的名字
    （典型是 `if TYPE_CHECKING: import x` 引入的）是安全的，不该报。
    不区分这一点的话检查器会一直喊狼来了，喊多了就没人看了。
    """
    for st in tree.body:
        if isinstance(st, ast.ImportFrom) and st.module == "__future__":
            if any(a.name == "annotations" for a in st.names):
                return True
    return False


def check(path: Path) -> list[tuple[str, int]]:
    """返回 ``[(未定义的名字, 行号), …]``。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [(f"<语法错误 {exc}>", exc.lineno or 0)]
    root = _Scope()
    _bind_module_level(tree, root)      # 第一遍：模块级名字全部先绑好
    findings: list[tuple[str, int]] = []
    if _has_future_annotations(tree):
        _strip_annotations(tree)        # 注解不求值，先摘掉再查
    _collect(tree, root, findings)      # 第二遍：再查函数体里的引用
    # 去重（同一名字多处用只报第一次）
    seen: set[str] = set()
    uniq: list[tuple[str, int]] = []
    for name, line in findings:
        if name not in seen:
            seen.add(name)
            uniq.append((name, line))
    return uniq


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path("dsh_console")
    files = sorted(root.rglob("*.py")) + [Path("main.py")]
    total = 0
    for f in files:
        if not f.is_file():
            continue
        bad = check(f)
        if bad:
            total += len(bad)
            for name, line in bad:
                print(f"{f}:{line}: 未定义的名字 {name!r}")
    if total:
        print(f"\n共 {total} 处：这些名字一旦被调用就会 NameError")
        return 1
    print(f"✓ 扫描 {len(files)} 个文件，没有未定义的名字")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
