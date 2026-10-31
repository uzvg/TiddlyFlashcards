"""
TiddlyFlashcards 的 UI 工具：错误报告对话框。

对外入口沿用 aqt.utils 的命名风格（showCritical / showWarning 等），
因此公开函数使用 camelCase：showError / showException；
内部实现（ErrorReport / ErrorDialog / build_debug_info）仍用 snake_case。

Anki 自带的 aqt.utils.showCritical / showWarning 只能显示一段文本，
aqt.errors 里的错误对话框虽然支持复制调试信息，但没有“折叠详情”。
这里实现一个组合两者能力的对话框：

- 标题与简要说明始终可见；
- 完整报告默认隐藏，点击 Show Details 后才展开；
- Copy Debug Info 一键复制完整报告、异常 traceback 与 Anki 环境信息，
  便于用户直接粘贴到 issue 中。
"""

import traceback
from dataclasses import dataclass

from aqt import mw
from aqt.errors import addon_debug_info
from aqt.qt import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QStyle,
    Qt,
    QVBoxLayout,
    QWidget,
    qconnect,
)
from aqt.utils import disable_help_button, supportText, tooltip

WINDOW_TITLE = "TiddlyFlashcards"


@dataclass(slots=True)
class ErrorReport:
    """一次同步失败的报告内容。

    title    —— 主要错误标题（对话框顶部大字显示）。
    summary  —— 一句话说明影响范围或下一步操作。
    details  —— 完整错误报告；可以很长，默认折叠。
    """

    title: str
    summary: str = ""
    details: str = ""

    @classmethod
    def from_exception(
        cls,
        exc: BaseException,
        *,
        title: str | None = None,
        summary: str = "",
        details: str = "",
    ) -> "ErrorReport":
        """由异常生成报告，自动附带 traceback（即程序退出时的错误报告）。"""
        trace = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        ).rstrip()

        extra = details.strip()
        details = f"{extra}\n\n{trace}" if extra else trace

        return cls(
            title=title or f"{type(exc).__name__}: {exc}",
            summary=summary,
            details=details,
        )

    def as_text(self) -> str:
        """拼出完整的错误报告（不含 Anki 环境信息）。"""
        parts = [self.title]
        if self.summary:
            parts.append(self.summary)
        if self.details:
            parts.append(self.details)
        return "\n\n".join(parts)


def build_debug_info(report: ErrorReport) -> str:
    """组合环境信息与错误报告，供用户复制反馈。"""
    parts = [supportText().rstrip()]
    try:
        parts.append(addon_debug_info().rstrip())
    except Exception as exc:
        # 环境信息收集失败不应影响用户复制错误本身。
        parts.append(f"(Failed to collect add-on info: {exc})")
    parts.append(report.as_text())
    return "\n\n".join(parts) + "\n"


class ErrorDialog(QDialog):
    """带折叠详情与复制调试信息的模态错误对话框。"""

    def __init__(self, report: ErrorReport, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.report = report
        self._details_visible = False

        self.setWindowTitle(WINDOW_TITLE)
        self.setModal(True)
        self.setMinimumWidth(580)
        disable_help_button(self)
        self._build_ui()
        self.adjustSize()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(12)

        # 顶部：警告图标 + 标题 + 简要说明
        header = QHBoxLayout()
        header.setSpacing(12)

        icon = QLabel(self)
        style = self.style()
        assert style is not None
        icon.setPixmap(
            style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning).pixmap(
                32, 32
            )
        )
        icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        header.addWidget(icon)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(6)

        title = QLabel(self.report.title, self)
        title.setWordWrap(True)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        title_font = title.font()
        title_font.setBold(True)
        title_font.setPointSizeF(title_font.pointSizeF() * 1.15)
        title.setFont(title_font)
        text_layout.addWidget(title)

        if self.report.summary:
            summary = QLabel(self.report.summary, self)
            summary.setWordWrap(True)
            summary.setTextFormat(Qt.TextFormat.PlainText)
            summary.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            text_layout.addWidget(summary)

        header.addLayout(text_layout, 1)
        layout.addLayout(header)

        # 完整报告默认折叠
        self._details = QPlainTextEdit(self)
        self._details.setReadOnly(True)
        self._details.setPlainText(
            self.report.details or "No additional details were provided."
        )
        self._details.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._details.setMinimumHeight(280)
        self._details.setVisible(False)
        layout.addWidget(self._details)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        qconnect(buttons.rejected, self.reject)

        copy_button = QPushButton("Copy Debug Info", self)
        qconnect(copy_button.clicked, self._copy_debug_info)
        buttons.addButton(copy_button, QDialogButtonBox.ButtonRole.ActionRole)

        self._details_button = QPushButton("Show Details", self)
        qconnect(self._details_button.clicked, self._toggle_details)
        buttons.addButton(self._details_button, QDialogButtonBox.ButtonRole.ActionRole)

        layout.addWidget(buttons)

    def _toggle_details(self) -> None:
        self._details_visible = not self._details_visible
        self._details.setVisible(self._details_visible)
        self._details_button.setText(
            "Hide Details" if self._details_visible else "Show Details"
        )
        self.adjustSize()
        if self._details_visible:
            self._details.setFocus()

    def _copy_debug_info(self) -> None:
        clipboard = QApplication.clipboard()
        assert clipboard is not None
        clipboard.setText(build_debug_info(self.report))
        tooltip("Debug info copied to clipboard.", parent=self)


def showError(report: ErrorReport, parent: QWidget | None = None) -> None:
    """模态显示错误对话框；关闭后调用方可直接中止后续同步。"""
    ErrorDialog(report, parent=parent or mw).exec()


def showException(
    exc: BaseException,
    *,
    title: str | None = None,
    summary: str = "",
    details: str = "",
    parent: QWidget | None = None,
) -> None:
    """由异常直接弹出错误对话框（自动附带 traceback）。"""
    showError(
        ErrorReport.from_exception(exc, title=title, summary=summary, details=details),
        parent=parent,
    )
