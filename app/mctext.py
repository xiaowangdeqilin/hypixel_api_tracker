"""Minecraft 颜色代码渲染。

Hypixel 的 ``lore`` 字段使用 Minecraft 传统颜色码，例如::

    §7Break §e10M Ore§7 Blocks.

这类代码是「状态」而不是「包裹」：``§e`` 之后的所有文字都是黄色，直到遇到
下一个颜色码或 ``§r`` 重置。因此不能简单做字符串替换，必须顺序扫描并维护
当前样式状态。本模块把这种文本转换成等价的内联样式 HTML，静态 Pages 页面
与本地 FastAPI 页面共用同一份实现，保证两边呈现一致。

支持范围：

* 颜色 ``§0``–``§9``、``§a``–``§f``（共 16 色，取 Minecraft 官方 RGB 值）
* 格式 ``§l`` 粗体 / ``§o`` 斜体 / ``§n`` 下划线 / ``§m`` 删除线 / ``§k`` 混淆
* 重置 ``§r``（同时清除颜色与全部格式）
* 十六进制颜色 ``§x§R§R§G§G§B§B``（MC 1.16+ 写法）
* ``&`` 前缀写法（第三方接口常见，默认关闭以免误伤 ``R&D`` 这类普通文本）

颜色码在 MC 里是大小写不敏感的（``§A`` 等同 ``§a``），这里同样按小写处理。
"""

from __future__ import annotations

import html
import re
from typing import Dict, List, Optional, Tuple

# Minecraft 官方 16 色（Java 版 §0-§f）
MC_COLORS: Dict[str, str] = {
    "0": "#000000",  # 黑
    "1": "#0000AA",  # 深蓝
    "2": "#00AA00",  # 深绿
    "3": "#00AAAA",  # 深青
    "4": "#AA0000",  # 深红
    "5": "#AA00AA",  # 深紫
    "6": "#FFAA00",  # 金
    "7": "#AAAAAA",  # 灰
    "8": "#555555",  # 深灰
    "9": "#5555FF",  # 蓝
    "a": "#55FF55",  # 绿
    "b": "#55FFFF",  # 青
    "c": "#FF5555",  # 红
    "d": "#FF55FF",  # 粉
    "e": "#FFFF55",  # 黄
    "f": "#FFFFFF",  # 白
}

# 纯黑在深色主题背景上完全不可见，属于可用性问题，单独提亮一档；
# 其余颜色严格使用官方值，保证「识别正确」。
_COLOR_OVERRIDE = {"0": "#404040"}

# 格式码 -> 状态字段
MC_FORMATS = {
    "l": "bold",
    "o": "italic",
    "n": "underline",
    "m": "strike",
    "k": "obf",
}

_FORMAT_KEYS = ("bold", "italic", "underline", "strike", "obf")

_CODE_PREFIX = "\u00a7"  # §
_HEX_RE = re.compile(r"^x((?:[\u00a7&][0-9a-fA-F]){6})")


def _initial_state(default_color: Optional[str]) -> Dict[str, object]:
    state: Dict[str, object] = {"color": default_color}
    for key in _FORMAT_KEYS:
        state[key] = False
    return state


def _style_attr(state: Dict[str, object]) -> str:
    css: List[str] = []
    color = state.get("color")
    if color:
        css.append(f"color:{color}")
    if state.get("bold"):
        css.append("font-weight:700")
    if state.get("italic"):
        css.append("font-style:italic")
    decorations = []
    if state.get("underline"):
        decorations.append("underline")
    if state.get("strike"):
        decorations.append("line-through")
    if decorations:
        css.append("text-decoration:" + " ".join(decorations))
    return ";".join(css)


def _emit(chunk: str, state: Dict[str, object]) -> str:
    """把一段纯文本按当前状态包成 HTML。"""
    if not chunk:
        return ""
    body = html.escape(chunk, quote=False)
    classes = []
    if state.get("obf"):
        classes.append("mc-obf")
    style = _style_attr(state)
    if not style and not classes:
        return body
    attrs = ""
    if classes:
        attrs += f' class="{" ".join(classes)}"'
    if style:
        attrs += f' style="{html.escape(style)}"'
    return f"<span{attrs}>{body}</span>"


