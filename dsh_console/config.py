"""控制台自己的配置文件读写。

**只有一个真源**：路径就是 ``~/.config/dsh-console/config.json``（便携模式下 ``HOME``
已被启动器指到包内，所以自动变成 ``<包根>/home/.config/…``）。

以前这个读写写在 ``ui/main_window.py`` 里，更新功能要用就只能复制一份——配置一旦有
两处读写，迟早会出现"改了这里没改那里"。
"""

from __future__ import annotations

import json
from pathlib import Path

#: 配置文件位置。**不要**在别处再拼一遍这个路径。
CONFIG_PATH = Path.home() / ".config" / "dsh-console" / "config.json"

#: 控制台自身的发布地址（检查/应用更新用）。留空表示没配。
KEY_UPDATE_URL = "consoleUpdateUrl"


def load() -> dict:
    """读配置；读不到或坏了都返回空 dict——配置问题不该让界面起不来。"""
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save(data: dict) -> None:
    """写配置。失败就静默——写不了配置不值得打断用户正在做的事。"""
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def get(key: str, default: str = "") -> str:
    value = load().get(key, default)
    return str(value) if value is not None else default


def put(key: str, value: object) -> None:
    data = load()
    data[key] = value
    save(data)
