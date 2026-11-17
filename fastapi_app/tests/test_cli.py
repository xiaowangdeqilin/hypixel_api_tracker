"""CLI 语言选择、帮助与错误提示测试。"""

from __future__ import annotations

import json

import pytest

from hypixel_tracker_api import cli
from hypixel_tracker_api.cli_i18n import detect_language, translate
from hypixel_tracker_api.crashes import CrashReporter


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"LANG": "zh_CN.UTF-8"}, "zh"),
        ({"LANG": "ja_JP.UTF-8"}, "ja"),
        ({"LC_ALL": "de_DE.UTF-8", "LANG": "fr_FR.UTF-8"}, "de"),
        ({"LANG": "pt_BR.UTF-8"}, "pt"),
        ({"LANG": "ru_RU.UTF-8"}, "ru"),
        ({"LANG": "C"}, "en"),
        ({"LANG": ""}, "en"),
        ({"LANG": "ko_KR.UTF-8"}, "en"),
    ],
)
def test_detect_language(env, expected):
    assert detect_language(env) == expected


def test_translate_falls_back_to_english():
    assert translate("zh", "help_clear").startswith("清除")
    assert translate("ko", "help_clear") == translate("en", "help_clear")
    assert translate("en", "missing_key") == "missing_key"


def test_help_is_translated(monkeypatch, capsys):
    monkeypatch.setenv("LC_ALL", "zh_CN.UTF-8")
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])
    assert exit_info.value.code == 0

    output = capsys.readouterr().out
    assert "用法" in output
    assert "选项" in output
    assert "--check-update" in output


def test_version_output_uses_locale(monkeypatch, capsys):
    monkeypatch.setenv("LANG", "ja_JP.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    assert cli.main(["--version"]) == 0
    assert "3.0.0" in capsys.readouterr().out


def test_invalid_port_message_is_localized(monkeypatch, capsys):
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["serve", "--port", "70000"])
    assert exit_info.value.code == 2
    assert "Port" in capsys.readouterr().err


def test_print_crash_reads_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HYPIXEL_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    report_id = CrashReporter(tmp_path / "crash").write(
        RuntimeError("boom"), path="/x", method="GET", request_id="req"
    )

    assert cli.main(["--print-crash", report_id]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["id"] == report_id
    assert printed["message"] == "boom"


def test_print_crash_rejects_bad_id(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HYPIXEL_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    assert cli.main(["--print-crash", "../../etc/passwd"]) == 2
    assert "invalid crash id" in capsys.readouterr().err

    assert cli.main(["--print-crash", "20260101-000000"]) == 1
    assert "no crash report" in capsys.readouterr().err
