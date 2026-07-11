"""发现 EPUB 的 OPF、正文、导航与 NCX 文档。"""

from __future__ import annotations

import logging
import posixpath
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile

from .errors import DocumentParseError, EpubConversionError
from .models import DocumentKind
from .xml_utils import local_name, parse_xml

LOGGER = logging.getLogger(__name__)

PACKAGE_MEDIA_TYPE = "application/oebps-package+xml"
XHTML_MEDIA_TYPE = "application/xhtml+xml"
HTML_MEDIA_TYPE = "text/html"
NCX_MEDIA_TYPE = "application/x-dtbncx+xml"
MAX_CORE_XML_SIZE = 16 * 1024 * 1024
MAX_ROOTFILES = 32
MAX_MANIFEST_ITEMS = 100_000
FONT_MEDIA_TYPES = {
    "application/font-sfnt",
    "application/font-woff",
    "application/font-woff2",
    "application/vnd.ms-opentype",
    "application/x-font-opentype",
    "application/x-font-otf",
    "application/x-font-truetype",
    "application/x-font-ttf",
    "application/x-font-woff",
    "application/x-font-woff2",
    "font/otf",
    "font/sfnt",
    "font/ttf",
    "font/woff",
    "font/woff2",
}


@dataclass(frozen=True)
class EpubIndex:
    """EPUB 内部文本文件分类结果。"""

    documents: dict[str, DocumentKind]
    package_documents: tuple[str, ...]
    media_types: dict[str, str]


def _read_core_xml_member(archive: ZipFile, member_name: str) -> bytes:
    """在分配内存前限制 container、OPF、encryption 等核心 XML。"""

    try:
        info = archive.getinfo(member_name)
    except KeyError as exc:
        raise EpubConversionError(f"EPUB 缺少核心文件：{member_name}") from exc
    if info.file_size > MAX_CORE_XML_SIZE:
        raise EpubConversionError(
            f"EPUB 核心 XML 超过 16 MiB 安全上限：{member_name}"
        )
    return archive.read(info)


def _normalise_archive_reference(base: str, href: str) -> str | None:
    """将 OPF 中的 URL 引用解析为 ZIP 内 POSIX 路径。"""

    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        return None

    decoded_path = unquote(parsed.path)
    if not decoded_path or decoded_path.startswith("/"):
        return None

    joined = posixpath.normpath(posixpath.join(base, decoded_path))
    if joined == ".." or joined.startswith("../"):
        return None
    return joined


def _container_rootfiles(archive: ZipFile) -> list[str]:
    """从 ``container.xml`` 读取所有 rendition 的 OPF 路径。"""

    container_name = "META-INF/container.xml"
    try:
        data = _read_core_xml_member(archive, container_name)
    except EpubConversionError as exc:
        raise EpubConversionError(
            "输入文件缺少或无法安全读取 META-INF/container.xml。"
        ) from exc

    try:
        tree = parse_xml(data, container_name)
    except DocumentParseError as exc:
        raise EpubConversionError(
            f"EPUB 核心文件 container.xml 无法解析：{exc}"
        ) from exc

    rootfiles: list[str] = []
    for element in tree.getroot().iter():
        if local_name(element.tag) != "rootfile":
            continue
        full_path = element.get("full-path")
        media_type = element.get("media-type")
        if not full_path:
            continue
        if media_type and media_type != PACKAGE_MEDIA_TYPE:
            LOGGER.warning(
                "rootfile %s 使用了非标准 media-type：%s",
                full_path,
                media_type,
            )
        resolved = _normalise_archive_reference("", full_path)
        if resolved and resolved not in rootfiles:
            rootfiles.append(resolved)
            if len(rootfiles) > MAX_ROOTFILES:
                raise EpubConversionError(
                    "container.xml 的 rootfile 数量超过 32 个安全上限。"
                )

    if not rootfiles:
        raise EpubConversionError("container.xml 中没有有效的 OPF rootfile。")
    return rootfiles


def _manifest_documents(
    archive: ZipFile,
    package_path: str,
) -> tuple[dict[str, DocumentKind], dict[str, str]]:
    """解析一个 OPF manifest 并返回其中的文本资源。"""

    try:
        package_data = _read_core_xml_member(archive, package_path)
    except EpubConversionError as exc:
        raise EpubConversionError(
            f"container.xml 指向不存在或过大的 OPF：{package_path}"
        ) from exc

    try:
        tree = parse_xml(package_data, package_path)
    except DocumentParseError as exc:
        raise EpubConversionError(
            f"EPUB 核心 OPF 无法解析：{exc}"
        ) from exc

    base = posixpath.dirname(package_path)
    discovered: dict[str, DocumentKind] = {}
    media_types: dict[str, str] = {}
    item_count = 0

    for element in tree.getroot().iter():
        if local_name(element.tag) != "item":
            continue
        item_count += 1
        if item_count > MAX_MANIFEST_ITEMS:
            raise EpubConversionError(
                f"OPF manifest 条目超过 100000 个：{package_path}"
            )

        href = element.get("href")
        media_type = (element.get("media-type") or "").lower()
        if not href:
            continue

        member_name = _normalise_archive_reference(base, href)
        if member_name is None:
            LOGGER.warning(
                "忽略 OPF 中无法映射到 EPUB 内部的引用：%s -> %s",
                package_path,
                href,
            )
            continue

        previous_media_type = media_types.get(member_name)
        if (
            previous_media_type
            and media_type
            and previous_media_type != media_type
        ):
            raise EpubConversionError(
                "同一资源在 OPF 中声明了冲突的 media-type："
                f"{member_name}"
            )
        if media_type:
            media_types[member_name] = media_type

        if media_type == XHTML_MEDIA_TYPE:
            discovered[member_name] = DocumentKind.XHTML
        elif media_type == HTML_MEDIA_TYPE:
            discovered[member_name] = DocumentKind.HTML
        elif media_type == NCX_MEDIA_TYPE:
            discovered[member_name] = DocumentKind.NCX

    return discovered, media_types


