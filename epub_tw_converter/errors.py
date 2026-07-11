"""项目自定义异常。"""


class EpubConversionError(Exception):
    """输入 EPUB 无效，或转换过程遇到无法继续的错误。"""


class DocumentParseError(Exception):
    """某个可转换文档无法安全解析。"""


class UnsafeArchiveError(EpubConversionError):
    """ZIP 条目包含重复路径、危险路径或其他不安全结构。"""

