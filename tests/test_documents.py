"""文档序列化与旧式 HTML 的边界回归测试。"""

from __future__ import annotations

import re
from time import perf_counter

import pytest
from lxml import etree

from epub_tw_converter.documents import (
    ILLUSTRATION_PAGE_CLASS,
    ILLUSTRATION_PAGE_CSS,
    ILLUSTRATION_PAGE_STYLE_ID,
    VERTICAL_STYLE_ID,
    DocumentTransformer,
)
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


@pytest.mark.parametrize("image_count", [1, 4])
def test_image_only_xhtml_keeps_original_image_composition(
    transformer: DocumentTransformer,
    image_count: int,
) -> None:
    """单图封面与多切片彩页不应被竖排样式改变几何排列。"""

    image_markup = "\n".join(
        f'<img src="../Images/cy{index}.jpg" alt="网络插画" '
        f'title="软件彩图" width="{600 + index}" height="1600" '
        f'style="vertical-align: top" data-index="{index}" />'
        for index in range(1, image_count + 1)
    )
    source = f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-CN">
<head>
  <title>网络彩页</title>
  <style id="author-style">img {{ margin: 0; }}</style>
</head>
<body>&#160;<div>&#8203;{image_markup}</div></body>
</html>
""".encode()

    result = transformer.transform(
        source,
        DocumentKind.XHTML,
        "illustration.xhtml",
    )

    root = etree.fromstring(result.data)
    vertical_styles = root.xpath(
        f'//*[local-name()="style" and @id="{VERTICAL_STYLE_ID}"]'
    )
    assert vertical_styles == []

    author_style = root.xpath(
        '//*[local-name()="style" and @id="author-style"]'
    )[0]
    assert author_style.text == "img { margin: 0; }"

    images = root.xpath('//*[local-name()="body"]//*[local-name()="img"]')
    assert [image.get("src") for image in images] == [
        f"../Images/cy{index}.jpg"
        for index in range(1, image_count + 1)
    ]
    assert [image.get("data-index") for image in images] == [
        str(index) for index in range(1, image_count + 1)
    ]
    assert [image.get("width") for image in images] == [
        str(600 + index) for index in range(1, image_count + 1)
    ]
    assert all(image.get("height") == "1600" for image in images)
    assert all(image.get("style") == "vertical-align: top" for image in images)
    assert all(image.get("alt") == "網路插畫" for image in images)
    assert all(image.get("title") == "軟體彩圖" for image in images)
    assert root.get("{http://www.w3.org/XML/1998/namespace}lang") == "zh-TW"

    page_styles = root.xpath(
        f'//*[local-name()="head"]//*['
        f'local-name()="style" and @id="{ILLUSTRATION_PAGE_STYLE_ID}"]'
    )
    if image_count == 1:
        assert page_styles == []
        assert images[0].get("class") is None
    else:
        assert len(page_styles) == 1
        assert page_styles[0].text == ILLUSTRATION_PAGE_CSS
        assert [image.get("class") for image in images] == [
            None,
            *[ILLUSTRATION_PAGE_CLASS] * (image_count - 1),
        ]


def test_illustration_after_prose_starts_on_a_new_page(
    transformer: DocumentTransformer,
) -> None:
    """文字之后的插画应从新页开始，但正文仍使用竖排。"""

    source = """<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>章节</title></head>
<body><p>程序网络</p><img src="../Images/c1.jpg" alt="插画" /></body>
</html>""".encode()

    result = transformer.transform(
        source,
        DocumentKind.XHTML,
        "chapter-with-illustration.xhtml",
    )

    root = etree.fromstring(result.data)
    image = root.xpath('//*[local-name()="img"]')[0]
    assert image.get("class") == ILLUSTRATION_PAGE_CLASS
    page_style = root.xpath(
        f'//*[local-name()="head"]//*['
        f'local-name()="style" and @id="{ILLUSTRATION_PAGE_STYLE_ID}"]'
    )[0]
    assert page_style.text == ILLUSTRATION_PAGE_CSS
    assert root.xpath(
        f'//*[local-name()="head"]//*['
        f'local-name()="style" and @id="{VERTICAL_STYLE_ID}"]'
    )


def test_inline_svg_only_page_preserves_svg_geometry(
    transformer: DocumentTransformer,
) -> None:
    """纯 SVG 插画页保留大小写敏感属性与资源引用。"""

    source = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:svg="http://www.w3.org/2000/svg"
      xmlns:xlink="http://www.w3.org/1999/xlink">
<head><title>网络插画</title></head>
<body>
  <svg:svg width="1200" height="1600" viewBox="0 0 1200 1600"
           preserveAspectRatio="xMidYMid meet">
    <svg:style id="lnc-vertical-style">.frame { fill: none; }</svg:style>
    <svg:title>网络图片</svg:title>
    <svg:desc>软件说明</svg:desc>
    <svg:image width="1200" height="1600"
               xlink:href="../Images/程序-cover.jpg" />
    <svg:text x="10" y="20">程序网络</svg:text>
  </svg:svg>
</body>
</html>
""".encode()

    result = transformer.transform(
        source,
        DocumentKind.XHTML,
        "svg-illustration.xhtml",
    )

    root = etree.fromstring(result.data)
    assert not root.xpath(
        f'//*[local-name()="head"]//*['
        f'local-name()="style" and @id="{VERTICAL_STYLE_ID}"]'
    )
    svg = root.xpath('//*[local-name()="svg"]')[0]
    assert svg.get("width") == "1200"
    assert svg.get("height") == "1600"
    assert svg.get("viewBox") == "0 0 1200 1600"
    assert svg.get("preserveAspectRatio") == "xMidYMid meet"
    svg_style = root.xpath(
        '//*[local-name()="svg"]/*[local-name()="style"]'
    )[0]
    assert svg_style.get("id") == VERTICAL_STYLE_ID
    assert svg_style.text == ".frame { fill: none; }"
    image = root.xpath('//*[local-name()="svg"]/*[local-name()="image"]')[0]
    assert image.get("{http://www.w3.org/1999/xlink}href") == (
        "../Images/程序-cover.jpg"
    )
    assert "程式網路" in "".join(root.itertext())


