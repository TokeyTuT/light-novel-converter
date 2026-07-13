"""EPUB 内 XML、XHTML、HTML、NCX 与 OPF 的转换逻辑。"""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import logging
import re

from lxml import etree

from .errors import DocumentParseError, EpubConversionError
from .models import DocumentKind, PageDirection, TransformResult
from .text import TaiwanTextConverter
from .xml_utils import (
    DC_NAMESPACE,
    OPF_NAMESPACE,
    XML_NAMESPACE,
    local_name,
    namespace_uri,
    parse_xml,
    qualified_name,
)

VERTICAL_STYLE_ID = "lnc-vertical-style"
ILLUSTRATION_PAGE_STYLE_ID = "lnc-illustration-page-style"
ILLUSTRATION_PAGE_CLASS = "lnc-illustration-page"
ILLUSTRATION_START_CLASS = "lnc-illustration-start"
ILLUSTRATION_END_CLASS = "lnc-illustration-end"
LOGGER = logging.getLogger(__name__)
VERTICAL_CSS = """/* 由 light-novel-converter 注入：台湾繁体竖排 */
html,
body {
    -webkit-writing-mode: vertical-rl !important;
    -epub-writing-mode: vertical-rl !important;
    writing-mode: vertical-rl !important;
    -webkit-text-orientation: mixed !important;
    -epub-text-orientation: mixed !important;
    text-orientation: mixed !important;
    line-break: strict !important;
}
"""
ILLUSTRATION_PAGE_CSS = """/* 由 light-novel-converter 注入：插画从新页开始 */
.lnc-illustration-page {
    display: block !important;
}
.lnc-illustration-start {
    -webkit-column-break-before: always;
    break-before: page;
    page-break-before: always;
}
.lnc-illustration-end {
    -webkit-column-break-after: always;
    break-after: page;
    page-break-after: always;
}
"""

HUMAN_READABLE_ATTRIBUTES = {
    "alt",
    "aria-label",
    "placeholder",
    "summary",
    "title",
}
SUPPRESSED_TEXT_ELEMENTS = {"script", "style"}
LAYOUT_IGNORED_TEXT_ELEMENTS = {"script", "style", "template"}
ILLUSTRATION_ELEMENTS = {"img", "picture", "svg"}
LAYOUT_IGNORABLE_ENTITY_NAMES = {
    "emsp",
    "ensp",
    "hairsp",
    "lrm",
    "nbsp",
    "numsp",
    "puncsp",
    "rlm",
    "shy",
    "thinsp",
    "zerowidthspace",
    "zwj",
    "zwnj",
}
FLOW_BOUNDARY_ELEMENTS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "body",
    "br",
    "caption",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "head",
    "header",
    "hr",
    "iframe",
    "img",
    "li",
    "main",
    "math",
    "nav",
    "object",
    "ol",
    "p",
    "pre",
    "rp",
    "rt",
    "section",
    "svg",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "title",
    "tr",
    "ul",
    "video",
    "wbr",
}
OPF_NATURAL_LANGUAGE_FIELDS = {
    "contributor",
    "coverage",
    "creator",
    "description",
    "publisher",
    "rights",
    "subject",
    "title",
}
OPF_NATURAL_META_PROPERTIES = {"alternate-script", "file-as"}
SIMPLIFIED_CHINESE_LANGUAGE_TAGS = {
    "zh",
    "zh-cn",
    "zh-hans",
    "zh-hans-cn",
    "zh-sg",
}


def _is_simplified_chinese_language(value: str) -> bool:
    """判断 BCP 47 标签是否明确指向简体中文。"""

    normalised = value.strip().lower()
    return (
        normalised in SIMPLIFIED_CHINESE_LANGUAGE_TAGS
        or normalised.startswith("zh-hans-")
        or normalised.startswith("zh-cn-")
        or normalised.startswith("zh-sg-")
    )


