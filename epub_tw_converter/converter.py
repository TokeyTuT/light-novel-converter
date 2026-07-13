"""EPUB ZIP 容器校验、条目转换与原子重打包。"""

from __future__ import annotations

import copy
import logging
import os
import shutil
import stat
import tempfile
from pathlib import Path, PurePosixPath
from zipfile import (
    BadZipFile,
    ZIP_DEFLATED,
    ZIP_STORED,
    ZipFile,
    ZipInfo,
)

from .documents import DocumentTransformer
from .errors import (
    DocumentParseError,
    EpubConversionError,
    UnsafeArchiveError,
)
from .index import build_epub_index, detect_unsupported_encryption
from .models import ConversionSummary, DocumentKind, PageDirection
from .text import TaiwanTextConverter

LOGGER = logging.getLogger(__name__)
EPUB_MIMETYPE = b"application/epub+zip"
# 一些旧制书工具会在 mimetype 末尾写入换行。该输入不完全符合
# EPUB OCF，但内容仍可被可靠识别；重打包时会写回严格规范的值。
COMPATIBLE_EPUB_MIMETYPES = frozenset(
    {
        EPUB_MIMETYPE,
        EPUB_MIMETYPE + b"\n",
        EPUB_MIMETYPE + b"\r\n",
    }
)
MAX_TOTAL_UNCOMPRESSED_SIZE = 4 * 1024 * 1024 * 1024
MAX_ENTRY_UNCOMPRESSED_SIZE = 1024 * 1024 * 1024
MAX_TEXT_DOCUMENT_SIZE = 64 * 1024 * 1024
COPY_BUFFER_SIZE = 1024 * 1024
MAX_ARCHIVE_ENTRIES = 100_000