def render_mc_text(
    text: Optional[str],
    *,
    default_color: Optional[str] = None,
    allow_ampersand: bool = False,
) -> str:
    """把带 Minecraft 颜色码的文本渲染成安全的 HTML 片段。

    ``default_color`` 用于给没有颜色码的文本指定默认颜色（如 ``#e2e8f0``）；
    ``allow_ampersand`` 打开后同时识别 ``&7`` 写法。
    """
    if text is None:
        return ""
    raw = str(text)
    if not raw:
        return ""

    prefixes = (_CODE_PREFIX, "&") if allow_ampersand else (_CODE_PREFIX,)
    state = _initial_state(default_color)
    out: List[str] = []
    buf: List[str] = []
    i = 0
    length = len(raw)

    while i < length:
        char = raw[i]
        if char in prefixes and i + 1 < length:
            code = raw[i + 1].lower()

            # 十六进制写法：§x§R§R§G§G§B§B
            if code == "x":
                match = _HEX_RE.match(raw[i + 1 :])
                if match:
                    digits = re.findall(r"[0-9a-fA-F]", match.group(1))
                    if len(digits) == 6:
                        out.append(_emit("".join(buf), state))
                        buf = []
                        color = "#" + "".join(digits)
                        state = _initial_state(color)
                        i += 1 + len(match.group(0))
                        continue

            if code in MC_COLORS:
                out.append(_emit("".join(buf), state))
                buf = []
                # 颜色码会重置全部格式（与 MC 行为一致）
                state = _initial_state(_COLOR_OVERRIDE.get(code, MC_COLORS[code]))
                i += 2
                continue

            if code == "r":
                out.append(_emit("".join(buf), state))
                buf = []
                state = _initial_state(None)
                i += 2
                continue

            if code in MC_FORMATS:
                out.append(_emit("".join(buf), state))
                buf = []
                state[MC_FORMATS[code]] = True
                i += 2
                continue

        buf.append(char)
        i += 1

    out.append(_emit("".join(buf), state))
    return "".join(part for part in out if part)


def strip_mc_codes(text: Optional[str], *, allow_ampersand: bool = False) -> str:
    """去掉颜色码，返回纯文本（用于 ``title``、``data.json`` 等场景）。"""
    if text is None:
        return ""
    raw = str(text)
    prefixes = "\u00a7&" if allow_ampersand else "\u00a7"
    pattern = re.compile(f"[{re.escape(prefixes)}][0-9a-fk-orxA-FK-OR]")
    # 十六进制写法连写 7 个码，多轮替换直到稳定
    previous = None
    cleaned = raw
    while previous != cleaned:
        previous = cleaned
        cleaned = pattern.sub("", cleaned)
    return cleaned


def plain_lines(lines: Optional[List[str]], *, allow_ampersand: bool = False) -> List[str]:
    """把 ``fullLore`` 这类多行文本批量转成纯文本。"""
    return [strip_mc_codes(line, allow_ampersand=allow_ampersand) for line in (lines or [])]


def render_mc_lines(
    lines: Optional[List[str]], *, default_color: Optional[str] = None
) -> List[str]:
    """把多行文本逐行渲染成 HTML 片段。"""
    return [render_mc_text(line, default_color=default_color) for line in (lines or [])]


def split_runs(text: Optional[str]) -> List[Tuple[str, Dict[str, object]]]:
    """把文本拆成 ``(文字, 样式状态)`` 序列，便于测试与自定义渲染。"""
    if text is None:
        return []
    raw = str(text)
    state = _initial_state(None)
    runs: List[Tuple[str, Dict[str, object]]] = []
    buf: List[str] = []
    i = 0
    while i < len(raw):
        char = raw[i]
        if char == _CODE_PREFIX and i + 1 < len(raw):
            code = raw[i + 1].lower()
            if code in MC_COLORS:
                if buf:
                    runs.append(("".join(buf), dict(state)))
                    buf = []
                state = _initial_state(_COLOR_OVERRIDE.get(code, MC_COLORS[code]))
                i += 2
                continue
            if code == "r":
                if buf:
                    runs.append(("".join(buf), dict(state)))
                    buf = []
                state = _initial_state(None)
                i += 2
                continue
            if code in MC_FORMATS:
                if buf:
                    runs.append(("".join(buf), dict(state)))
                    buf = []
                state[MC_FORMATS[code]] = True
                i += 2
                continue
        buf.append(char)
        i += 1
    if buf:
        runs.append(("".join(buf), dict(state)))
    return runs