class DocumentTransformer:
    """对 EPUB 内的不同文本资源执行安全、定向转换。"""

    def __init__(
        self,
        text_converter: TaiwanTextConverter,
        page_direction: PageDirection = PageDirection.KEEP,
    ) -> None:
        self._text_converter = text_converter
        self._page_direction = page_direction

    def transform(
        self,
        data: bytes,
        kind: DocumentKind,
        member_name: str,
    ) -> TransformResult:
        """按文件类型转换单个 ZIP 条目。"""

        if kind is DocumentKind.HTML:
            return self._transform_html(data, member_name)

        tree = parse_xml(data, member_name)
        if kind is DocumentKind.XHTML:
            return self._transform_xhtml(tree, served_as_html=False)
        if kind is DocumentKind.NCX:
            return self._transform_ncx(tree)
        if kind is DocumentKind.PACKAGE:
            return self._transform_package(tree, member_name)
        raise EpubConversionError(f"不支持的文档类型：{kind}")

    def _convert_value(self, value: str | None) -> tuple[str | None, int]:
        converted, changed = self._text_converter.convert(value)
        return converted, int(changed)

    def _convert_visible_subtree(
        self,
        element: etree._Element,
    ) -> int:
        """按可见文本流联合转换，保留跨行内标签的 OpenCC 语境。"""

        text_flows: list[list[tuple[etree._Element, str]]] = []
        current_flow: list[tuple[etree._Element, str]] = []
        changed = 0

        def flush_flow() -> None:
            nonlocal current_flow
            if current_flow:
                text_flows.append(current_flow)
                current_flow = []

        def add_slot(owner: etree._Element, field: str) -> None:
            value = getattr(owner, field)
            if value:
                current_flow.append((owner, field))

        def walk_detached(node: etree._Element) -> None:
            """单独收集 ruby 注音，但不切断正在收集的基文字流。"""

            nonlocal current_flow
            base_flow = current_flow
            current_flow = []
            walk(node, is_root=True)
            flush_flow()
            current_flow = base_flow

        def walk(node: etree._Element, *, is_root: bool = False) -> None:
            nonlocal changed
            name = local_name(node.tag).lower()
            if name in SUPPRESSED_TEXT_ELEMENTS:
                # script/style 内部不是自然语言；同时阻断其两侧的词组匹配。
                flush_flow()
                return

            is_boundary = not is_root and name in FLOW_BOUNDARY_ELEMENTS
            if is_boundary:
                flush_flow()

            for attribute_name in HUMAN_READABLE_ATTRIBUTES:
                if attribute_name not in node.attrib:
                    continue
                converted, delta = self._convert_value(
                    node.attrib[attribute_name]
                )
                if converted is not None:
                    node.attrib[attribute_name] = converted
                changed += delta

            add_slot(node, "text")
            for child in node:
                if isinstance(child.tag, str):
                    child_name = local_name(child.tag).lower()
                    if name == "ruby" and child_name in {"rp", "rt"}:
                        # <rt>/<rp> 是注音/回退文本；单独转换它们，同时让
                        # ruby base 的“头…发”仍能联合匹配为“頭髮”。
                        walk_detached(child)
                    else:
                        walk(child)
                # tail 属于父节点语境；script/style 或块元素会在 walk 中
                # 先结束上一条流，因此这里会自然开启新的流。
                add_slot(child, "tail")

            if is_boundary:
                flush_flow()

        walk(element, is_root=True)
        flush_flow()
        for flow in text_flows:
            changed += self._convert_text_flow(flow)
        return changed

    def _convert_text_flow(
        self,
        slots: list[tuple[etree._Element, str]],
    ) -> int:
        """合并一条文本流转换，再按字符对齐安全写回原 text/tail。"""

        source_parts = [getattr(owner, field) for owner, field in slots]
        source = "".join(source_parts)
        converted, was_changed = self._text_converter.convert(source)
        if not was_changed or converted is None:
            return 0

        boundaries = self._align_slot_boundaries(
            source,
            converted,
            [len(part) for part in source_parts],
        )
        changed_slots = 0
        for index, (owner, field) in enumerate(slots):
            new_value = converted[boundaries[index] : boundaries[index + 1]]
            if new_value != source_parts[index]:
                setattr(owner, field, new_value)
                changed_slots += 1
        return changed_slots

    @staticmethod
    def _align_slot_boundaries(
        source: str,
        converted: str,
        part_lengths: list[int],
    ) -> list[int]:
        """把源文本节点边界映射到可能不同长度的 OpenCC 输出。"""

        source_boundaries = [0]
        for length in part_lengths:
            source_boundaries.append(source_boundaries[-1] + length)

        if len(source) == len(converted):
            return source_boundaries

        # 不使用 SequenceMatcher：对长且高度重复的小说文本，它最坏会达到
        # O(n²)。OpenCC 保持字符顺序，因此用线性比例映射少数长度变化词组；
        # 拼接后的转换文本始终完全正确，最多只影响变长词跨标签时的样式边界。
        mapped = [
            round(position * len(converted) / len(source))
            for position in source_boundaries
        ]
        mapped[0] = 0
        mapped[-1] = len(converted)
        for index in range(1, len(mapped)):
            mapped[index] = max(mapped[index - 1], mapped[index])
        return mapped

    def _convert_entire_subtree(self, element: etree._Element) -> int:
        """转换已确认属于自然语言字段的整个子树。"""

        changed = 0
        element.text, delta = self._convert_value(element.text)
        changed += delta
        for child in element:
            if isinstance(child.tag, str):
                changed += self._convert_entire_subtree(child)
            child.tail, delta = self._convert_value(child.tail)
            changed += delta
        return changed

    @staticmethod
    def _serialise_xml(tree: etree._ElementTree) -> bytes:
        """以 UTF-8 重写 XML，并让 lxml 保留完整的内部 DTD 子集。"""

        # 不能显式传 tree.docinfo.doctype：该属性不包含 internal subset，
        # 显式传入会让 <!ENTITY ...> 声明丢失，却留下 &entity; 引用。
        return etree.tostring(
            tree,
            encoding="UTF-8",
            xml_declaration=True,
            pretty_print=False,
        )

    @staticmethod
    def _serialise_html(tree: etree._ElementTree) -> bytes:
        """把已经安全解析的树序列化为 UTF-8 HTML。"""

        return etree.tostring(
            tree,
            encoding="UTF-8",
            method="html",
            pretty_print=False,
        )

    @staticmethod
    def _set_chinese_language(
        root: etree._Element,
        *,
        html_syntax: bool,
    ) -> bool:
        """按 XML/HTML 各自规则设置语言，避免重复或不合 schema。"""

        changed = False
        xml_lang_name = f"{{{XML_NAMESPACE}}}lang"
        if html_syntax:
            for attribute_name in (xml_lang_name, "xml:lang"):
                if attribute_name in root.attrib:
                    root.attrib.pop(attribute_name)
                    changed = True
            if root.get("lang") != "zh-TW":
                root.set("lang", "zh-TW")
                changed = True
            return changed

        # XHTML 1.1（EPUB 2）只允许 xml:lang；EPUB 3 也能识别它。
        if "lang" in root.attrib:
            root.attrib.pop("lang")
            changed = True
        if "xml:lang" in root.attrib:
            root.attrib.pop("xml:lang")
            changed = True
        if root.get(xml_lang_name) != "zh-TW":
            root.set(xml_lang_name, "zh-TW")
            changed = True
        return changed

    @staticmethod
    def _normalise_existing_language_attributes(
        root: etree._Element,
    ) -> int:
        """把 OPF 中已有的简体中文语言标记同步为 zh-TW。"""

        changed = 0
        for element in root.iter():
            for attribute_name in list(element.attrib):
                if local_name(attribute_name) != "lang":
                    continue
                language = element.attrib[attribute_name]
                if _is_simplified_chinese_language(language):
                    element.attrib[attribute_name] = "zh-TW"
                    changed += 1
        return changed

    @staticmethod
    def _find_first(root: etree._Element, wanted_name: str) -> etree._Element | None:
        for element in root.iter():
            if local_name(element.tag).lower() == wanted_name:
                return element
        return None

    def _get_or_create_head(self, root: etree._Element) -> etree._Element:
        """取得 head，缺失时以与旧逻辑相同的方式安全创建。"""

        head = self._find_first(root, "head")
        if head is not None:
            return head

        if local_name(root.tag).lower() != "html":
            raise DocumentParseError("文档根节点不是 html，无法安全注入样式")

        namespace = namespace_uri(root.tag)
        head = etree.Element(qualified_name(namespace, "head"))
        body = self._find_first(root, "body")
        if body is None:
            root.insert(0, head)
        else:
            root.insert(root.index(body), head)
        return head

    @staticmethod
    def _ensure_injected_style(
        head: etree._Element,
        style_id: str,
        css: str,
    ) -> bool:
        """幂等地写入转换器专用的样式节点。"""

        existing: etree._Element | None = None
        for element in head.iter():
            if (
                local_name(element.tag).lower() == "style"
                and element.get("id") == style_id
            ):
                existing = element
                break

        if existing is not None:
            changed = existing.text != css
            existing.text = css
            if existing.get("type") != "text/css":
                existing.set("type", "text/css")
                changed = True
            return changed

        namespace = namespace_uri(head.tag)
        style = etree.Element(qualified_name(namespace, "style"))
        style.set("id", style_id)
        style.set("type", "text/css")
        style.text = css
        head.append(style)
        return True

    def _ensure_vertical_style(self, root: etree._Element) -> bool:
        """幂等地注入根级竖排 CSS，不改写图片或字体引用。"""

        head = self._get_or_create_head(root)
        return self._ensure_injected_style(
            head,
            VERTICAL_STYLE_ID,
            VERTICAL_CSS,
        )

    def _ensure_illustration_page_style(self, root: etree._Element) -> bool:
        """为已标记的插画写入从新页开始的 CSS。"""

        head = self._get_or_create_head(root)
        return self._ensure_injected_style(
            head,
            ILLUSTRATION_PAGE_STYLE_ID,
            ILLUSTRATION_PAGE_CSS,
        )

    @staticmethod
    def _remove_vertical_style(root: etree._Element) -> bool:
        """从纯图片页移除旧版本曾经注入的竖排样式。"""

        head = next(
            (
                element
                for element in root.iter()
                if local_name(element.tag).lower() == "head"
            ),
            None,
        )
        if head is None:
            return False

        changed = False
        # 转换器只会把该样式注入 head。将清理范围限定在
        # head，避免误删 inline SVG 内恰好同 ID 的作者样式。
        for element in list(head.iter()):
            if (
                local_name(element.tag).lower() == "style"
                and element.get("id") == VERTICAL_STYLE_ID
            ):
                parent = element.getparent()
                if parent is not None:
                    parent.remove(element)
                    changed = True
        return changed

    @staticmethod
    def _has_meaningful_layout_text(value: str | None) -> bool:
        """返回字符串是否包含影响排版判断的可见文字。"""

        if not value:
            return False
        ignored_characters = {"\u200b", "\u200c", "\u200d", "\ufeff"}
        return any(
            not character.isspace() and character not in ignored_characters
            for character in value
        )

    @staticmethod
    def _is_ignorable_layout_entity(entity: etree._Entity) -> bool:
        """返回未展开实体是否仅表示空白或格式字符。"""

        return (entity.name or "").lower() in LAYOUT_IGNORABLE_ENTITY_NAMES

    @staticmethod
    def _append_css_class(element: etree._Element, class_name: str) -> bool:
        """不覆盖作者原有 class，幂等地追加转换器 class。"""

        classes = (element.get("class") or "").split()
        if class_name in classes:
            return False
        classes.append(class_name)
        element.set("class", " ".join(classes))
        return True

    def _mark_illustration_page_breaks(
        self,
        root: etree._Element,
    ) -> tuple[bool, bool]:
        """标记插画前后的分页，令图片不与正文同页。"""

        body = self._find_first(root, "body")
        if body is None:
            return False, False

        events: list[tuple[str, etree._Element | None]] = []

        def collect_events(element: etree._Element) -> None:
            name = local_name(element.tag).lower()
            if name in LAYOUT_IGNORED_TEXT_ELEMENTS:
                return

            # picture/SVG 视为一个插画容器，不进入子树重复计算。
            if name in ILLUSTRATION_ELEMENTS:
                events.append(("illustration", element))
                return

            if self._has_meaningful_layout_text(element.text):
                events.append(("text", None))

            for child in element:
                if isinstance(child.tag, str):
                    collect_events(child)
                elif isinstance(child, etree._Entity):
                    if not self._is_ignorable_layout_entity(child):
                        events.append(("text", None))
                if self._has_meaningful_layout_text(child.tail):
                    events.append(("text", None))

        collect_events(body)
        changed = False
        has_illustration = False
        for index, (kind, element) in enumerate(events):
            if kind != "illustration" or element is None:
                continue

            has_illustration = True
            changed |= self._append_css_class(
                element,
                ILLUSTRATION_PAGE_CLASS,
            )

            previous_kind = events[index - 1][0] if index else None
            if previous_kind == "text":
                changed |= self._append_css_class(
                    element,
                    ILLUSTRATION_START_CLASS,
                )

            # 后续只要还有正文或下一张插画，都要由当前插画强制
            # 分页。对连续插画仅使用前一张的 after break，避免双重分页。
            if index + 1 < len(events):
                changed |= self._append_css_class(
                    element,
                    ILLUSTRATION_END_CLASS,
                )

        return changed, has_illustration

    def _is_image_only_document(self, root: etree._Element) -> bool:
        """判断 body 是否只由一张或多张图片及无文本包装元素组成。"""

        body = self._find_first(root, "body")
        if body is None:
            return False

        has_image = False
        has_visible_text = False

        def visit(element: etree._Element) -> None:
            nonlocal has_image, has_visible_text
            name = local_name(element.tag).lower()
            if name in LAYOUT_IGNORED_TEXT_ELEMENTS:
                return

            if name in {"img", "picture", "svg"}:
                has_image = True
                # SVG 的 text/title/desc 都属于插画自身，不能把它们误认为正文。
                if name == "svg":
                    return
            elif name == "object":
                media_type = (element.get("type") or "").lower()
                data_path = (element.get("data") or "").lower()
                if media_type.startswith("image/") or data_path.endswith(
                    (".avif", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp")
                ):
                    has_image = True
                    return

            inline_style = (element.get("style") or "").lower()
            if "background-image" in inline_style or (
                "background" in inline_style and "url(" in inline_style
            ):
                has_image = True

            if self._has_meaningful_layout_text(element.text):
                has_visible_text = True

            for child in element:
                if isinstance(child.tag, str):
                    visit(child)
                elif isinstance(child, etree._Entity):
                    # 安全解析器不展开 XML 实体，无法在此确定其是
                    # 空白还是正文。常见空白/格式实体可安全忽略，
                    # 其余实体则按可见内容保守处理。
                    if not self._is_ignorable_layout_entity(child):
                        has_visible_text = True
                if self._has_meaningful_layout_text(child.tail):
                    has_visible_text = True

        visit(body)
        return has_image and not has_visible_text

    def _update_content_layout(self, root: etree._Element) -> bool:
        """正文注入竖排；纯图页保留几何，插画从新页开始。"""

        # 部分电子书用连续的 inline <img> 拼成跨页插画。
        # vertical-rl 会旋转 inline 轴，导致阅读器把切片分到不同分页。
        if self._is_image_only_document(root):
            changed = self._remove_vertical_style(root)
        else:
            changed = self._ensure_vertical_style(root)

        page_break_changed, has_page_break = self._mark_illustration_page_breaks(
            root,
        )
        changed |= page_break_changed
        if has_page_break:
            changed |= self._ensure_illustration_page_style(root)
        return changed

    def _normalise_utf8_meta(
        self,
        root: etree._Element,
        *,
        add_if_missing: bool,
    ) -> bool:
        """让 HTML 内的字符集声明与 UTF-8 序列化结果一致。"""

        head = self._find_first(root, "head")
        if head is None:
            return False

        changed = False
        found_charset_declaration = False
        for element in head.iter():
            if local_name(element.tag).lower() != "meta":
                continue

            if "charset" in element.attrib:
                found_charset_declaration = True
                if element.get("charset", "").lower() != "utf-8":
                    element.set("charset", "utf-8")
                    changed = True

            http_equiv = (element.get("http-equiv") or "").strip().lower()
            if http_equiv != "content-type":
                continue

            found_charset_declaration = True
            content = element.get("content") or "text/html"
            if re.search(r"charset\s*=", content, flags=re.IGNORECASE):
                updated = re.sub(
                    r"(charset\s*=\s*)[^;\s]+",
                    r"\g<1>utf-8",
                    content,
                    flags=re.IGNORECASE,
                )
            else:
                updated = content.rstrip("; ") + "; charset=utf-8"
            if updated != content:
                element.set("content", updated)
                changed = True

        # XHTML 依靠 XML declaration 即可；只对 text/html 补 HTML5 meta，
        # 避免给严格 XHTML 1.1 增加其 schema 不认识的 charset 属性。
        if add_if_missing and not found_charset_declaration:
            namespace = namespace_uri(head.tag)
            meta = etree.Element(qualified_name(namespace, "meta"))
            meta.set("charset", "utf-8")
            head.insert(0, meta)
            changed = True

        return changed

    def _transform_xhtml(
        self,
        tree: etree._ElementTree,
        *,
        served_as_html: bool,
    ) -> TransformResult:
        root = tree.getroot()
        if local_name(root.tag).lower() != "html":
            raise DocumentParseError("XHTML 根节点不是 html")

        changed_nodes = self._convert_visible_subtree(root)
        language_changed = self._set_chinese_language(
            root,
            html_syntax=served_as_html,
        )
        layout_changed = self._update_content_layout(root)
        layout_changed |= self._normalise_utf8_meta(
            root,
            add_if_missing=served_as_html,
        )
        if language_changed:
            changed_nodes += 1

        return TransformResult(
            data=(
                self._serialise_html(tree)
                if served_as_html
                else self._serialise_xml(tree)
            ),
            changed_nodes=changed_nodes,
            layout_changed=layout_changed,
        )

    def _transform_html(self, data: bytes, member_name: str) -> TransformResult:
        """解析 OPF 明确标记为 text/html 的旧式页面。"""

        # 很多旧书把实际 XHTML 错标成 text/html。若它是严格 XML，优先走
        # XML 路径，以完整保留命名空间、SVG 与属性大小写。
        try:
            xml_tree = parse_xml(data, member_name)
        except DocumentParseError:
            xml_tree = None
        if (
            xml_tree is not None
            and local_name(xml_tree.getroot().tag).lower() == "html"
        ):
            return self._transform_xhtml(xml_tree, served_as_html=True)

        parser = etree.HTMLParser(
            no_network=True,
            recover=True,
            remove_blank_text=False,
            remove_comments=False,
        )
        try:
            tree = etree.parse(BytesIO(data), parser)
        except (etree.XMLSyntaxError, OSError, ValueError) as exc:
            raise DocumentParseError(f"{member_name}：{exc}") from exc

        root = tree.getroot()
        if root is None or local_name(root.tag).lower() != "html":
            raise DocumentParseError(f"{member_name}：无法构造 HTML 文档树")

        # libxml2 的旧式 HTML parser 会把 SVG 的 viewBox 等属性小写化。
        # 对包含 foreign content 的非 XML HTML，宁可原样跳过，也不冒险损坏。
        foreign_elements = {"math", "svg"}
        if any(
            local_name(element.tag).lower() in foreign_elements
            for element in root.iter()
            if isinstance(element.tag, str)
        ):
            raise DocumentParseError(
                f"{member_name}：非 XML text/html 含 SVG/MathML，已安全跳过"
            )

        changed_nodes = self._convert_visible_subtree(root)
        language_changed = self._set_chinese_language(
            root,
            html_syntax=True,
        )
        layout_changed = self._update_content_layout(root)
        layout_changed |= self._normalise_utf8_meta(
            root,
            add_if_missing=True,
        )
        if language_changed:
            changed_nodes += 1

        serialised = self._serialise_html(tree)
        return TransformResult(
            data=serialised,
            changed_nodes=changed_nodes,
            layout_changed=layout_changed,
        )

    def _transform_ncx(self, tree: etree._ElementTree) -> TransformResult:
        root = tree.getroot()
        changed_nodes = 0

        for element in root.iter():
            if local_name(element.tag) != "text":
                continue
            parent = element.getparent()
            if parent is None:
                continue
            if local_name(parent.tag) in {
                "docAuthor",
                "docTitle",
                "navInfo",
                "navLabel",
            }:
                changed_nodes += self._convert_entire_subtree(element)

        xml_lang_name = f"{{{XML_NAMESPACE}}}lang"
        current_language = (root.get(xml_lang_name) or "").lower()
        if not current_language or _is_simplified_chinese_language(
            current_language
        ):
            root.set(xml_lang_name, "zh-TW")
            changed_nodes += 1

        return TransformResult(
            data=self._serialise_xml(tree),
            changed_nodes=changed_nodes,
        )

    def _transform_package(
        self,
        tree: etree._ElementTree,
        member_name: str,
    ) -> TransformResult:
        root = tree.getroot()
        changed_nodes = self._normalise_existing_language_attributes(root)

        for element in root.iter():
            name = local_name(element.tag)
            namespace = namespace_uri(element.tag)

            if (
                namespace == DC_NAMESPACE
                and name in OPF_NATURAL_LANGUAGE_FIELDS
            ):
                changed_nodes += self._convert_entire_subtree(element)
                file_as_names = [
                    key
                    for key in element.attrib
                    if local_name(key) == "file-as"
                ]
                for attribute_name in file_as_names:
                    converted, delta = self._convert_value(
                        element.attrib[attribute_name]
                    )
                    if converted is not None:
                        element.attrib[attribute_name] = converted
                    changed_nodes += delta

            elif namespace == DC_NAMESPACE and name == "language":
                language = element.text or ""
                if _is_simplified_chinese_language(language):
                    element.text = "zh-TW"
                    changed_nodes += 1

            elif namespace == OPF_NAMESPACE and name == "meta":
                property_name = element.get("property")
                legacy_name = (element.get("name") or "").lower()
                if property_name in OPF_NATURAL_META_PROPERTIES:
                    changed_nodes += self._convert_entire_subtree(element)
                elif legacy_name in {
                    "author",
                    "creator",
                    "description",
                    "keywords",
                    "publisher",
                    "rights",
                    "subject",
                    "title",
                }:
                    content = element.get("content")
                    converted, delta = self._convert_value(content)
                    if converted is not None:
                        element.set("content", converted)
                    changed_nodes += delta

        layout_changed = self._update_package_layout(root, member_name)
        if layout_changed:
            changed_nodes += 1

        return TransformResult(
            data=self._serialise_xml(tree),
            changed_nodes=changed_nodes,
            layout_changed=layout_changed,
        )

    def _update_package_layout(
        self,
        root: etree._Element,
        member_name: str,
    ) -> bool:
        """按用户选项设置翻页方向，并刷新 EPUB 3 修改时间。"""

        version_text = (root.get("version") or "").strip()
        try:
            major_version = int(version_text.split(".", maxsplit=1)[0])
        except (TypeError, ValueError):
            major_version = 0

        progression = self._page_direction.progression
        if major_version < 2:
            return False

        changed = False
        if progression is not None:
            if major_version == 2:
                LOGGER.warning(
                    "%s 是 EPUB 2；将 page-progression-direction=%s 作为"
                    "阅读器兼容扩展写入，可能无法通过严格 EPUB 2 校验。",
                    member_name,
                    progression,
                )
            for element in root.iter():
                if local_name(element.tag) == "spine":
                    if element.get("page-progression-direction") != progression:
                        element.set("page-progression-direction", progression)
                        changed = True
                    break

        if major_version < 3:
            return changed

        metadata = None
        modified_metas: list[etree._Element] = []
        for element in root.iter():
            name = local_name(element.tag)
            if name == "metadata" and metadata is None:
                metadata = element
            elif (
                name == "meta"
                and element.get("property") == "dcterms:modified"
            ):
                modified_metas.append(element)

        if metadata is not None:
            timestamp = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            if modified_metas:
                modified_meta = modified_metas[0]
                for duplicate in modified_metas[1:]:
                    parent = duplicate.getparent()
                    if parent is not None:
                        parent.remove(duplicate)
                        changed = True
            else:
                namespace = namespace_uri(metadata.tag)
                modified_meta = etree.Element(
                    qualified_name(namespace, "meta")
                )
                modified_meta.set("property", "dcterms:modified")
                metadata.append(modified_meta)
            modified_meta.text = timestamp
            changed = True

        return changed
