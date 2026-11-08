"""可选的多语言（i18n）支持。

设计原则
--------
* **默认关闭**：不设置环境变量 ``ENABLE_I18N`` 时，页面与旧版完全一致
  （纯中文、无语言切换器、零额外开销）。
* 开启方式：启动前设置 ``ENABLE_I18N=1``（也接受 true/yes/on，大小写不敏感）。
* 译文存放在 ``app/locales/<语言代码>.json``，**键为简体中文原文，值为译文**。
  想新增语言只需在该目录放入一个新的 JSON 文件并在 ``LANGUAGE_NAMES``
  里登记名称，无需改动任何代码。
* 缺失或未翻译的条目自动回退为简体中文原文，因此语言包可以只翻译
  高频条目，渐进补全。
* 从右往左（RTL）语言（阿拉伯语、希伯来语、波斯语、乌尔都语）会把
  ``<html dir>`` 设为 ``rtl``，由 ``static/i18n.css`` 做整体镜像适配；
  代码片段、时间戳、纯数字等仍按 LTR 展示。

语言解析顺序：URL ``?lang=`` 参数 > Cookie ``hypixel-lang`` > 简体中文。
不合法的取值一律忽略（白名单校验），不存在注入面。
"""

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

LOCALES_DIR = Path(__file__).resolve().parent / "locales"

DEFAULT_LANGUAGE = "zh-CN"
COOKIE_NAME = "hypixel-lang"
COOKIE_MAX_AGE = 365 * 24 * 60 * 60

# 从右往左语言：<html dir="rtl"> + i18n.css 镜像适配
RTL_LANGUAGES = frozenset({"ar", "he", "fa", "ur"})

# 语言代码 -> 本语言自称（用于语言切换器展示，一律用母语书写）
LANGUAGE_NAMES: Dict[str, str] = {
    "zh-CN": "简体中文",
    "zh-TW": "繁體中文",
    "en": "English",
    "ja": "日本語",
    "ko": "한국어",
    "vi": "Tiếng Việt",
    "th": "ไทย",
    "id": "Bahasa Indonesia",
    "ms": "Bahasa Melayu",
    "fil": "Filipino",
    "hi": "हिन्दी",
    "bn": "বাংলা",
    "ur": "اردو",
    "ar": "العربية",
    "he": "עברית",
    "fa": "فارسی",
    "tr": "Türkçe",
    "fr": "Français",
    "de": "Deutsch",
    "es": "Español",
    "it": "Italiano",
    "pt": "Português",
    "nl": "Nederlands",
    "ru": "Русский",
    "uk": "Українська",
    "pl": "Polski",
    "cs": "Čeština",
    "hu": "Magyar",
    "ro": "Română",
    "el": "Ελληνικά",
    "sv": "Svenska",
}

_lock = threading.Lock()
_pack_cache: Dict[str, Dict[str, str]] = {}
_enabled_cache: Optional[bool] = None
_discovered_cache: Optional[set] = None

# ---------------------------------------------------------------------------
# 含 HTML 标记 / 占位符的长句：作为翻译键统一在这里定义，模板与语言包共用。
# 语言包里的键必须与这里的中文原文完全一致；允许包含 <span>/<strong>/<code>
# 与 {days}、{time} 这类占位符（仅由仓库受控内容渲染，非用户输入）。
# ---------------------------------------------------------------------------
CHANGES_HINT = (
    '按代码审核（diff）风格展示：<span class="legend-add">新增只标绿 +</span>、'
    '<span class="legend-del">删除只标红 -</span>；修改<strong>只显示发生变化的那一段</strong>'
    '（删除行标红、新增行标绿，未变化内容不输出）。端点 diff 已排除 version / lastUpdated 等'
    '元数据字段。<strong>只展示最近 {days} 天的增删改，更早的记录会自动清理。</strong>'
)
FIRESALE_EMPTY_HINT = (
    '数据来自 <code>/v2/skyblock/firesales</code>。当前还没有特卖数据，先运行 '
    '<code>python scripts/build_dashboard.py</code> 生成一次即可。'
)
FIRESALE_HINT = (
    '价格以宝石（Gems）结算。特卖<strong>开始前官方可能随时调整时间、数量与价格</strong>，'
    '因此每次运行都会重新抓取并覆盖，不存在「查过就跳过」。一次响应里可能同时有多期特卖，'
    '每期独立成卡，按开始时间排序；主视图只展示<strong>即将开始与进行中</strong>的期次。'
)
BINGO_EMPTY_HINT = (
    '数据来自 <code>/v2/resources/skyblock/bingo</code>。当前还没有 Bingo 数据，先运行 '
    '<code>python scripts/build_dashboard.py</code> 生成一次即可。'
)
BINGO_HINT = (
    '发布时间取自接口的 <code>lastUpdated</code>，「首次发现」是本仓库第一次抓到这一期的时刻；'
    '开始与结束时间取自 <code>start</code> / <code>end</code>。任务文案保留 Minecraft 颜色码'
    '并按官方配色渲染。<strong>全服进度只用于展示，不参与变更比对</strong>。'
)
BINGO_TIP = (
    '只有「未开始」和「进行中」显示倒计时，由浏览器按本地时间逐秒计算，到点自动切换状态，'
    '不受 Actions 抓取延迟影响；已结束的期次不再计时。点击任意格子查看完整文案与全服进度。'
)


