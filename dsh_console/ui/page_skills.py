"""技能市场页。

DSH 的「技能」是插件生态里的一个子类（往 ``ctx.skills`` 注册能力的包）。
本页复用 :class:`~dsh_console.ui.page_market.MarketPage` 的全部能力
（排名、评分、关键字/形态筛选、并发体检、安装卸载、README），只换数据源与文案。

数据取法见 :func:`dsh_console.market.search_skills`：npm 上没有统一的 DSH 技能
关键字，所以用「DSH 插件集合 + skill 文本查询」拿候选，再用技能关键字判定。
"""

from __future__ import annotations

from .. import market
from ..themes import Theme
from .page_market import PAGE_SIZE, MarketPage


class SkillsPage(MarketPage):
    """技能市场。"""

    PAGE_TITLE = "技能市场"
    PAGE_SUBTITLE = (
        "DeepSeek Harness 的技能类插件（往 ctx.skills 注册能力的包）。"
        "在 npm 的 dsh-plugin 集合内按 skill 检索后，仅保留带技能关键字或包名含 skill 的包。"
    )

    def __init__(self, theme: Theme, parent=None) -> None:
        super().__init__(theme, parent)
        # 技能页默认按下载量排，并把「关键字筛选」的提示改成技能语境
        self.kw_edit.setPlaceholderText("在已加载的技能里筛选（包名 / 说明 / 标签）…")
        self.btn_sources.setToolTip("配置技能市场的 GitHub 仓库源（与 DSH 技能中枢共享同一份列表）")

    def _open_source_dialog(self) -> None:
        """技能市场设置的是 GitHub 仓库源（与技能中枢共享）。"""
        from .dialogs_sources import SkillSourceDialog

        SkillSourceDialog(self.theme, self).exec()

    def _fetch_page(self, query: str, offset: int):
        """技能用分页版本：先按 skill 取候选再本地过滤。"""
        return market.search_skills_page(query, PAGE_SIZE, offset=offset)

    def _do_search(self, query: str) -> tuple[list[market.MarketPlugin], str]:
        return market.search_skills(query)