def build_epub_index(archive: ZipFile) -> EpubIndex:
    """构建文本文件索引，并用扩展名覆盖不完整 manifest 的常见情况。"""

    names = set(archive.namelist())
    package_documents = _container_rootfiles(archive)
    documents: dict[str, DocumentKind] = {}
    media_types: dict[str, str] = {}

    for package_path in package_documents:
        documents[package_path] = DocumentKind.PACKAGE
        media_types[package_path] = PACKAGE_MEDIA_TYPE
        package_text_documents, package_media_types = _manifest_documents(
            archive,
            package_path,
        )
        documents.update(package_text_documents)
        for member_name, media_type in package_media_types.items():
            previous_media_type = media_types.get(member_name)
            if previous_media_type and previous_media_type != media_type:
                raise EpubConversionError(
                    "不同 OPF rendition 对同一资源声明了冲突的 media-type："
                    f"{member_name}"
                )
            media_types[member_name] = media_type

    # 某些旧 EPUB 的 manifest 不完整。扩展名兜底可覆盖封面、注释页和 NCX，
    # 但不会碰普通 XML，以免误改非自然语言数据。
    for member_name in names:
        suffix = posixpath.splitext(member_name)[1].lower()
        if suffix == ".ncx":
            documents.setdefault(member_name, DocumentKind.NCX)
        elif suffix == ".xhtml":
            documents.setdefault(member_name, DocumentKind.XHTML)
        elif suffix in {".html", ".htm"}:
            documents.setdefault(member_name, DocumentKind.HTML)

    missing = sorted(name for name in documents if name not in names)
    for member_name in missing:
        LOGGER.warning("OPF 引用的文本资源不存在，已忽略：%s", member_name)
        documents.pop(member_name, None)

    return EpubIndex(
        documents=documents,
        package_documents=tuple(package_documents),
        media_types=media_types,
    )


def detect_unsupported_encryption(
    archive: ZipFile,
    index: EpubIndex,
) -> None:
    """允许 EPUB 字体混淆，但拒绝无法安全处理的正文 DRM。"""

    encryption_name = "META-INF/encryption.xml"
    if encryption_name not in archive.namelist():
        return

    try:
        encryption_data = _read_core_xml_member(archive, encryption_name)
        tree = parse_xml(encryption_data, encryption_name)
    except DocumentParseError as exc:
        raise EpubConversionError(f"encryption.xml 无法解析：{exc}") from exc

    allowed_algorithms = {
        "http://www.idpf.org/2008/embedding",
        "http://ns.adobe.com/pdf/enc#RC",
    }
    encrypted_items = [
        element
        for element in tree.getroot().iter()
        if local_name(element.tag) == "EncryptedData"
    ]
    if not encrypted_items:
        raise EpubConversionError(
            "encryption.xml 没有可验证的 EncryptedData，已拒绝转换。"
        )

    archive_names = set(archive.namelist())
    verified_fonts: list[str] = []
    for encrypted_item in encrypted_items:
        algorithm = None
        cipher_uri = None
        for element in encrypted_item.iter():
            name = local_name(element.tag)
            if name == "EncryptionMethod" and algorithm is None:
                algorithm = element.get("Algorithm")
            elif name == "CipherReference" and cipher_uri is None:
                cipher_uri = element.get("URI")

        if algorithm not in allowed_algorithms:
            raise EpubConversionError(
                "检测到无法安全转换的加密/DRM 算法："
                f"{algorithm or '未知算法'}"
            )
        if not cipher_uri:
            raise EpubConversionError("加密条目缺少 CipherReference URI。")

        member_name = _normalise_archive_reference("", cipher_uri)
        if member_name is None or member_name not in archive_names:
            raise EpubConversionError(
                f"加密条目指向无效资源：{cipher_uri}"
            )

        media_type = index.media_types.get(member_name, "").lower()
        if media_type not in FONT_MEDIA_TYPES:
            raise EpubConversionError(
                "允许的混淆算法只能用于 manifest 声明的字体，"
                f"但目标是 {member_name}（{media_type or '无 media-type'}）。"
            )
        verified_fonts.append(member_name)

    LOGGER.info(
        "已验证 %d 个字体混淆资源；将保持其字节与 dc:identifier 不变。",
        len(verified_fonts),
    )
