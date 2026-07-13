"""macOS 与 Windows 交互式批量转换入口。"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from .converter import COMPATIBLE_EPUB_MIMETYPES, EpubConverter
from .errors import EpubConversionError
from .models import PageDirection

OUTPUT_SUFFIX = "_conceverted"
InputFunction = Callable[[str], str]
OutputFunction = Callable[[str], None]
FileSelector = Callable[[], list[Path]]


@dataclass(frozen=True)
class BatchFileResult:
    """单本 EPUB 的批量转换结果。"""

    input_path: Path
    output_path: Path | None
    succeeded: bool
    message: str


def default_output_path(input_path: Path) -> Path:
    """返回同目录下带用户指定后缀的默认输出路径。"""

    return input_path.with_name(f"{input_path.stem}{OUTPUT_SUFFIX}.epub")


def validate_epub_file(path: Path) -> str | None:
    """进行快速格式检查；返回错误文本，合法时返回 ``None``。"""

    if not path.is_file():
        return "文件不存在或不是普通文件"
    if path.suffix.lower() != ".epub":
        return "文件扩展名不是 .epub"

    try:
        with ZipFile(path) as archive:
            if archive.read("mimetype") not in COMPATIBLE_EPUB_MIMETYPES:
                return "mimetype 不符合 EPUB 规范"
    except KeyError:
        return "缺少 EPUB 的 mimetype 文件"
    except BadZipFile:
        return "不是有效的 ZIP/EPUB 文件"
    except OSError as exc:
        return f"无法读取文件：{exc}"
    return None


def _select_epub_files_macos() -> list[Path]:
    """通过 macOS Finder 的原生对话框选择一个或多个 EPUB。"""

    script = """
tell application "Finder" to activate
set selectedFiles to choose file with prompt "选择一个或多个 EPUB 文件" with multiple selections allowed
set outputPaths to ""
repeat with selectedFile in selectedFiles
    set outputPaths to outputPaths & POSIX path of selectedFile & linefeed
