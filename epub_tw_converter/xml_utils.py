"""安全 XML 解析与命名空间辅助函数。"""

from __future__ import annotations

from io import BytesIO

from lxml import etree

from .errors import DocumentParseError

XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
XHTML_NAMESPACE = "http://www.w3.org/1999/xhtml"
OPF_NAMESPACE = "http://www.idpf.org/2007/opf"
DC_NAMESPACE = "http://purl.org/dc/elements/1.1/"


def make_xml_parser() -> etree.XMLParser:
    """创建不会联网、不会展开外部实体的严格 XML 解析器。"""

    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        recover=False,
        remove_blank_text=False,
        remove_comments=False,
        remove_pis=False,
        strip_cdata=False,
        huge_tree=False,
    )


def parse_xml(data: bytes, member_name: str) -> etree._ElementTree:
    """严格解析 XML；失败时附带 EPUB 内部文件名。"""

    try:
        return etree.parse(BytesIO(data), make_xml_parser())
    except (etree.XMLSyntaxError, OSError, ValueError) as exc:
        raise DocumentParseError(f"{member_name}：{exc}") from exc


def local_name(tag: object) -> str:
    """返回 Clark notation 标签的本地名；注释等节点返回空串。"""

    if not isinstance(tag, str):
        return ""
    try:
        return etree.QName(tag).localname
    except ValueError:
        return tag


def namespace_uri(tag: object) -> str | None:
    """返回标签的命名空间 URI。"""

    if not isinstance(tag, str):
        return None
    try:
        return etree.QName(tag).namespace
    except ValueError:
        return None


def qualified_name(namespace: str | None, name: str) -> str:
    """按需要构造带命名空间的标签名。"""

    return f"{{{namespace}}}{name}" if namespace else name

