"""命令行参数与日志配置。"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from .converter import EpubConverter
from .errors import EpubConversionError


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""

    parser = argparse.ArgumentParser(
        prog="convert.py",
        description="将简体中文横排 EPUB 转为台湾繁体中文竖排 EPUB。",
    )
    parser.add_argument("input", type=Path, help="输入 EPUB 文件")
    parser.add_argument("output", type=Path, help="输出 EPUB 文件")
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="允许覆盖已存在的输出文件",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="任何正文/目录文档解析失败时，不生成输出文件",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="显示每个文档的详细处理日志",
    )
    return parser


def configure_logging(verbose: bool) -> None:
    """让正常进度与错误统一输出到 stderr。"""

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """执行命令行转换并返回进程退出码。"""

    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)

    try:
        summary = EpubConverter(strict=args.strict).convert(
            args.input,
            args.output,
            overwrite=args.force,
        )
    except EpubConversionError as exc:
        logging.error("转换失败：%s", exc)
        return 1

    logging.info(
        "转换完成：%s；共扫描 %d 个 ZIP 条目，处理 %d 个文本文档，"
        "修改 %d 个文档、%d 个文本/元数据节点，跳过 %d 个文档。",
        args.output,
        summary.total_entries,
        summary.candidate_documents,
        summary.changed_documents,
        summary.converted_nodes,
        summary.skipped_count,
    )
    if summary.skipped_documents:
        logging.warning("跳过列表：%s", "、".join(summary.skipped_documents))
    return 0

