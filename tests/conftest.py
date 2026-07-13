"""测试用 EPUB fixture。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest

MIMETYPE = b"application/epub+zip"
IMAGE_BYTES = b"\xff\xd8\xff\xe0fake-jpeg-binary\xff\xd9"
FONT_BYTES = b"OTTO\x00\x01fake-font-binary\x00\xff"

CONTAINER_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0"
 xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf"
      media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

CHAPTER_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-CN"
      xml:lang="zh-CN">
  <head>
    <title>程序与网络</title>
    <style>.网络 { content: "程序"; }</style>
    <script><![CDATA[const 程序 = "网络";]]></script>
  </head>
  <body>
    <p id="程序-id">头发与<em>程序</em>网络、软件（测试）</p>
    <img src="../Images/封面.jpg" alt="网络封面" title="软件插图"/>
  </body>
</html>
""".encode()

NAV_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh-CN">
  <head><title>小说目录</title></head>
  <body><nav epub:type="toc"><ol><li><a href="Text/chapter.xhtml">程序与网络</a></li></ol></nav></body>
</html>
""".encode()

NCX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"
     xml:lang="zh-CN">
  <head><meta name="dtb:uid" content="urn:uuid:test"/></head>
  <docTitle><text>程序小说</text></docTitle>
  <docAuthor><text>网络作者</text></docAuthor>
  <navMap>
    <navPoint id="程序-id" playOrder="1">
      <navLabel><text>头发与软件</text></navLabel>
      <content src="Text/chapter.xhtml#程序-id"/>
    </navPoint>
  </navMap>
</ncx>
""".encode()

BROKEN_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>坏页面</title></head>
<body><p>程序与网络</body></html>
""".encode()


def package_document(
    version: str = "3.0",
    include_broken: bool = True,
    spine_direction: str | None = None,
) -> bytes:
    """构造包含 EPUB 2 NCX 与 EPUB 3 nav 的 OPF。"""

    broken_item = (
        '<item id="broken" href="Text/broken.xhtml" '
        'media-type="application/xhtml+xml"/>'
        if include_broken
        else ""
    )
    is_epub3 = version.startswith("3")
    modified_meta = (
        '<meta property="dcterms:modified">2000-01-01T00:00:00Z</meta>'
        if is_epub3
        else ""
    )
    nav_properties = ' properties="nav"' if is_epub3 else ""
    direction_attribute = (
        f' page-progression-direction="{spine_direction}"'
        if spine_direction is not None
        else ""
    )
    package = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="{version}"
         unique-identifier="book-id" xml:lang="zh-CN">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">urn:uuid:程序-identifier</dc:identifier>
    <dc:title xml:lang="zh-Hans">程序与网络</dc:title>
    <dc:creator file-as="网络作者">网络作者</dc:creator>
    <dc:description>头发与软件</dc:description>
    <dc:language>zh-CN</dc:language>
    {modified_meta}
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"{nav_properties}/>
    <item id="chapter" href="Text/chapter.xhtml"
          media-type="application/xhtml+xml"/>
    {broken_item}
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="cover" href="Images/封面.jpg" media-type="image/jpeg"/>
    <item id="font" href="Fonts/书体.otf" media-type="font/otf"/>
    <item id="css" href="Styles/book.css" media-type="text/css"/>
  </manifest>
  <spine toc="ncx"{direction_attribute}><itemref idref="chapter"/></spine>
</package>
"""
    return package.encode()


def write_epub(
    path: Path,
    *,
    version: str = "3.0",
    include_broken: bool = True,
    mimetype_first: bool = True,
    mimetype_compression: int = ZIP_STORED,
    mimetype_data: bytes = MIMETYPE,
    spine_direction: str | None = None,
) -> dict[str, bytes]:
    """写入最小但结构完整的测试 EPUB。"""

    resources = {
        "META-INF/container.xml": CONTAINER_XML,
        "OEBPS/content.opf": package_document(
            version,
            include_broken,
            spine_direction,
        ),
        "OEBPS/nav.xhtml": NAV_XHTML,
        "OEBPS/toc.ncx": NCX_XML,
        "OEBPS/Text/chapter.xhtml": CHAPTER_XHTML,
        "OEBPS/Styles/book.css": b"body { font-family: NovelFont; }",
        "OEBPS/Images/封面.jpg": IMAGE_BYTES,
        "OEBPS/Fonts/书体.otf": FONT_BYTES,
    }
    if include_broken:
        resources["OEBPS/Text/broken.xhtml"] = BROKEN_XHTML

    with ZipFile(path, "w") as archive:
        if mimetype_first:
            archive.writestr(
                "mimetype",
                mimetype_data,
                compress_type=mimetype_compression,
            )
        for name, data in resources.items():
            if name == "OEBPS/Images/封面.jpg":
                info = ZipInfo(name, date_time=(2024, 1, 2, 3, 4, 6))
                info.compress_type = ZIP_STORED
                info.comment = b"cover-resource"
                info.extra = b"\xfe\xca\x04\x00test"
                info.external_attr = 0o100640 << 16
                archive.writestr(info, data)
            else:
                archive.writestr(name, data, compress_type=ZIP_DEFLATED)
        if not mimetype_first:
            archive.writestr(
                "mimetype",
                mimetype_data,
                compress_type=mimetype_compression,
            )
    return resources


@pytest.fixture
def epub_factory(tmp_path: Path) -> Callable[..., tuple[Path, dict[str, bytes]]]:
    """返回可按测试参数创建 EPUB 的工厂。"""

    counter = 0

    def factory(**kwargs: object) -> tuple[Path, dict[str, bytes]]:
        nonlocal counter
        counter += 1
        path = tmp_path / f"input-{counter}.epub"
        resources = write_epub(path, **kwargs)
        return path, resources

    return factory
