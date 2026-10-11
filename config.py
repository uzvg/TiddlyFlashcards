"""
TiddlyFlashcards Plugin Config Dialog
"""

import os
import shutil
import subprocess
from dataclasses import dataclass, field, asdict

from aqt import mw
from aqt.qt import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    Qt,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


DEFAULT_URL = "http://127.0.0.1:8080"
WIKI_TYPE_HTML = "html"
WIKI_TYPE_FOLDER = "folder"


@dataclass(slots=True)
class WikiSource:
    # Wiki 来源（会被序列化到 Anki 的 add-on config 里）。
    name: str
    path: str  # wiki path
    type: str  # wiki type: WIKI_TYPE_HTML / WIKI_TYPE_FOLDER
    url: str
    enabled: bool = True  # enable wiki or not

    @classmethod
    def from_dict(cls, raw: dict) -> "WikiSource":
        return cls(
            name=raw.get("name", ""),
            path=raw.get("path", ""),
            type=raw.get("type", WIKI_TYPE_FOLDER),
            url=raw.get("url", DEFAULT_URL),
            enabled=raw.get("enabled", True),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class PluginConfig:
    # 用 dataclass 做“内存中的配置模型”。
    tiddlywiki_path: str = ""

    wiki_sources: list[WikiSource] = field(default_factory=list)
    custom_css: str = ""

    @classmethod
    def from_dict(cls, raw: dict) -> "PluginConfig":
        # 缺失字段回退到 DEFAULT_CONFIG，兼容手改 config.json 造成的缺键情况。
        def _get(key: str):
            return raw.get(key, DEFAULT_CONFIG[key])

        return cls(
            tiddlywiki_path=_get("tiddlywiki_path"),
            wiki_sources=[WikiSource.from_dict(w) for w in _get("wiki_sources")],
            custom_css=_get("custom_css"),
        )

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict = {
    # 落盘到 Anki 配置系统里的 dict 结构。
    "tiddlywiki_path": "",
    "wiki_sources": [],
    "custom_css": "",
}


def load_config() -> PluginConfig:
    # Anki Add-on 通过 __name__ 区分不同插件的配置命名空间。
    raw = mw.addonManager.getConfig(__name__) or {}
    # raw(dict) 可能缺键，先与默认值合并再解析成 dataclass。
    # 解包成关键字参数，先解包DEFAULT_CONFIG，再解包raw，后面的raw会覆盖同名的默认键
    #   最终得到一个“默认值+用户配置”的完整 dict。
    return PluginConfig.from_dict({**DEFAULT_CONFIG, **raw})


def save_config(cfg: PluginConfig) -> None:
    # writeConfig 接受 JSON-like 的 dict；所以这里把 dataclass 再转换回 dict。
    # 用户配置，会以JSON文件的方式，被保存到当前文件夹下的meta.json文件中
    mw.addonManager.writeConfig(__name__, cfg.to_dict())


# ---------------------------------------------------------------------------
# TiddlyWiki detection
# ---------------------------------------------------------------------------


def resolve_tiddlywiki_path(path: str) -> str:
    """路径为空时自动探测；返回实际可用的二进制路径。"""
    return path.strip() or shutil.which("tiddlywiki") or "tiddlywiki"


def check_tiddlywiki(path: str) -> tuple[bool, str]:
    """
    Run `tiddlywiki --version` with the given binary path.
    If path is empty, auto-detect via shutil.which.
    Returns (success: bool, message: str).
    """

    bin_path = resolve_tiddlywiki_path(path)

    try:
        # subprocess.run 传 list 参数可以避免 shell=True 带来的转义/注入问题。
        result = subprocess.run(
            [bin_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,  # 返回码由下面的 result.returncode 分支处理。
        )
    except FileNotFoundError:
        return False, "binary not found"
    except subprocess.TimeoutExpired:
        return False, "process timed out"
    except OSError as exc:
        return False, str(exc)

    # 如果执行失败：
    if result.returncode != 0:
        return False, result.stderr.strip() or "Non-zero exit code."
    # 如果执行成功：
    return True, result.stdout.strip() or result.stderr.strip()


# ---------------------------------------------------------------------------
# Tab 基类与通用组件
# ---------------------------------------------------------------------------


def _field_row(
    label: str,
    edit: QLineEdit,
    buttons: QPushButton | list[QPushButton] | None = None,
    label_width: int = 150,
) -> QHBoxLayout:
    """ "label + 输入框(+按钮)" 水平行，供各 tab 复用。"""
    row = QHBoxLayout()
    row.setSpacing(8)

    lab = QLabel(label)
    lab.setFixedWidth(label_width)
    row.addWidget(lab)
    row.addWidget(edit, stretch=1)

    if buttons is not None:
        if not isinstance(buttons, list):
            buttons = [buttons]
        for btn in buttons:
            row.addWidget(btn)

    return row


class _BaseTab(QWidget):
    """tab 公共基类：统一边距/间距，并约定 apply_to() 契约。"""

    margins = (16, 16, 16, 16)
    spacing = 12

    def __init__(self, cfg: PluginConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._build_ui()
        self._populate()

    def _vbox(self) -> QVBoxLayout:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*self.margins)
        layout.setSpacing(self.spacing)
        return layout

    def _build_ui(self) -> None:
        raise NotImplementedError

    def _populate(self) -> None:
        # 默认无预填逻辑；WikisTab 覆写以把当前配置填进表格。
        pass

    def apply_to(self, cfg: PluginConfig) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Wikis Tab
# ---------------------------------------------------------------------------

_COL_ENABLED = 0
_COL_NAME = 1
_COL_PATH = 2
_COL_API = 3
_COL_TYPE = 4


def detect_wiki_type(path: str) -> tuple[str | None, str]:
    """Validate a wiki path and return (wiki_type, error_message).

    HTML file  -> (WIKI_TYPE_HTML, "")
    directory  -> checks for tiddlywiki.info, then (WIKI_TYPE_FOLDER, "")
    otherwise  -> (None, reason)
    """
    if os.path.isfile(path) and path.lower().endswith((".html", ".htm")):
        return WIKI_TYPE_HTML, ""
    if os.path.isdir(path):
        if os.path.isfile(os.path.join(path, "tiddlywiki.info")):
            return WIKI_TYPE_FOLDER, ""
        return None, "Not a TiddlyWiki working directory (tiddlywiki.info not found)."
    return None, "Path is neither an HTML file nor an existing directory."


class _WikiDialog(QDialog):
    """Add/Edit dialog for a single WikiSource. Validates path on accept."""

    def __init__(
        self, wiki: WikiSource | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(
            "Edit Wiki Source" if wiki is not None else "Add Wiki Source"
        )
        self.resize(520, 0)
        self._result: WikiSource | None = None
        self._enabled = wiki.enabled if wiki is not None else True

        name = wiki.name if wiki is not None else ""
        path = wiki.path if wiki is not None else ""
        api = wiki.url if wiki is not None else DEFAULT_URL

        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText("e.g. My TiddlyWiki")

        self.path_edit = QLineEdit(path)
        html_btn = QPushButton("HTML file...")
        html_btn.setFixedWidth(95)
        html_btn.clicked.connect(self._pick_html_file)
        folder_btn = QPushButton("Folder...")
        folder_btn.setFixedWidth(75)
        folder_btn.clicked.connect(self._pick_folder)

        self.api_edit = QLineEdit(api)
        self.api_edit.setPlaceholderText(DEFAULT_URL)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        root.addLayout(_field_row("Wiki name", self.name_edit))

        root.addLayout(_field_row("Wiki path", self.path_edit, [html_btn, folder_btn]))

        root.addLayout(_field_row("API URL", self.api_edit))

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        root.addLayout(btn_row)

    def _pick_html_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select TiddlyWiki HTML file",
            "",
            "HTML files (*.html *.htm);;All files (*)",
        )
        if path:
            self.path_edit.setText(path)

    def _pick_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select a TiddlyWiki folder")
        if path:
            self.path_edit.setText(path)

    def accept(self) -> None:
        name = self.name_edit.text().strip()
        path = self.path_edit.text().strip()
        api = self.api_edit.text().strip()

        if not name:
            QMessageBox.warning(self, "Missing field", "Wiki name cannot be empty.")
            return
        if not path:
            QMessageBox.warning(self, "Missing field", "Wiki path cannot be empty.")
            return

        wiki_type, msg = detect_wiki_type(path)
        if wiki_type is None:
            QMessageBox.warning(self, "Invalid path", msg)
            return

        self._result = WikiSource(
            name=name,
            path=path,
            type=wiki_type,
            url=api or DEFAULT_URL,
            enabled=self._enabled,
        )
        super().accept()

    def result_wiki(self) -> WikiSource:
        # 仅在 exec() 返回 Accepted 后调用；届时 _result 必然非空。
        assert self._result is not None
        return self._result


class WikisTab(_BaseTab):
    spacing = 8

    def _build_ui(self) -> None:
        layout = self._vbox()

        self.bin_edit = QLineEdit(self._cfg.tiddlywiki_path)
        self.bin_edit.setPlaceholderText("Leave empty to auto-detect")
        test_btn = QPushButton("Test")
        test_btn.clicked.connect(self._on_test)
        layout.addLayout(_field_row("TiddlyWiki Binary", self.bin_edit, test_btn))

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["", "Name", "Path", "API URL", "Type"])
        header = self.table.horizontalHeader()
        assert header is not None  # QTableWidget 始终有 header
        header.setSectionResizeMode(
            _COL_ENABLED, QHeaderView.ResizeMode.ResizeToContents
        )
        header.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_PATH, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_API, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_TYPE, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        v_header = self.table.verticalHeader()
        assert v_header is not None
        v_header.setVisible(False)
        layout.addWidget(self.table)

        # ── buttons (right-aligned) ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        for text, slot in (
            ("Add", self._on_add),
            ("Edit", self._on_edit),
            ("Delete", self._on_remove),
        ):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)

        layout.addLayout(btn_row)

    def _populate(self) -> None:
        self.table.setRowCount(0)
        for wiki in self._cfg.wiki_sources:
            self._append_row(wiki)

    def _append_row(self, wiki: WikiSource) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._set_row(row, wiki)

    def _set_row(self, row: int, wiki: WikiSource) -> None:
        self.table.setCellWidget(row, _COL_ENABLED, self._checkbox_cell(wiki.enabled))
        self.table.setItem(row, _COL_NAME, QTableWidgetItem(wiki.name))
        self.table.setItem(row, _COL_PATH, QTableWidgetItem(wiki.path))
        self.table.setItem(row, _COL_API, QTableWidgetItem(wiki.url))
        self.table.setItem(row, _COL_TYPE, QTableWidgetItem(wiki.type))

    def _row_to_wiki(self, row: int) -> WikiSource:
        def _text(col: int) -> str:
            item = self.table.item(row, col)
            return item.text() if item is not None else ""

        enabled = True
        wrapper = self.table.cellWidget(row, _COL_ENABLED)
        if wrapper is not None:
            chk = wrapper.findChild(QCheckBox)
            if chk is not None:
                enabled = chk.isChecked()
        return WikiSource(
            name=_text(_COL_NAME),
            path=_text(_COL_PATH),
            url=_text(_COL_API),
            type=_text(_COL_TYPE) or WIKI_TYPE_FOLDER,
            enabled=enabled,
        )

    @staticmethod
    def _checkbox_cell(enabled: bool) -> QWidget:
        # 用中间容器包装，让 checkbox 在单元格内居中显示。
        chk = QCheckBox()
        chk.setChecked(enabled)
        wrapper = QWidget()
        box = QHBoxLayout(wrapper)
        box.addWidget(chk)
        box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.setContentsMargins(0, 0, 0, 0)
        return wrapper

    def _on_test(self) -> None:
        ok, msg = check_tiddlywiki(self.bin_edit.text())
        if ok:
            QMessageBox.information(
                self, "Success", f"TiddlyWiki detected successfully.\nVersion:\t{msg}"
            )
        else:
            QMessageBox.critical(self, "Error", f"TiddlyWiki check failed.\n{msg}")

    def _on_add(self) -> None:
        dlg = _WikiDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._append_row(dlg.result_wiki())

    def _on_edit(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(
                self, "No selection", "Please select a row to edit."
            )
            return
        dlg = _WikiDialog(wiki=self._row_to_wiki(row), parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._set_row(row, dlg.result_wiki())

    def _on_remove(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    # -- public API --
    def apply_to(self, cfg: PluginConfig) -> None:
        cfg.tiddlywiki_path = resolve_tiddlywiki_path(self.bin_edit.text())
        cfg.wiki_sources = [self._row_to_wiki(row) for row in range(self.table.rowCount())]


# ---------------------------------------------------------------------------
# Card Style Tab
# ---------------------------------------------------------------------------


class CardStyleTab(_BaseTab):
    spacing = 8

    def _build_ui(self) -> None:
        layout = self._vbox()

        layout.addWidget(QLabel("Card CSS"))

        self.css_edit = QPlainTextEdit(self._cfg.custom_css)
        self.css_edit.setPlaceholderText(
            "/* CSS injected into Anki card templates */\n"
            ".card {\n"
            "    font-size: 18px;\n"
            "}\n"
            ".card img {\n"
            "    max-width: 100%;\n"
            "}"
        )
        font = self.css_edit.font()
        # NOTE: Qt 的字体 family 更偏“首选字体名”；不同平台上未必有 Courier New。
        font.setFamily("Cascadia Code, Menlo, Monaco, monospace")
        self.css_edit.setFont(font)
        layout.addWidget(self.css_edit, stretch=1)

    # -- public API --
    def apply_to(self, cfg: PluginConfig) -> None:
        cfg.custom_css = self.css_edit.toPlainText()


# ---------------------------------------------------------------------------
# Config Dialog
# ---------------------------------------------------------------------------


class ConfigDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent or mw)
        self.setWindowTitle("TiddlyWiki Flashcards Config")
        self.resize(680, 480)
        self.setMinimumSize(600, 420)

        self._cfg = load_config()
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # ── Tab widget ──
        tabs = QTabWidget()

        self._wikis_tab = WikisTab(self._cfg)
        self._style_tab = CardStyleTab(self._cfg)

        tabs.addTab(self._wikis_tab, "Wikis")
        tabs.addTab(self._style_tab, "Card Style")

        root.addWidget(tabs, stretch=1)

        # ── Bottom buttons ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        save_btn = QPushButton("Save")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        root.addLayout(btn_row)

    def _on_save(self) -> None:
        for tab in (self._wikis_tab, self._style_tab):
            tab.apply_to(self._cfg)
        save_config(self._cfg)
        self.accept()


# ---------------------------------------------------------------------------
# Entry point registered with AddonManager
# ---------------------------------------------------------------------------


def open_config_dialog() -> None:
    ConfigDialog().exec()