def test_old_vertical_style_is_removed_from_image_only_page_idempotently(
    transformer: DocumentTransformer,
) -> None:
    """重新处理旧输出时移除错误样式，并保留作者自有 CSS。"""

    source = f"""<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-CN">
<head>
  <title>封面</title>
  <style id="author-style">img {{ display: inline; }}</style>
  <style id="{VERTICAL_STYLE_ID}" type="text/css">
    html, body {{ writing-mode: vertical-rl !important; }}
  </style>
</head>
<body><img src="../Images/cover.jpg" alt="网络封面" /></body>
</html>""".encode()

    first = transformer.transform(source, DocumentKind.XHTML, "cover.xhtml")
    second = transformer.transform(
        first.data,
        DocumentKind.XHTML,
        "cover.xhtml",
    )

    assert first.layout_changed is True
    assert second.layout_changed is False
    assert first.data == second.data
    root = etree.fromstring(second.data)
    assert not root.xpath(
        f'//*[local-name()="style" and @id="{VERTICAL_STYLE_ID}"]'
    )
    author_style = root.xpath(
        '//*[local-name()="style" and @id="author-style"]'
    )[0]
    assert author_style.text == "img { display: inline; }"
    image = root.xpath('//*[local-name()="img"]')[0]
    assert image.get("src") == "../Images/cover.jpg"
    assert image.get("alt") == "網路封面"


def test_template_text_is_converted_without_affecting_static_image_layout(
    transformer: DocumentTransformer,
) -> None:
    """template 当前不可见，但其中文仍须完成转换。"""

    source = """<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>彩页</title></head>
<body>
  <img src="../Images/cover.jpg" alt="封面" />
  <template><p>程序网络软件</p></template>
</body>
</html>""".encode()

    result = transformer.transform(
        source,
        DocumentKind.XHTML,
        "template-illustration.xhtml",
    )

    root = etree.fromstring(result.data)
    assert not root.xpath(
        f'//*[local-name()="head"]/*['
        f'local-name()="style" and @id="{VERTICAL_STYLE_ID}"]'
    )
    template = root.xpath('//*[local-name()="template"]')[0]
    assert "".join(template.itertext()) == "程式網路軟體"


def test_unresolved_entity_prevents_image_only_classification(
    transformer: DocumentTransformer,
) -> None:
    """实体内容未展开时按可见正文保守处理。"""

    source = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html [<!ENTITY caption "程序网络">]>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>插画</title></head>
<body><img src="../Images/cover.jpg" />&caption;</body>
</html>""".encode()

    result = transformer.transform(
        source,
        DocumentKind.XHTML,
        "entity-caption.xhtml",
    )

    root = etree.fromstring(result.data)
    assert root.xpath(
        f'//*[local-name()="head"]/*['
        f'local-name()="style" and @id="{VERTICAL_STYLE_ID}"]'
    )


def test_nonbreaking_space_entity_is_ignored_on_image_only_page(
    transformer: DocumentTransformer,
) -> None:
    """XHTML 常见 &nbsp; 不应让纯图页重新获得竖排样式。"""

    source = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html [<!ENTITY nbsp "&#160;">]>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>插画</title></head>
<body><img src="../Images/cover.jpg" />&nbsp;</body>
</html>""".encode()

    result = transformer.transform(
        source,
        DocumentKind.XHTML,
        "entity-whitespace.xhtml",
    )

    root = parse_xml(result.data, "entity-whitespace.xhtml").getroot()
    assert not root.xpath(
        f'//*[local-name()="head"]/*['
        f'local-name()="style" and @id="{VERTICAL_STYLE_ID}"]'
    )