end repeat
return outputPaths
"""
    try:
        completed = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise EpubConversionError(
            "当前系统没有 osascript，无法打开 macOS 文件选择框。"
        ) from exc

    if completed.returncode:
        # 用户点“取消”时 osascript 返回非零，这不是转换错误。
        if "User canceled" in completed.stderr or "-128" in completed.stderr:
            return []
        detail = completed.stderr.strip() or "未知 AppleScript 错误"
        raise EpubConversionError(f"无法打开文件选择框：{detail}")

    return [Path(item) for item in completed.stdout.splitlines() if item]


def _select_epub_files_tk() -> list[Path]:
    """通过 Windows 等平台的 Tk 系统对话框选择一个或多个 EPUB。"""

    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise EpubConversionError(
            "当前 Python 未安装 tkinter，无法打开系统文件选择框；"
            "请安装包含 Tcl/Tk 的 Python 发行版。"
        ) from exc

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise EpubConversionError(f"无法打开系统文件选择框：{exc}") from exc

    root.withdraw()
    try:
        # 将对话框带到前台，避免在 Windows 中被终端窗口遮挡。
        root.attributes("-topmost", True)
        selected_paths = filedialog.askopenfilenames(
            parent=root,
            title="选择一个或多个 EPUB 文件",
            filetypes=[("EPUB 文件", "*.epub"), ("所有文件", "*.*")],
        )
    finally:
        root.destroy()

    return [Path(item) for item in selected_paths]


def select_epub_files_with_dialog() -> list[Path]:
    """按当前系统打开支持多选的原生文件选择框。"""

    if sys.platform == "darwin":
        return _select_epub_files_macos()
    return _select_epub_files_tk()


def convert_batch(
    input_paths: Iterable[Path],
    *,
    page_direction: PageDirection,
    overwrite: bool,
    strict: bool,
) -> list[BatchFileResult]:
    """逐本转换并收集成功、格式错误及转换异常，而不中断整个批次。"""

    results: list[BatchFileResult] = []
    for input_path in input_paths:
        source = input_path.expanduser()
        validation_error = validate_epub_file(source)
        if validation_error:
            results.append(
                BatchFileResult(
                    input_path=source,
                    output_path=None,
                    succeeded=False,
                    message=f"格式检查失败：{validation_error}",
                )
            )
            continue

        output_path = default_output_path(source)
        try:
            summary = EpubConverter(
                strict=strict,
                page_direction=page_direction,
            ).convert(
                source,
                output_path,
                overwrite=overwrite,
            )
        except EpubConversionError as exc:
            results.append(
                BatchFileResult(
                    input_path=source,
                    output_path=output_path,
                    succeeded=False,
                    message=str(exc),
                )
            )
            continue

        message = (
            f"已处理 {summary.candidate_documents} 个文本文档，"
            f"跳过 {summary.skipped_count} 个"
        )
        results.append(
            BatchFileResult(
                input_path=source,
                output_path=output_path,
                succeeded=True,
                message=message,
            )
        )
    return results


def print_batch_report(
    results: Iterable[BatchFileResult],
    *,
    output_func: OutputFunction = print,
) -> int:
    """打印逐文件结果，并返回适合作为进程退出码的状态。"""

    result_list = list(results)
    successes = [result for result in result_list if result.succeeded]
    failures = [result for result in result_list if not result.succeeded]

    output_func("\n转换结果")
    for result in successes:
        output_func(
            f"  成功  {result.input_path.name} -> {result.output_path.name}"
        )
    for result in failures:
        output_func(f"  失败  {result.input_path.name}：{result.message}")
    output_func(f"汇总：成功 {len(successes)} 本，失败 {len(failures)} 本。")
    return 1 if failures else 0


def _choose_option(
    prompt: str,
    choices: dict[str, str],
    *,
    default_key: str,
    input_func: InputFunction,
    output_func: OutputFunction,
) -> str:
    """反复读取菜单项，直至用户输入有效选择。"""

    while True:
        answer = input_func(prompt).strip() or default_key
        if answer in choices:
            return choices[answer]
        output_func("输入无效，请按菜单中的编号选择。")


def run_interactive(
    *,
    file_selector: FileSelector = select_epub_files_with_dialog,
    input_func: InputFunction = input,
    output_func: OutputFunction = print,
) -> int:
    """运行菜单、拉起文件选择框并执行批量转换。"""

    output_func("\n简体横排 EPUB -> 台湾繁体竖排 EPUB")
    if sys.platform == "darwin":
        selection_hint = "即将打开 Finder，请在对话框中按 Command 键多选 EPUB 文件。"
    else:
        selection_hint = "即将打开系统文件选择框，请按 Ctrl 键多选 EPUB 文件。"
    output_func(selection_hint)
    try:
        input_paths = file_selector()
    except EpubConversionError as exc:
        output_func(f"无法选择文件：{exc}")
        return 1

    if not input_paths:
        output_func("未选择文件，已取消。")
        return 0

    output_func(f"已选择 {len(input_paths)} 个文件。")
    output_func("转换格式：1. 台湾繁体竖排 EPUB（OpenCC s2twp）")
    _choose_option(
        "请选择转换格式 [1]：",
        {"1": "tw-vertical"},
        default_key="1",
        input_func=input_func,
        output_func=output_func,
    )

    output_func("翻页方向：1. 向左  2. 向右  3. 保留原书")
    direction_value = _choose_option(
        "请选择翻页方向 [1/2/3，默认 1]：",
        {
            "1": PageDirection.LEFT.value,
            "2": PageDirection.RIGHT.value,
            "3": PageDirection.KEEP.value,
        },
        default_key="1",
        input_func=input_func,
        output_func=output_func,
    )
    overwrite_value = _choose_option(
        "同名输出已存在时：1. 覆盖  2. 不覆盖 [1/2，默认 2]：",
        {"1": "yes", "2": "no"},
        default_key="2",
        input_func=input_func,
        output_func=output_func,
    )
    strict_value = _choose_option(
        "遇到坏章节时：1. 跳过并继续  2. 整本失败 [1/2，默认 1]：",
        {"1": "no", "2": "yes"},
        default_key="1",
        input_func=input_func,
        output_func=output_func,
    )

    results = convert_batch(
        input_paths,
        page_direction=PageDirection(direction_value),
        overwrite=overwrite_value == "yes",
        strict=strict_value == "yes",
    )
    return print_batch_report(results, output_func=output_func)
