"""文档序列化与旧式 HTML 的边界回归测试。"""

from __future__ import annotations

import re
from time import perf_counter

import pytest
from lxml import etree

from epub_tw_converter.documents import DocumentTransformer
from epub_tw_converter.errors import DocumentParseError
from epub_tw_converter.models import DocumentKind
from epub_tw_converter.text import TaiwanTextConverter
from epub_tw_converter.xml_utils import parse_xml


@pytest.fixture
def transformer() -> DocumentTransformer:
    """创建使用真实 s2twp 配置的文档转换器。"""

    return DocumentTransformer(TaiwanTextConverter("s2twp"))


def test_xml_internal_dtd_subset_is_preserved(
    transformer: DocumentTransformer,
) -> None:
    source = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html [<!ENTITY keep "&#160;">]>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>程序目录</title></head>
  <body><p>程序&keep;网络</p></body>
</html>
""".encode()

    result = transformer.transform(
        source,
        DocumentKind.XHTML,
        "chapter.xhtml",
    )

    output_text = result.data.decode("utf-8")
    assert '<!ENTITY keep "&#160;">' in output_text
    assert "&keep;" in output_text
    assert "程式" in output_text
    assert "網路" in output_text
    # 若 internal subset 被截掉，这一步会以“Entity not defined”失败。
    parse_xml(result.data, "converted.xhtml")


def test_legacy_html_charset_is_updated_to_utf8(
    transformer: DocumentTransformer,
) -> None:
    source_text = """<!DOCTYPE html>
<html><head>
<meta http-equiv="Content-Type" content="text/html; charset=gb2312">
<title>程序网络</title>
</head><body><p>头发与软件</p></body></html>
"""
    source = source_text.encode("gb2312")

    result = transformer.transform(
        source,
        DocumentKind.HTML,
        "legacy.html",
    )

    output_text = result.data.decode("utf-8")
    assert re.search(r"charset=utf-8", output_text, flags=re.IGNORECASE)
    assert "程式網路" in output_text
    assert "頭髮與軟體" in output_text
    etree.fromstring(result.data, etree.HTMLParser())


def test_non_xml_html_with_svg_is_skipped_safely(
    transformer: DocumentTransformer,
) -> None:
    # 未闭合 meta 使它只能走旧式 HTML parser；该 parser 会将 viewBox
    # 小写化，因此转换器必须拒绝序列化并让上层原样保留。
    source = b"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head><body>
<svg viewBox="0 0 10 10"><path d="M0 0L10 10"></path></svg>
</body></html>
"""

    with pytest.raises(DocumentParseError, match="SVG/MathML"):
        transformer.transform(source, DocumentKind.HTML, "foreign.html")


def test_opencc_keeps_context_across_inline_elements(
    transformer: DocumentTransformer,
) -> None:
    source = """<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>测试</title></head><body>
<p>头<em>发</em>与程<em>序</em>和网<span>络</span></p>
</body></html>""".encode()

    result = transformer.transform(
        source,
        DocumentKind.XHTML,
        "split-words.xhtml",
    )

    root = etree.fromstring(result.data)
    paragraph = root.xpath('//*[local-name()="p"]')[0]
    assert "".join(paragraph.itertext()) == "頭髮與程式和網路"


def test_non_xml_html_has_one_consistent_language_attribute(
    transformer: DocumentTransformer,
) -> None:
    source = b"""<!DOCTYPE html>
<html xml:lang="zh-CN"><head><meta charset="utf-8"></head>
<body><p>program</p><br></body></html>"""

    result = transformer.transform(
        source,
        DocumentKind.HTML,
        "language.html",
    )

    output_text = result.data.decode("utf-8")
    assert output_text.count("xml:lang") == 0
    parsed = etree.fromstring(result.data, etree.HTMLParser())
    assert parsed.get("lang") == "zh-TW"


def test_well_formed_text_html_gets_utf8_meta(
    transformer: DocumentTransformer,
) -> None:
    source = """<html><head><title>程序</title></head>
<body><p>网络</p><script/></body></html>""".encode()

    result = transformer.transform(
        source,
        DocumentKind.HTML,
        "xml-like.html",
    )

    output_text = result.data.decode("utf-8")
    assert '<meta charset="utf-8">' in output_text
    assert "<script></script>" in output_text
    assert "程式" in output_text
    assert "網路" in output_text


def test_length_changing_phrase_across_inline_elements(
    transformer: DocumentTransformer,
) -> None:
    source = """<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>测试</title></head><body><p>内<em>存</em></p></body></html>""".encode()

    result = transformer.transform(
        source,
        DocumentKind.XHTML,
        "longer-output.xhtml",
    )

    root = etree.fromstring(result.data)
    paragraph = root.xpath('//*[local-name()="p"]')[0]
    assert "".join(paragraph.itertext()) == "記憶體"


def test_length_change_boundary_alignment_is_linear() -> None:
    source = "啊" * 100_000 + "内存" + "啊" * 100_000
    converted = "啊" * 100_000 + "記憶體" + "啊" * 100_000
    started = perf_counter()

    boundaries = DocumentTransformer._align_slot_boundaries(
        source,
        converted,
        [100_000, 1, 1, 100_000],
    )

    assert boundaries[0] == 0
    assert boundaries[-1] == len(converted)
    assert boundaries == sorted(boundaries)
    assert perf_counter() - started < 1.0


def test_ruby_annotations_do_not_break_base_text_context(
    transformer: DocumentTransformer,
) -> None:
    source = """<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>测试</title></head><body>
<p><ruby>头<rt>tou</rt>发<rt>fa</rt></ruby></p>
</body></html>""".encode()

    result = transformer.transform(
        source,
        DocumentKind.XHTML,
        "ruby.xhtml",
    )

    root = etree.fromstring(result.data)
    ruby = root.xpath('//*[local-name()="ruby"]')[0]
    annotations = ruby.xpath('./*[local-name()="rt"]')
    base_text = (ruby.text or "") + "".join(
        annotation.tail or "" for annotation in annotations
    )
    assert base_text == "頭髮"
    assert [annotation.text for annotation in annotations] == ["tou", "fa"]
