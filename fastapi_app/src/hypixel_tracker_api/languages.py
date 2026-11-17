"""语言清单与 RTL 判定。

清单与仓库 ``src/i18n.rs`` 中的语言表保持一致（103 个语言包 + 简体中文原文，
共 104 个选项），服务端与网页共用同一份数据，避免两边语言列表漂移。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

DEFAULT_LANGUAGE = "zh-CN"
RTL_LANGUAGES = frozenset({"ar", "he", "fa", "ur", "sd", "ps"})

# (代码, 母语名称)；顺序即下拉框顺序。
LANGUAGES: Tuple[Tuple[str, str], ...] = (
    ("zh-CN", "简体中文"),
    ("zh-TW", "繁體中文"),
    ("en", "English"),
    ("ja", "日本語"),
    ("ko", "한국어"),
    ("vi", "Tiếng Việt"),
    ("th", "ไทย"),
    ("id", "Bahasa Indonesia"),
    ("ms", "Bahasa Melayu"),
    ("fil", "Filipino"),
    ("hi", "हिन्दी"),
    ("bn", "বাংলা"),
    ("ur", "اردو"),
    ("ar", "العربية"),
    ("he", "עברית"),
    ("fa", "فارسی"),
    ("tr", "Türkçe"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("it", "Italiano"),
    ("pt", "Português"),
    ("nl", "Nederlands"),
    ("ru", "Русский"),
    ("uk", "Українська"),
    ("pl", "Polski"),
    ("cs", "Čeština"),
    ("hu", "Magyar"),
    ("ro", "Română"),
    ("el", "Ελληνικά"),
    ("sv", "Svenska"),
    ("no", "Norsk"),
    ("da", "Dansk"),
    ("fi", "Suomi"),
    ("is", "Íslenska"),
    ("fy", "Frysk"),
    ("lb", "Lëtzebuergesch"),
    ("af", "Afrikaans"),
    ("et", "Eesti"),
    ("lv", "Latviešu"),
    ("lt", "Lietuvių"),
    ("sk", "Slovenčina"),
    ("sl", "Slovenščina"),
    ("hr", "Hrvatski"),
    ("sr", "Српски"),
    ("bs", "Bosanski"),
    ("mk", "Македонски"),
    ("bg", "Български"),
    ("be", "Беларуская"),
    ("sq", "Shqip"),
    ("ga", "Gaeilge"),
    ("cy", "Cymraeg"),
    ("mt", "Malti"),
    ("ca", "Català"),
    ("gl", "Galego"),
    ("eu", "Euskara"),
    ("br", "Brezhoneg"),
    ("my", "မြန်မာ"),
    ("km", "ខ្មែរ"),
    ("lo", "ລາວ"),
    ("ne", "नेपाली"),
    ("si", "සිංහල"),
    ("ta", "தமிழ்"),
    ("te", "తెలుగు"),
    ("ml", "മലയാളം"),
    ("kn", "ಕನ್ನಡ"),
    ("gu", "ગુજરાતી"),
    ("mr", "मराठी"),
    ("pa", "ਪੰਜਾਬੀ"),
    ("sd", "سنڌي"),
    ("ps", "پښتو"),
    ("ku", "Kurdî"),
    ("az", "Azərbaycan dili"),
    ("hy", "Հայերեն"),
    ("ka", "ქართული"),
    ("kk", "Қазақша"),
    ("ky", "Кыргызча"),
    ("uz", "Oʻzbekcha"),
    ("tk", "Türkmençe"),
    ("tg", "Тоҷикӣ"),
    ("mn", "Монгол"),
    ("am", "አማርኛ"),
    ("ti", "ትግርኛ"),
    ("so", "Soomaali"),
    ("sw", "Kiswahili"),
    ("rw", "Ikinyarwanda"),
    ("zu", "isiZulu"),
    ("xh", "isiXhosa"),
    ("ha", "Hausa"),
    ("ig", "Asụsụ Igbo"),
    ("yo", "Èdè Yorùbá"),
    ("mg", "Malagasy"),
    ("ht", "Kreyòl ayisyen"),
    ("qu", "Runa Simi"),
    ("gn", "Avañe'ẽ"),
    ("mi", "Te Reo Māori"),
    ("sm", "Gagana Sāmoa"),
    ("to", "Lea Faka-Tonga"),
    ("fj", "Na Vosa Vakaviti"),
    ("haw", "ʻŌlelo Hawaiʻi"),
    ("kl", "Kalaallisut"),
    ("iu", "ᐃᓄᒃᑎᑐᑦ"),
    ("bo", "བོད་སྐད"),
    ("dz", "རྫོང་ཁ"),
)

LANGUAGE_NAMES: Dict[str, str] = dict(LANGUAGES)

# 常见区域变体 / 旧代码 -> 登记代码，大小写不敏感。
ALIASES: Dict[str, str] = {
    "zh-hans": "zh-CN",
    "zh-hans-cn": "zh-CN",
    "zh-hant": "zh-TW",
    "zh-hant-tw": "zh-TW",
    "zh": "zh-CN",
    "nb": "no",
    "nn": "no",
    "tl": "fil",
    "in": "id",
    "iw": "he",
    "pt-br": "pt",
    "pt-pt": "pt",
    "sr-latn": "sr",
    "az-latn": "az",
    "uz-latn": "uz",
    "ku-arab": "ku",
    "pa-guru": "pa",
    "mn-cyrl": "mn",
}


def normalize(code: str) -> Optional[str]:
    """把用户输入归一到登记代码；未知语言返回 ``None``。"""
    if not code:
        return None
    raw = code.strip()
    if raw in LANGUAGE_NAMES:
        return raw
    lowered = raw.lower().replace("_", "-")
    if lowered in LANGUAGE_NAMES:
        return lowered
    if lowered in ALIASES:
        return ALIASES[lowered]
    primary = lowered.split("-")[0]
    if primary in ALIASES:
        return ALIASES[primary]
    if primary in LANGUAGE_NAMES:
        return primary
    return None


def is_rtl(code: str) -> bool:
    return normalize(code) in RTL_LANGUAGES


def language_list() -> List[Dict[str, str]]:
    """``/api/languages`` 与网页下拉框使用的列表。"""
    return [{"code": code, "name": name} for code, name in LANGUAGES]
