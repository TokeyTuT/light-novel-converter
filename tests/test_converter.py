"""转换器端到端测试。"""

from __future__ import annotations

import copy
import hashlib
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from zipfile import ZIP_BZIP2, ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest
from lxml import etree

from epub_tw_converter.converter import EPUB_MIMETYPE, EpubConverter
from epub_tw_converter.documents import VERTICAL_STYLE_ID
from epub_tw_converter.errors import EpubConversionError
from epub_tw_converter import index as index_module


def parse_xml_from_epub(archive: ZipFile, name: str) -> etree._Element:
    """读取并解析输出 EPUB 中的 XML。"""

    return etree.fromstring(archive.read(name))


def by_local_name(root: etree._Element, name: str) -> list[etree._Element]:
    """忽略 namespace prefix 查找元素。"""

    return root.xpath(f'//*[local-name()="{name}"]')


def digest(data: bytes) -> str:
    """计算资源完整性断言使用的 SHA-256。"""

    return hashlib.sha256(data).hexdigest()


def add_epub_member(
    source_path: Path,
    output_path: Path,
    name: str,
    data: bytes,
) -> None:
    """复制测试 EPUB，并追加一个 META-INF 条目。"""

    with ZipFile(source_path) as source, ZipFile(output_path, "w") as target:
        for info in source.infolist():
            target.writestr(copy.copy(info), source.read(info))
        target.writestr(name, data, compress_type=ZIP_DEFLATED)


