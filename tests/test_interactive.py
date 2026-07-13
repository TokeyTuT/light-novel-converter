"""交互式批量转换的回归测试。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from zipfile import ZipFile

import pytest
from lxml import etree

from epub_tw_converter import cli
from epub_tw_converter import interactive
from epub_tw_converter.interactive import (
    OUTPUT_SUFFIX,
    convert_batch,
    default_output_path,
    run_interactive,
    validate_epub_file,
)
from epub_tw_converter.models import PageDirection


def test_cli_without_paths_starts_interactive_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用户只输入启动命令时应进入菜单，而不是要求两个路径参数。"""

    monkeypatch.setattr(cli, "run_interactive", lambda: 23)

    assert cli.main([]) == 23


def test_file_dialog_uses_native_picker_on_macos(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """macOS 应继续使用 Finder，避免依赖 tkinter。"""

    expected = [tmp_path / "mac.epub"]
    monkeypatch.setattr(interactive.sys, "platform", "darwin")
    monkeypatch.setattr(
        interactive,
        "_select_epub_files_macos",
        lambda: expected,
    )

    assert interactive.select_epub_files_with_dialog() == expected


def test_file_dialog_uses_tk_picker_on_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Windows 不应尝试调用 macOS 的 osascript。"""

    expected = [tmp_path / "windows.epub"]
    monkeypatch.setattr(interactive.sys, "platform", "win32")
    monkeypatch.setattr(
        interactive,
        "_select_epub_files_tk",
        lambda: expected,
    )

    assert interactive.select_epub_files_with_dialog() == expected


def test_default_output_path_uses_requested_suffix(tmp_path: Path) -> None:
    """批量模式默认在源文件目录输出 ``_conceverted.epub``。"""

    source = tmp_path / "小说.epub"

    assert default_output_path(source) == tmp_path / f"小说{OUTPUT_SUFFIX}.epub"


def test_validate_epub_file_checks_extension_zip_and_mimetype(
    tmp_path: Path,
    epub_factory: Callable[..., tuple[Path, dict[str, bytes]]],
) -> None:
    """选择文件后会在真正转换前完成基本 EPUB 格式检查。"""

    valid_path, _ = epub_factory(include_broken=False)
    text_path = tmp_path / "not-epub.txt"
    text_path.write_text("not an epub", encoding="utf-8")
    broken_path = tmp_path / "broken.epub"
    broken_path.write_bytes(b"not a zip")

    assert validate_epub_file(valid_path) is None
    assert "扩展名" in (validate_epub_file(text_path) or "")
    assert "ZIP/EPUB" in (validate_epub_file(broken_path) or "")


@pytest.mark.parametrize("line_ending", [b"\n", b"\r\n"])
def test_validate_epub_file_accepts_legacy_mimetype_line_ending(
    epub_factory: Callable[..., tuple[Path, dict[str, bytes]]],
    line_ending: bytes,
) -> None:
    """批量入口不应拦截可由转换器自动规范化的旧 EPUB。"""

    legacy_path, _ = epub_factory(
        include_broken=False,
        mimetype_data=b"application/epub+zip" + line_ending,
    )

    assert validate_epub_file(legacy_path) is None


def test_convert_batch_reports_each_success_and_failure(
    tmp_path: Path,
    epub_factory: Callable[..., tuple[Path, dict[str, bytes]]],
) -> None:
    """一个文件失败不能阻止同一批中其他 EPUB 的转换。"""

    valid_path, _ = epub_factory(include_broken=False)
    invalid_path = tmp_path / "wrong-format.txt"
    invalid_path.write_text("not an epub", encoding="utf-8")

    results = convert_batch(
        [valid_path, invalid_path],
        page_direction=PageDirection.LEFT,
        overwrite=False,
        strict=False,
    )

    assert [result.succeeded for result in results] == [True, False]
    assert results[0].output_path == default_output_path(valid_path)
    assert results[0].output_path.is_file()
    assert "格式检查失败" in results[1].message


def test_interactive_menu_uses_selected_options_and_prints_summary(
    tmp_path: Path,
    epub_factory: Callable[..., tuple[Path, dict[str, bytes]]],
) -> None:
    """菜单会将向左翻页传给转换器并逐文件显示批量结果。"""

    valid_path, _ = epub_factory(include_broken=False)
    invalid_path = tmp_path / "wrong-format.txt"
    invalid_path.write_text("not an epub", encoding="utf-8")
    answers = iter(["1", "1", "1", "1"])
    messages: list[str] = []

    exit_code = run_interactive(
        file_selector=lambda: [valid_path, invalid_path],
        input_func=lambda _: next(answers),
        output_func=messages.append,
    )

    assert exit_code == 1
    output_path = default_output_path(valid_path)
    assert output_path.is_file()
    with ZipFile(output_path) as archive:
        package = etree.fromstring(archive.read("OEBPS/content.opf"))
        spine = package.xpath('//*[local-name()="spine"]')[0]
        assert spine.get("page-progression-direction") == "rtl"
    assert any("成功" in message and valid_path.name in message for message in messages)
    assert any("失败" in message and invalid_path.name in message for message in messages)