def is_enabled() -> bool:
    """多语言开关：环境变量 ``ENABLE_I18N``，默认关闭。"""
    global _enabled_cache
    if _enabled_cache is None:
        raw = os.getenv("ENABLE_I18N", "").strip().lower()
        _enabled_cache = raw in ("1", "true", "yes", "on")
    return _enabled_cache


def reset_cache() -> None:
    """清空缓存（供测试或热更新语言包使用）。"""
    global _enabled_cache, _discovered_cache
    with _lock:
        _pack_cache.clear()
        _enabled_cache = None
        _discovered_cache = None


def available_languages() -> Dict[str, str]:
    """返回可展示在切换器里的语言：{代码: 母语名称}，简体中文固定在最前。"""
    global _discovered_cache
    if _discovered_cache is None:
        _discovered_cache = {p.stem for p in LOCALES_DIR.glob("*.json")}
    langs = {DEFAULT_LANGUAGE: LANGUAGE_NAMES[DEFAULT_LANGUAGE]}
    for code in sorted(LANGUAGE_NAMES):
        if code != DEFAULT_LANGUAGE and code in _discovered_cache:
            langs[code] = LANGUAGE_NAMES.get(code, code)
    return langs


# 小写代码 -> 登记代码（如 "en" -> "en"、"zh-cn" -> "zh-CN"），外加历史别名
_LOWERCASE_ALIASES = {
    "zh-hans": "zh-CN",
    "zh-hans-cn": "zh-CN",
    "zh-hant": "zh-TW",
    "zh-hant-tw": "zh-TW",
    "no": "sv",  # 挪威语归并到已提供的北欧语言（回退链相同）
    "nb": "sv",
    "nn": "sv",
    "tl": "fil",
    "in": "id",
    "iw": "he",
    "pt-br": "pt",
    "pt-pt": "pt",
}


def normalize_language(raw: Any) -> Optional[str]:
    """白名单校验：只接受登记过的语言代码（大小写、下划线不敏感）。"""
    if not isinstance(raw, str) or not raw or len(raw) > 16:
        return None
    candidate = raw.strip().replace("_", "-").lower()
    candidate = _LOWERCASE_ALIASES.get(candidate, candidate)
    # 常见区域变体（zh-cn / pt-br 之外）截断为主代码，如 "fr-CA" -> "fr"
    if candidate not in LANGUAGE_NAMES and "-" in candidate:
        candidate = candidate.split("-", 1)[0]
    if candidate in LANGUAGE_NAMES and candidate in available_languages():
        return candidate
    return None


def is_rtl(code: str) -> bool:
    return code in RTL_LANGUAGES


def load_pack(code: str) -> Dict[str, str]:
    """加载语言包；损坏的 JSON 一律视为空包（回退中文），绝不让页面 500。"""
    if code == DEFAULT_LANGUAGE:
        return {}
    with _lock:
        cached = _pack_cache.get(code)
        if cached is not None:
            return cached
    pack: Dict[str, str] = {}
    path = LOCALES_DIR / f"{code}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            pack = {
                str(k): str(v)
                for k, v in data.items()
                if isinstance(v, str) and isinstance(k, str)
            }
    except (OSError, json.JSONDecodeError, ValueError):
        pack = {}
    with _lock:
        _pack_cache[code] = pack
    return pack


def translator(code: str) -> Callable[..., str]:
    """生成 ``t(text, **kw)`` 翻译函数。

    * 文案缺失 -> 回退简体中文原文；
    * 传入关键字参数 -> 对译文做 ``str.format`` 填充（译文内容受控于
      仓库内语言包，不存在用户输入进入格式串的路径）。
    """
    pack = load_pack(code)

    def t(key: str, **kwargs: Any) -> str:
        # 允许译文为空字符串（如中文量词「个」在部分语言中不需要），
        # 因此用 ``in`` 判断而不是真值判断。
        template = pack[key] if key in pack else key
        if kwargs:
            try:
                return template.format(**kwargs)
            except (KeyError, IndexError, ValueError):
                return key.format(**kwargs)
        return template

    return t


def js_pack(code: str) -> Dict[str, str]:
    """前端脚本使用的译文集合（直接复用整包，体量很小）。"""
    return load_pack(code)


def resolve_language(request: Any, query_lang: Any = None) -> str:
    """决定本次请求使用的语言：?lang= > Cookie > 默认。

    所有输入都过白名单；多语言关闭时永远返回默认语言。
    """
    if not is_enabled():
        return DEFAULT_LANGUAGE
    for candidate in (query_lang, request.cookies.get(COOKIE_NAME) if request else None):
        code = normalize_language(candidate)
        if code:
            return code
    return DEFAULT_LANGUAGE
