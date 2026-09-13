"""
TiddlyFlashcards Plugin Config Dialog
"""

import shutil
import subprocess
from dataclasses import dataclass, field

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


@dataclass(slots=True)
class WikiSource:
    # Wiki来源，（会被序列化到 Anki 的 add-on config 里）。
    path: str
    type: str  # "folder" or "html"
    enabled: bool = True

    @classmethod
    def from_dict(cls, raw: dict) -> "WikiSource":
        return cls(
            path=raw.get("path", ""),
            type=raw.get("type", "folder"),
            enabled=raw.get("enabled", True),
        )

    def to_dict(self) -> dict:
        return {"path": self.path, "type": self.type, "enabled": self.enabled}


@dataclass(slots=True)
class PluginConfig:
    # 用 dataclass 做“内存中的配置模型”，读取/保存时再转成 dict（更好类型提示、也更易重构）。
    tiddlywiki_bin: str = ""

    # 可变默认值必须用 default_factory，否则会在多个实例之间共享同一个 list。
    wikis: list[WikiSource] = field(default_factory=list)
    card_css: str = ""
    api_url: str = "http://127.0.0.1:8080"

    @classmethod
    def from_dict(cls, raw: dict) -> "PluginConfig":
        # 缺失字段回退到 DEFAULT_CONFIG，兼容手改 config.json 造成的缺键情况。
        def _get(key: str):
            return raw.get(key, DEFAULT_CONFIG[key])

        return cls(
            tiddlywiki_bin=_get("tiddlywiki_bin"),
            wikis=[WikiSource.from_dict(w) for w in _get("wikis")],
            card_css=_get("card_css"),
            api_url=_get("api_url"),
        )

    def to_dict(self) -> dict:
        return {
            "tiddlywiki_bin": self.tiddlywiki_bin,
            "wikis": [w.to_dict() for w in self.wikis],
            "card_css": self.card_css,
            "api_url": self.api_url,
        }


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict = {
    # 落盘到 Anki 配置系统里的 dict 结构。
    "tiddlywiki_bin": "",
    "wikis": [],
    "card_css": "",
    "api_url": "http://127.0.0.1:8080",
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


def resolve_tiddlywiki_bin(path: str) -> str:
    """路径为空时自动探测；返回实际可用的二进制路径。"""
    return path.strip() or shutil.which("tiddlywiki") or "tiddlywiki"


def check_tiddlywiki(path: str) -> tuple[bool, str]:
    """
    Run `tiddlywiki --version` with the given binary path.
    If path is empty, auto-detect via shutil.which.
    Returns (success: bool, message: str).
    """

    bin_path = resolve_tiddlywiki_bin(path)

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
    button: QPushButton | None = None,
    label_width: int = 150,
) -> QHBoxLayout:
    """“label + 输入框(+按钮)” 水平行，供 Environment/API tab 复用。"""
    row = QHBoxLayout()
    row.setSpacing(8)

    lab = QLabel(label)
    lab.setFixedWidth(label_width)
    row.addWidget(lab)

    row.addWidget(edit, stretch=1)

    if button is not None:
        button.setFixedWidth(70)
        row.addWidget(button)

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
# Environment Tab
# ---------------------------------------------------------------------------


class EnvironmentTab(_BaseTab):
    def _build_ui(self) -> None:
        layout = self._vbox()

        self.bin_edit = QLineEdit(self._cfg.tiddlywiki_bin)
        self.bin_edit.setPlaceholderText("Leave empty to auto-detect")
        test_btn = QPushButton("Test")
        test_btn.clicked.connect(self._on_test)

        layout.addLayout(_field_row("TiddlyWiki Binary", self.bin_edit, test_btn))
        layout.addStretch()

    def _on_test(self) -> None:
        ok, msg = check_tiddlywiki(self.bin_edit.text())
        if ok:
            QMessageBox.information(
                self,
                "Success",
                f"TiddlyWiki detected successfully.\nVersion:\t{msg}",
            )
        else:
            QMessageBox.critical(
                self,
                "Error",
                f"TiddlyWiki check failed.\n{msg}",
            )

    # -- public API --
    def apply_to(self, cfg: PluginConfig) -> None:
        cfg.tiddlywiki_bin = resolve_tiddlywiki_bin(self.bin_edit.text())


# ---------------------------------------------------------------------------
# Wikis Tab
# ---------------------------------------------------------------------------

_COL_ENABLED = 0
_COL_PATH = 1
_COL_TYPE = 2


