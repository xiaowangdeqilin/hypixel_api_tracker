"""命令行入口。

职责：
- ``serve``（默认）/ ``--version`` / ``--check-update`` / ``--print-crash`` / ``--clear-cache``；
- 按系统语言输出帮助与错误提示（见 :mod:`cli_i18n`）；
- 版本检查只提示升级命令，不修改已安装的包。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from typing import Callable, List, Optional

import httpx

from .cli_i18n import detect_language, make_translator
from .config import APP_NAME, APP_VERSION, Settings
from .crashes import CRASH_ID_RE, CrashReporter
from .data_source import HttpJsonClient, clear_cache_files
from .updates import UpdateChecker, upgrade_command

USAGE_EXAMPLES = [
    "hypixel-tracker-api",
    "hypixel-tracker-api serve --host 0.0.0.0 --port 9000",
    "hypixel-tracker-api --check-update",
    "hypixel-tracker-api --print-crash 20261117-203845",
    "hypixel-tracker-api --clear-cache",
]

SECTION_MAP = {
    "usage: ": "usage",
    "positional arguments:": "positional",
    "optional arguments:": "options",
    "options:": "options",
}


class I18nArgumentParser(argparse.ArgumentParser):
    """把 argparse 的固定英文骨架替换成当前语言的文案。"""

    def __init__(self, lang: str, **kwargs: object) -> None:
        self.lang = lang
        self.t = make_translator(lang)
        super().__init__(add_help=False, formatter_class=argparse.RawDescriptionHelpFormatter,
                         **kwargs)

    def format_usage(self) -> str:
        text = super().format_usage()
        return text.replace("usage: ", self.t("usage"), 1)

    def format_help(self) -> str:
        lines = []
        for line in super().format_help().splitlines():
            translated = SECTION_MAP.get(line)
            if translated and line != "usage: ":
                lines.append(self.t(translated) + ":")
            else:
                lines.append(line.replace("usage: ", self.t("usage"), 1))
        return "\n".join(lines) + "\n"

    def error(self, message: str) -> None:  # type: ignore[override]
        missing = re.search(r"argument ([^:]+): expected one argument", message)
        unknown = re.search(r"unrecognized arguments: (.+)", message)
        if "--port" in message:
            detail = self.t("err_invalid_port")
        elif missing:
            detail = self.t("err_missing_value", arg=missing.group(1))
        elif unknown:
            detail = self.t("err_unknown_arg", arg=unknown.group(1))
        else:
            detail = message
        sys.stderr.write(self.format_usage())
        sys.stderr.write(self.t("err_prefix") + " " + detail + "\n")
        raise SystemExit(2)


def _make_port(t) -> Callable[[str], int]:
    def _port(value: str) -> int:
        try:
            port = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(t("err_invalid_port")) from None
        if not 1 <= port <= 65535:
            raise argparse.ArgumentTypeError(t("err_invalid_port"))
        return port

    return _port


def build_parser(lang: str) -> I18nArgumentParser:
    t = make_translator(lang)
    parser = I18nArgumentParser(
        lang,
        prog=APP_NAME,
        description=t("description"),
        epilog=t("examples") + ":\n  " + "\n  ".join(USAGE_EXAMPLES),
    )
    parser.add_argument("command", nargs="?", metavar="command", help=t("cmd_serve"))
    parser.add_argument("--host", default="127.0.0.1", help=t("help_host"))
    parser.add_argument("--port", type=_make_port(t), default=8000, help=t("help_port"))
    parser.add_argument("--reload", action="store_true", help=t("help_reload"))
    parser.add_argument("--version", dest="show_version", action="store_true",
                        help=t("help_version"))
    parser.add_argument("--check-update", action="store_true", help=t("help_check"))
    parser.add_argument("--print-crash", metavar="ID", help=t("help_print"))
    parser.add_argument("--clear-cache", action="store_true", help=t("help_clear"))
    parser.add_argument("-h", "--help", action="help", help=t("help_help"))
    return parser


def _serve(host: str, port: int, reload: bool, lang: str) -> int:
    import uvicorn

    t = make_translator(lang)
    Settings.from_env().ensure_dirs()
    sys.stderr.write(t("starting", host=host, port=port) + "\n")
    uvicorn.run(
        "hypixel_tracker_api.app:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )
    return 0


async def _fetch_release(settings: Settings) -> dict:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={
            "User-Agent": APP_NAME + "/" + APP_VERSION,
            "Accept": "application/vnd.github+json",
        },
    ) as client:
        fetcher = HttpJsonClient(client, settings.timeout_seconds)
        checker = UpdateChecker(
            settings.release_api_url,
            settings.releases_page,
            settings.update_cache_seconds,
        )
        return await checker.check(fetcher.get_json)


def _check_update(lang: str) -> int:
    t = make_translator(lang)
    settings = Settings.from_env()
    try:
        result = asyncio.run(_fetch_release(settings))
    except Exception as exc:  # noqa: BLE001 - CLI 需要把失败变成一行提示
        sys.stderr.write(t("check_failed", error=exc) + "\n")
        return 1

    print(t("check_current", version=result.get("current") or APP_VERSION))
    if result.get("error"):
        sys.stderr.write(t("check_failed", error=result["error"]) + "\n")
        return 1
    print(t("check_latest", latest=result.get("latest") or "—"))
    if result.get("available"):
        print(t("check_available", latest=result["latest"], current=result["current"]))
        print(t("upgrade_hint", command=upgrade_command()))
        print(t("release_page", page=result.get("page")))
    else:
        print(t("check_uptodate", version=result.get("current") or APP_VERSION))
    return 0


def _print_crash(lang: str, crash_id: str) -> int:
    t = make_translator(lang)
    reporter = CrashReporter(Settings.from_env().crash_dir)
    if not CRASH_ID_RE.match(crash_id or ""):
        sys.stderr.write(t("crash_invalid_id", id=crash_id) + "\n")
        return 2
    report = reporter.get(crash_id)
    if report is None:
        sys.stderr.write(
            t("crash_not_found", id=crash_id, dir=str(reporter.directory)) + "\n"
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _clear_cache(lang: str) -> int:
    t = make_translator(lang)
    settings = Settings.from_env()
    removed = clear_cache_files(settings)
    print(t("cache_cleared", count=len(removed), dir=str(settings.cache_dir)))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    lang = detect_language()
    parser = build_parser(lang)
    args = parser.parse_args(argv)

    if args.show_version:
        print(make_translator(lang)("version_line", version=APP_VERSION))
        return 0
    if args.check_update:
        return _check_update(lang)
    if args.print_crash:
        return _print_crash(lang, args.print_crash)
    if args.clear_cache:
        return _clear_cache(lang)

    command = args.command or "serve"
    if command != "serve":
        parser.error(make_translator(lang)("err_unknown_command", arg=command))
    return _serve(args.host, args.port, args.reload, lang)


if __name__ == "__main__":
    sys.exit(main())