def test_full_epub_conversion_and_binary_integrity(
    tmp_path: Path,
    epub_factory: Callable[..., tuple[Path, dict[str, bytes]]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    input_path, originals = epub_factory()
    output_path = tmp_path / "output.epub"

    summary = EpubConverter().convert(input_path, output_path)

    assert summary.skipped_documents == ["OEBPS/Text/broken.xhtml"]
    assert "保留原始字节" in caplog.text
    assert summary.converted_nodes > 0

    with ZipFile(input_path) as source_archive, ZipFile(output_path) as archive:
        infos = archive.infolist()
        assert infos[0].filename == "mimetype"
        assert infos[0].compress_type == ZIP_STORED
        assert infos[0].extra == b""
        assert archive.read("mimetype") == EPUB_MIMETYPE
        assert archive.testzip() is None

        # 图片、字体、CSS、坏页面都必须保持逐字节一致。
        for name in (
            "OEBPS/Images/封面.jpg",
            "OEBPS/Fonts/书体.otf",
            "OEBPS/Styles/book.css",
            "OEBPS/Text/broken.xhtml",
        ):
            assert digest(archive.read(name)) == digest(originals[name])

        source_cover_info = source_archive.getinfo("OEBPS/Images/封面.jpg")
        output_cover_info = archive.getinfo("OEBPS/Images/封面.jpg")
        assert output_cover_info.date_time == source_cover_info.date_time
        assert output_cover_info.comment == source_cover_info.comment
        assert output_cover_info.extra == source_cover_info.extra
        assert output_cover_info.external_attr == source_cover_info.external_attr
        assert output_cover_info.compress_type == source_cover_info.compress_type

        chapter = parse_xml_from_epub(archive, "OEBPS/Text/chapter.xhtml")
        paragraphs = by_local_name(chapter, "p")
        assert len(paragraphs) == 1
        assert "頭髮與程式網路、軟體（測試）" == "".join(
            paragraphs[0].itertext()
        )
        assert paragraphs[0].get("id") == "程序-id"

        image = by_local_name(chapter, "img")[0]
        assert image.get("src") == "../Images/封面.jpg"
        assert image.get("alt") == "網路封面"
        assert image.get("title") == "軟體插圖"

        scripts = by_local_name(chapter, "script")
        original_styles = [
            item
            for item in by_local_name(chapter, "style")
            if item.get("id") != VERTICAL_STYLE_ID
        ]
        assert scripts[0].text == 'const 程序 = "网络";'
        assert "网络" in (original_styles[0].text or "")

        vertical_styles = chapter.xpath(
            f'//*[local-name()="style" and @id="{VERTICAL_STYLE_ID}"]'
        )
        assert len(vertical_styles) == 1
        css = vertical_styles[0].text or ""
        assert "-webkit-writing-mode: vertical-rl !important" in css
        assert "-epub-writing-mode: vertical-rl !important" in css
        assert "writing-mode: vertical-rl !important" in css
        assert "text-orientation: mixed !important" in css
        assert chapter.get("lang") is None
        assert chapter.get("{http://www.w3.org/XML/1998/namespace}lang") == "zh-TW"

        nav = parse_xml_from_epub(archive, "OEBPS/nav.xhtml")
        assert "程式與網路" in "".join(nav.itertext())
        assert by_local_name(nav, "a")[0].get("href") == "Text/chapter.xhtml"

        ncx = parse_xml_from_epub(archive, "OEBPS/toc.ncx")
        ncx_text = "|".join(
            (element.text or "") for element in by_local_name(ncx, "text")
        )
        assert "程式小說" in ncx_text
        assert "網路作者" in ncx_text
        assert "頭髮與軟體" in ncx_text
        assert by_local_name(ncx, "content")[0].get("src") == (
            "Text/chapter.xhtml#程序-id"
        )

        package = parse_xml_from_epub(archive, "OEBPS/content.opf")
        assert by_local_name(package, "identifier")[0].text == (
            "urn:uuid:程序-identifier"
        )
        assert by_local_name(package, "title")[0].text == "程式與網路"
        assert by_local_name(package, "title")[0].get(
            "{http://www.w3.org/XML/1998/namespace}lang"
        ) == "zh-TW"
        assert by_local_name(package, "creator")[0].text == "網路作者"
        assert by_local_name(package, "creator")[0].get("file-as") == "網路作者"
        assert by_local_name(package, "description")[0].text == "頭髮與軟體"
        assert by_local_name(package, "language")[0].text == "zh-TW"
        assert package.get(
            "{http://www.w3.org/XML/1998/namespace}lang"
        ) == "zh-TW"
        assert by_local_name(package, "spine")[0].get(
            "page-progression-direction"
        ) == "rtl"

        modified = package.xpath(
            '//*[local-name()="meta" and @property="dcterms:modified"]'
        )
        assert len(modified) == 1
        assert re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
            modified[0].text or "",
        )

        # 结构与路径完全保留，输出仅重排了 mimetype 到第一项。
        assert set(archive.namelist()) == {"mimetype", *originals}


def test_mimetype_is_repaired_when_input_order_is_wrong(
    tmp_path: Path,
    epub_factory: Callable[..., tuple[Path, dict[str, bytes]]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    input_path, _ = epub_factory(
        include_broken=False,
        mimetype_first=False,
        mimetype_compression=8,
    )
    output_path = tmp_path / "fixed.epub"

    EpubConverter().convert(input_path, output_path)

    assert "自动修复" in caplog.text
    with ZipFile(output_path) as archive:
        first = archive.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == ZIP_STORED


def test_epub2_does_not_receive_epub3_page_direction(
    tmp_path: Path,
    epub_factory: Callable[..., tuple[Path, dict[str, bytes]]],
) -> None:
    input_path, _ = epub_factory(version="2.0", include_broken=False)
    output_path = tmp_path / "epub2.epub"

    EpubConverter().convert(input_path, output_path)

    with ZipFile(output_path) as archive:
        package = parse_xml_from_epub(archive, "OEBPS/content.opf")
        assert (
            by_local_name(package, "spine")[0].get(
                "page-progression-direction"
            )
            is None
        )


def test_vertical_style_is_not_duplicated_on_second_run(
    tmp_path: Path,
    epub_factory: Callable[..., tuple[Path, dict[str, bytes]]],
) -> None:
    input_path, _ = epub_factory(include_broken=False)
    first_output = tmp_path / "first.epub"
    second_output = tmp_path / "second.epub"

    EpubConverter().convert(input_path, first_output)
    EpubConverter().convert(first_output, second_output)

    with ZipFile(second_output) as archive:
        chapter = parse_xml_from_epub(archive, "OEBPS/Text/chapter.xhtml")
        styles = chapter.xpath(
            f'//*[local-name()="style" and @id="{VERTICAL_STYLE_ID}"]'
        )
        assert len(styles) == 1


def test_strict_mode_preserves_existing_destination(
    tmp_path: Path,
    epub_factory: Callable[..., tuple[Path, dict[str, bytes]]],
) -> None:
    input_path, _ = epub_factory(include_broken=True)
    output_path = tmp_path / "existing.epub"
    output_path.write_bytes(b"old-output")

    with pytest.raises(EpubConversionError, match="严格模式"):
        EpubConverter(strict=True).convert(
            input_path,
            output_path,
            overwrite=True,
        )

    assert output_path.read_bytes() == b"old-output"
    assert not list(tmp_path.glob(".existing.epub.*.part"))


def test_cli_supports_documented_invocation(
    tmp_path: Path,
    epub_factory: Callable[..., tuple[Path, dict[str, bytes]]],
) -> None:
    input_path, _ = epub_factory(include_broken=False)
    output_path = tmp_path / "cli-output.epub"
    project_root = Path(__file__).resolve().parents[1]

    process = subprocess.run(
        [
            sys.executable,
            str(project_root / "convert.py"),
            str(input_path),
            str(output_path),
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 0, process.stderr
    assert output_path.is_file()
    assert "转换完成" in process.stderr


def test_input_and_output_must_differ(
    epub_factory: Callable[..., tuple[Path, dict[str, bytes]]],
) -> None:
    input_path, _ = epub_factory(include_broken=False)

    with pytest.raises(EpubConversionError, match="同一个文件"):
        EpubConverter().convert(input_path, input_path, overwrite=True)


def test_non_ocf_compression_is_rejected(
    tmp_path: Path,
    epub_factory: Callable[..., tuple[Path, dict[str, bytes]]],
) -> None:
    input_path, _ = epub_factory(include_broken=False)
    bzip_path = tmp_path / "bzip2.epub"
    output_path = tmp_path / "should-not-exist.epub"

    with ZipFile(input_path) as source, ZipFile(bzip_path, "w") as target:
        for info in source.infolist():
            compression = (
                ZIP_BZIP2
                if info.filename == "OEBPS/Styles/book.css"
                else info.compress_type
            )
            target.writestr(
                info.filename,
                source.read(info),
                compress_type=compression,
            )

    with pytest.raises(EpubConversionError, match="不支持的压缩算法"):
        EpubConverter().convert(bzip_path, output_path)

    assert not output_path.exists()


def test_font_obfuscation_algorithm_cannot_target_xhtml(
    tmp_path: Path,
    epub_factory: Callable[..., tuple[Path, dict[str, bytes]]],
) -> None:
    input_path, _ = epub_factory(include_broken=False)
    encrypted_path = tmp_path / "encrypted-chapter.epub"
    output_path = tmp_path / "encrypted-output.epub"
    encryption = b"""<?xml version="1.0" encoding="UTF-8"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
 xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
  <enc:EncryptedData>
    <enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>
    <enc:CipherData>
      <enc:CipherReference URI="OEBPS/Text/chapter.xhtml"/>
    </enc:CipherData>
  </enc:EncryptedData>
</encryption>"""
    add_epub_member(
        input_path,
        encrypted_path,
        "META-INF/encryption.xml",
        encryption,
    )

    with pytest.raises(EpubConversionError, match="只能用于.*字体"):
        EpubConverter().convert(encrypted_path, output_path)

    assert not output_path.exists()


def test_valid_font_obfuscation_is_preserved(
    tmp_path: Path,
    epub_factory: Callable[..., tuple[Path, dict[str, bytes]]],
) -> None:
    input_path, originals = epub_factory(include_broken=False)
    encrypted_path = tmp_path / "font-obfuscated.epub"
    output_path = tmp_path / "font-output.epub"
    encryption = """<?xml version="1.0" encoding="UTF-8"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
 xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
  <enc:EncryptedData>
    <enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>
    <enc:CipherData>
      <enc:CipherReference URI="OEBPS/Fonts/%E4%B9%A6%E4%BD%93.otf"/>
    </enc:CipherData>
  </enc:EncryptedData>
</encryption>""".encode()
    add_epub_member(
        input_path,
        encrypted_path,
        "META-INF/encryption.xml",
        encryption,
    )

    EpubConverter().convert(encrypted_path, output_path)

    with ZipFile(output_path) as archive:
        assert digest(archive.read("OEBPS/Fonts/书体.otf")) == digest(
            originals["OEBPS/Fonts/书体.otf"]
        )
        package = parse_xml_from_epub(archive, "OEBPS/content.opf")
        assert by_local_name(package, "identifier")[0].text == (
            "urn:uuid:程序-identifier"
        )


def test_signed_epub_is_rejected_instead_of_copying_invalid_signature(
    tmp_path: Path,
    epub_factory: Callable[..., tuple[Path, dict[str, bytes]]],
) -> None:
    input_path, _ = epub_factory(include_broken=False)
    signed_path = tmp_path / "signed.epub"
    output_path = tmp_path / "signed-output.epub"
    add_epub_member(
        input_path,
        signed_path,
        "META-INF/signatures.xml",
        b'<?xml version="1.0"?><signatures/>',
    )

    with pytest.raises(EpubConversionError, match="数字签名失效"):
        EpubConverter().convert(signed_path, output_path)

    assert not output_path.exists()


def test_core_xml_size_is_checked_before_parsing(
    tmp_path: Path,
    epub_factory: Callable[..., tuple[Path, dict[str, bytes]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path, _ = epub_factory(include_broken=False)
    output_path = tmp_path / "oversized-core-output.epub"
    monkeypatch.setattr(index_module, "MAX_CORE_XML_SIZE", 128)

    with pytest.raises(EpubConversionError, match="container.xml"):
        EpubConverter().convert(input_path, output_path)

    assert not output_path.exists()
