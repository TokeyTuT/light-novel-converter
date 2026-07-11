"""OpenCC 文本转换封装。"""

from __future__ import annotations

from opencc import OpenCC

from .errors import EpubConversionError


class TaiwanTextConverter:
    """使用 OpenCC ``s2twp`` 完成台湾繁体与惯用词转换。"""

    def __init__(self, config: str = "s2twp") -> None:
        try:
            self._converter = OpenCC(config)
        except Exception as exc:  # OpenCC 的不同实现异常类型并不统一。
            raise EpubConversionError(
                f"无法载入 OpenCC 配置 {config!r}：{exc}"
            ) from exc

    def convert(self, value: str | None) -> tuple[str | None, bool]:
        """转换字符串，并返回“新值、是否发生变化”。"""

        if not value:
            return value, False

        try:
            converted = self._converter.convert(value)
        except Exception as exc:
            raise EpubConversionError(f"OpenCC 文本转换失败：{exc}") from exc

        return converted, converted != value