class EpubConverter:
    """将简体横排 EPUB 转为台湾繁体竖排 EPUB。"""

    def __init__(
        self,
        *,
        strict: bool = False,
        page_direction: PageDirection | str = PageDirection.KEEP,
    ) -> None:
        self._strict = strict
        try:
            resolved_direction = PageDirection(page_direction)
        except ValueError as exc:
            valid_values = ", ".join(direction.value for direction in PageDirection)
            raise EpubConversionError(
                f"无效的翻页方向 {page_direction!r}；可选值：{valid_values}"
            ) from exc
        self._transformer = DocumentTransformer(
            TaiwanTextConverter("s2twp"),
            page_direction=resolved_direction,
        )

    def convert(
        self,
        input_path: str | Path,
        output_path: str | Path,
        *,
        overwrite: bool = False,
    ) -> ConversionSummary:
        """转换 EPUB，并仅在全部必要步骤成功后原子替换输出文件。"""

        source = Path(input_path).expanduser()
        destination = Path(output_path).expanduser()
        self._validate_paths(source, destination, overwrite)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise EpubConversionError(
                f"无法创建输出目录 {destination.parent}：{exc}"
            ) from exc

        temporary_path: Path | None = None
        try:
            with ZipFile(source, "r") as archive:
                infos = archive.infolist()
                self._validate_archive(archive, infos)
                index = build_epub_index(archive)
                detect_unsupported_encryption(archive, index)

                if "META-INF/signatures.xml" in archive.namelist():
                    raise EpubConversionError(
                        "检测到 META-INF/signatures.xml；转换会使数字签名失效，"
                        "为避免输出携带无效签名，已拒绝处理。"
                    )

                summary = ConversionSummary(
                    total_entries=len(infos),
                    candidate_documents=len(index.documents),
                )
                temporary_path = self._new_temporary_path(destination)
                self._write_converted_archive(
                    archive,
                    infos,
                    index.documents,
                    temporary_path,
                    summary,
                )

            if self._strict and summary.skipped_count:
                skipped = "、".join(summary.skipped_documents)
                raise EpubConversionError(
                    f"严格模式下不允许跳过文档：{skipped}"
                )

            self._verify_output(temporary_path)
            os.replace(temporary_path, destination)
            temporary_path = None
            return summary
        except EpubConversionError:
            raise
        except Exception as exc:
            raise EpubConversionError(f"EPUB 文件处理失败：{exc}") from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _validate_paths(
        source: Path,
        destination: Path,
        overwrite: bool,
    ) -> None:
        if not source.is_file():
            raise EpubConversionError(f"输入文件不存在：{source}")
        if source.resolve() == destination.resolve():
            raise EpubConversionError("输入和输出不能是同一个文件。")
        if destination.exists() and not destination.is_file():
            raise EpubConversionError(f"输出路径不是普通文件：{destination}")
        if destination.exists() and not overwrite:
            raise EpubConversionError(
                f"输出文件已存在：{destination}；如需覆盖请添加 --force。"
            )

    @staticmethod
    def _validate_archive(archive: ZipFile, infos: list[ZipInfo]) -> None:
        if not infos:
            raise EpubConversionError("输入文件是空 ZIP，不是 EPUB。")
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise UnsafeArchiveError("EPUB 的 ZIP 条目数量超过 100000 个安全上限。")

        seen: set[str] = set()
        total_size = 0
        for info in infos:
            name = info.filename
            if name in seen:
                raise UnsafeArchiveError(f"EPUB 含重复 ZIP 条目：{name}")
            seen.add(name)

            if "\x00" in getattr(info, "orig_filename", name):
                raise UnsafeArchiveError("EPUB 的 ZIP 文件名含 NUL 字节。")

            path = PurePosixPath(name)
            if (
                not name
                or "\\" in name
                or name.startswith("/")
                or ".." in path.parts
            ):
                raise UnsafeArchiveError(f"EPUB 含危险 ZIP 路径：{name!r}")

            unix_mode = info.external_attr >> 16
            if unix_mode and stat.S_ISLNK(unix_mode):
                raise UnsafeArchiveError(f"EPUB 不应包含符号链接：{name}")
            if info.flag_bits & 0x1:
                raise EpubConversionError(f"ZIP 条目已加密，无法读取：{name}")
            if info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
                raise EpubConversionError(
                    f"EPUB 条目使用了 OCF 不支持的压缩算法：{name}"
                )
            if any(ord(character) > 127 for character in name) and not (
                info.flag_bits & 0x800
            ):
                raise EpubConversionError(
                    "ZIP 含未标记为 UTF-8 的非 ASCII 文件名，无法在不破坏"
                    f"资源引用的前提下重打包：{name}"
                )
            if info.file_size > MAX_ENTRY_UNCOMPRESSED_SIZE:
                raise UnsafeArchiveError(f"ZIP 条目过大：{name}")
            total_size += info.file_size

        if total_size > MAX_TOTAL_UNCOMPRESSED_SIZE:
            raise UnsafeArchiveError("EPUB 解压后总大小超过 4 GiB 安全限制。")

        if "mimetype" not in seen:
            raise EpubConversionError("输入 EPUB 缺少根目录 mimetype 文件。")
        mimetype_info = archive.getinfo("mimetype")
        input_mimetype = archive.read(mimetype_info)
        if input_mimetype not in COMPATIBLE_EPUB_MIMETYPES:
            raise EpubConversionError(
                "mimetype 内容不是 application/epub+zip，无法识别为 EPUB。"
            )
        if input_mimetype != EPUB_MIMETYPE:
            LOGGER.warning(
                "输入 EPUB 的 mimetype 含末尾换行；输出时将自动规范化。"
            )
        if (
            infos[0].filename != "mimetype"
            or mimetype_info.compress_type != ZIP_STORED
            or bool(mimetype_info.extra)
        ):
            LOGGER.warning(
                "输入 EPUB 的 mimetype 顺序或压缩方式不规范；输出时将自动修复。"
            )

    @staticmethod
    def _new_temporary_path(destination: Path) -> Path:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".part",
            dir=destination.parent,
        )
        os.close(descriptor)
        return Path(name)

    def _write_converted_archive(
        self,
        source_archive: ZipFile,
        infos: list[ZipInfo],
        documents: dict[str, DocumentKind],
        temporary_path: Path,
        summary: ConversionSummary,
    ) -> None:
        mimetype_info = source_archive.getinfo("mimetype")

        with ZipFile(temporary_path, "w", allowZip64=True) as output_archive:
            output_archive.comment = source_archive.comment
            output_archive.writestr(
                self._make_mimetype_info(mimetype_info),
                EPUB_MIMETYPE,
            )

            for info in infos:
                if info.filename == "mimetype":
                    continue

                kind = documents.get(info.filename)
                if kind is None:
                    self._copy_member_streaming(
                        source_archive,
                        output_archive,
                        info,
                    )
                    continue

                if info.file_size > MAX_TEXT_DOCUMENT_SIZE:
                    if kind is DocumentKind.PACKAGE:
                        raise EpubConversionError(
                            f"核心 OPF 超过 64 MiB 安全上限：{info.filename}"
                        )
                    summary.skipped_documents.append(info.filename)
                    LOGGER.warning(
                        "跳过超过 64 MiB 的文本文档，已原样复制：%s",
                        info.filename,
                    )
                    self._copy_member_streaming(
                        source_archive,
                        output_archive,
                        info,
                    )
                    continue

                original_data = source_archive.read(info)
                output_data = original_data

                try:
                    result = self._transformer.transform(
                        original_data,
                        kind,
                        info.filename,
                    )
                except DocumentParseError as exc:
                    if kind is DocumentKind.PACKAGE:
                        raise EpubConversionError(
                            f"核心 OPF 转换失败：{exc}"
                        ) from exc
                    summary.skipped_documents.append(info.filename)
                    LOGGER.warning(
                        "跳过无法解析的文档，已保留原始字节：%s（%s）",
                        info.filename,
                        exc,
                    )
                else:
                    output_data = result.data
                    summary.converted_nodes += result.changed_nodes
                    if output_data != original_data:
                        summary.changed_documents += 1
                    if result.layout_changed:
                        summary.layout_documents += 1
                    LOGGER.debug(
                        "已处理 %s：改变 %d 个文本/元数据节点",
                        info.filename,
                        result.changed_nodes,
                    )

                output_archive.writestr(copy.copy(info), output_data)

    @staticmethod
    def _copy_member_streaming(
        source_archive: ZipFile,
        output_archive: ZipFile,
        info: ZipInfo,
    ) -> None:
        """分块复制未修改资源，避免大图片、字体或音频占满内存。"""

        target_info = copy.copy(info)
        if info.is_dir():
            output_archive.writestr(target_info, b"")
            return

        with source_archive.open(info, "r") as source_file:
            with output_archive.open(target_info, "w") as output_file:
                shutil.copyfileobj(
                    source_file,
                    output_file,
                    length=COPY_BUFFER_SIZE,
                )

    @staticmethod
    def _make_mimetype_info(original: ZipInfo) -> ZipInfo:
        """创建符合 OCF 要求的首个、无 extra、未压缩 mimetype 条目。"""

        info = ZipInfo("mimetype", date_time=original.date_time)
        info.compress_type = ZIP_STORED
        info.comment = original.comment
        info.extra = b""
        info.create_system = original.create_system
        info.external_attr = original.external_attr
        info.internal_attr = original.internal_attr
        return info

    @staticmethod
    def _verify_output(path: Path) -> None:
        """在覆盖目标前验证 ZIP 可读性与 EPUB mimetype 封装。"""

        try:
            with ZipFile(path, "r") as archive:
                infos = archive.infolist()
                if not infos or infos[0].filename != "mimetype":
                    raise EpubConversionError("输出 EPUB 的首个条目不是 mimetype。")
                mimetype_info = infos[0]
                if mimetype_info.compress_type != ZIP_STORED:
                    raise EpubConversionError("输出 EPUB 的 mimetype 被压缩。")
                if mimetype_info.extra:
                    raise EpubConversionError("输出 EPUB 的 mimetype 含 extra 字段。")
                if archive.read(mimetype_info) != EPUB_MIMETYPE:
                    raise EpubConversionError("输出 EPUB 的 mimetype 内容错误。")
                broken_member = archive.testzip()
                if broken_member is not None:
                    raise EpubConversionError(
                        f"输出 EPUB 的 ZIP 校验失败：{broken_member}"
                    )
        except BadZipFile as exc:
            raise EpubConversionError(f"输出文件不是有效 ZIP：{exc}") from exc