class WikisTab(_BaseTab):
    spacing = 8

    def _build_ui(self) -> None:
        layout = self._vbox()

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["", "Path", "Type"])
        header = self.table.horizontalHeader()
        assert header is not None  # QTableWidget 始终有 header
        header.setSectionResizeMode(
            _COL_ENABLED, QHeaderView.ResizeMode.ResizeToContents
        )
        header.setSectionResizeMode(_COL_PATH, QHeaderView.ResizeMode.Stretch)
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
            ("Add HTML", self._on_add_html),
            ("Add Folder", self._on_add_folder),
            ("Remove", self._on_remove),
        ):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)

        layout.addLayout(btn_row)

    def _populate(self) -> None:
        self.table.setRowCount(0)
        for wiki in self._cfg.wikis:
            self._add_row(wiki.path, wiki.type, wiki.enabled)

    def _add_row(self, path: str, wiki_type: str, enabled: bool = True) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setCellWidget(row, _COL_ENABLED, self._checkbox_cell(enabled))
        self.table.setItem(row, _COL_PATH, QTableWidgetItem(path))
        self.table.setItem(row, _COL_TYPE, QTableWidgetItem(wiki_type))

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

    def _on_add_html(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select TiddlyWiki HTML file",
            "",
            "HTML files (*.html *.htm);;All files (*)",
        )
        if path:
            self._add_row(path, "html")

    def _on_add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "Select TiddlyWiki folder",
            "",
        )
        if path:
            self._add_row(path, "folder")

    def _on_remove(self) -> None:
        selected = self.table.selectedItems()
        if not selected:
            return
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    # -- public API --
    def apply_to(self, cfg: PluginConfig) -> None:
        wikis: list[WikiSource] = []
        for row in range(self.table.rowCount()):
            path_item = self.table.item(row, _COL_PATH)
            type_item = self.table.item(row, _COL_TYPE)
            wrapper = self.table.cellWidget(row, _COL_ENABLED)
            enabled = True
            if wrapper is not None:
                chk = wrapper.findChild(QCheckBox)
                if chk is not None:
                    enabled = chk.isChecked()
            if path_item:
                wikis.append(
                    WikiSource(
                        path=path_item.text(),
                        type=type_item.text() if type_item else "folder",
                        enabled=enabled,
                    )
                )
        cfg.wikis = wikis


# ---------------------------------------------------------------------------
# Card Style Tab
# ---------------------------------------------------------------------------


class CardStyleTab(_BaseTab):
    spacing = 8

    def _build_ui(self) -> None:
        layout = self._vbox()

        layout.addWidget(QLabel("Card CSS"))

        self.css_edit = QPlainTextEdit(self._cfg.card_css)
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
        cfg.card_css = self.css_edit.toPlainText()


# ---------------------------------------------------------------------------
# API Tab
# ---------------------------------------------------------------------------


class ApiTab(_BaseTab):
    def _build_ui(self) -> None:
        layout = self._vbox()

        self.url_edit = QLineEdit(self._cfg.api_url)
        self.url_edit.setPlaceholderText("http://127.0.0.1:8080")
        layout.addLayout(_field_row("Local API URL", self.url_edit, label_width=120))

        hint = QLabel(
            "Used to open tiddlers in the browser, e.g.:\n"
            "http://localhost:8080/#/tiddler/&lt;title&gt;"
        )
        hint.setStyleSheet("color: gray; font-size: 11px;")
        hint.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(hint)

        layout.addStretch()

    # -- public API --
    def apply_to(self, cfg: PluginConfig) -> None:
        cfg.api_url = self.url_edit.text().strip()


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

        self._env_tab = EnvironmentTab(self._cfg)
        self._wikis_tab = WikisTab(self._cfg)
        self._style_tab = CardStyleTab(self._cfg)
        self._api_tab = ApiTab(self._cfg)

        tabs.addTab(self._env_tab, "Environment")
        tabs.addTab(self._wikis_tab, "Wikis")
        tabs.addTab(self._style_tab, "Card Style")
        tabs.addTab(self._api_tab, "API")

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
        self._env_tab.apply_to(self._cfg)
        self._wikis_tab.apply_to(self._cfg)
        self._style_tab.apply_to(self._cfg)
        self._api_tab.apply_to(self._cfg)
        save_config(self._cfg)
        self.accept()


# ---------------------------------------------------------------------------
# Entry point registered with AddonManager
# ---------------------------------------------------------------------------


def open_config_dialog() -> None:
    dlg = ConfigDialog(mw)
    dlg.exec()
