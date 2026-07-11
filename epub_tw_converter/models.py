"""转换器使用的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DocumentKind(str, Enum):
    """EPUB 中需要转换的文本文件类型。"""

    XHTML = "xhtml"
    HTML = "html"
    NCX = "ncx"
    PACKAGE = "package"


@dataclass(frozen=True)
class TransformResult:
    """单个文档转换后的结果。"""

    data: bytes
    changed_nodes: int = 0
    layout_changed: bool = False


@dataclass
class ConversionSummary:
    """整本 EPUB 的转换统计。"""

    total_entries: int = 0
    candidate_documents: int = 0
    changed_documents: int = 0
    converted_nodes: int = 0
    layout_documents: int = 0
    skipped_documents: list[str] = field(default_factory=list)

    @property
    def skipped_count(self) -> int:
        """返回因解析错误而跳过的文档数量。"""

        return len(self.skipped_documents)

