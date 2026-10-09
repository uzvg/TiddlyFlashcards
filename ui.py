"""Inline table editor for TiddlyWiki entries in an Anki add-on.

Uses only Anki's bundled Qt binding (aqt.qt), the standard library, and
Anki's add-on config API.

Expected config.json:
{
  "wikis": [
    {
      "name": "PersonalWiki",
      "path": "/home/user/Documents/wikis/WikiSpace",
      "url": "http://127.0.0.1:9191/"
    }
  ]
}

Example menu integration in __init__.py:
    from aqt import mw
    from aqt.qt import QAction
    from .wiki_manager import show_wiki_manager

    action = QAction("TiddlyWiki 配置", mw)
    action.triggered.connect(lambda: show_wiki_manager(mw))
    mw.form.menuTools.addAction(action)
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse
import webbrowser

from aqt import mw
from aqt.qt import (
    Qt,
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


COLUMNS = ("Wiki 名称（唯一 ID）", "工作目录", "Permanent Link")


def _addon_name() -> str:
    # The config API expects the add-on package name (top-level package).
    return __name__.split(".")[0]


def _normalize_url(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("请填写 Permanent Link。")
    if "://" not in value:
        value = "http://" + value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("请输入有效的网址，例如 http://127.0.0.1:9191/")
    return value


def _read_wikis() -> list[dict]:
    config = mw.addonManager.getConfig(_addon_name()) or {}
    items = config.get("wikis", [])
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name, path, url = item.get("name"), item.get("path"), item.get("url")
        if all(isinstance(value, str) for value in (name, path, url)):
            result.append({"name": name, "path": path, "url": url})
    return result


def _write_wikis(wikis: list[dict]) -> None:
    # Preserve other add-on settings, if present.
    config = mw.addonManager.getConfig(_addon_name()) or {}
    config["wikis"] = wikis
    mw.addonManager.writeConfig(_addon_name(), config)


class WikiManagerDialog(QDialog):
    """Single-table CRUD UI: each row represents one configured wiki."""

    def __init__(self, parent=None):
        super().__init__(parent or mw)
        self.setWindowTitle("TiddlyWiki 配置")
        self.resize(1080, 520)
        self._wikis = _read_wikis()
        self._dirty = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(10)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.table.itemChanged.connect(self._on_item_changed)

        root.addWidget(self.table, 1)

        toolbar = QHBoxLayout()
        self.add_button = QPushButton("添加 Wiki")
        self.delete_button = QPushButton("删除所选")
        self.open_button = QPushButton("打开 Wiki")
        self.browse_button = QPushButton("选择工作目录…")
        self.save_button = QPushButton("保存更改")
        self.cancel_button = QPushButton("放弃更改")
        self.add_button.clicked.connect(self._add_row)
        self.delete_button.clicked.connect(self._delete_selected)
        self.open_button.clicked.connect(self._open_selected)
        self.browse_button.clicked.connect(self._browse_selected)
        self.save_button.clicked.connect(self._save)
        self.cancel_button.clicked.connect(self._discard)
        toolbar.addWidget(self.add_button)
        toolbar.addWidget(self.delete_button)
        toolbar.addWidget(self.open_button)
        toolbar.addWidget(self.browse_button)
        toolbar.addStretch(1)
        toolbar.addWidget(self.cancel_button)
        toolbar.addWidget(self.save_button)
        root.addLayout(toolbar)

        self.status = QLineEdit()
        self.status.setReadOnly(True)
        self.status.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.status.setPlaceholderText(
            "双击单元格即可编辑；工作目录列可通过“选择目录…”按钮设置。"
        )
        self.status.setMaximumHeight(28)
        root.addWidget(self.status)

        self._populate_table()
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self._update_buttons()

    def _populate_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for wiki in self._wikis:
            self._insert_row(wiki)
        self.table.blockSignals(False)
        self._dirty = False
        self._update_buttons()

    def _insert_row(self, wiki: dict):
        row = self.table.rowCount()
        self.table.insertRow(row)

        values = (wiki["name"], wiki["path"], wiki["url"])
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setData(Qt.ItemDataRole.UserRole, value)
            if column == 0:
                item.setToolTip("唯一 ID；保存时不可与其他 Wiki 重名。")
            elif column == 1:
                item.setToolTip("TiddlyWiki 工作目录")
            else:
                item.setToolTip("点击“打开 Wiki”可使用系统默认浏览器访问。")
            self.table.setItem(row, column, item)

    def _browse_for_row(self, row: int):
        if row < 0 or row >= self.table.rowCount():
            return
        current = self.table.item(row, 1).text().strip() or str(Path.home())
        directory = QFileDialog.getExistingDirectory(
            self, "选择 TiddlyWiki 工作目录", current
        )
        if directory:
            self.table.item(row, 1).setText(directory)

    def _browse_selected(self):
        row = self._selected_row()
        if row < 0:
            QMessageBox.information(self, "选择工作目录", "请先选择一个 Wiki。")
            return
        self._browse_for_row(row)

    def _on_item_changed(self, _item):
        self._dirty = True
        self.status.setText("有未保存的更改。")
        self._update_buttons()

    def _selected_row(self) -> int:
        return self.table.currentRow()

    def _add_row(self):
        row = self.table.rowCount()
        self._insert_row({"name": "", "path": "", "url": ""})
        self.table.setCurrentCell(row, 0)
        self.table.editItem(self.table.item(row, 0))
        self._dirty = True
        self.status.setText("已添加空白行，请填写名称、工作目录和 Permanent Link。")
        self._update_buttons()

    def _delete_selected(self):
        row = self._selected_row()
        if row < 0:
            return
        name = self.table.item(row, 0).text().strip() or "未命名 Wiki"
        answer = QMessageBox.question(
            self,
            "删除 Wiki 配置",
            f"确定删除“{name}”这一行吗？\n\n"
            "此操作只删除配置，不会删除磁盘上的 Wiki 文件。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.table.removeRow(row)
        self._dirty = True
        self.status.setText("已删除该行；点击“保存更改”后生效。")
        self._update_buttons()

    def _validate_table(self) -> list[dict] | None:
        wikis = []
        names = set()
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 0).text().strip()
            path = self.table.item(row, 1).text().strip()
            raw_url = self.table.item(row, 2).text().strip()

            if not name or not path or not raw_url:
                QMessageBox.warning(
                    self,
                    "信息不完整",
                    f"第 {row + 1} 行存在空字段。\n"
                    "请填写 Wiki 名称、工作目录和 Permanent Link。",
                )
                self.table.setCurrentCell(row, 0)
                return None
            if name in names:
                QMessageBox.warning(
                    self, "名称重复", f"Wiki 名称“{name}”重复；名称必须唯一。"
                )
                self.table.setCurrentCell(row, 0)
                return None
            names.add(name)

            try:
                url = _normalize_url(raw_url)
            except ValueError as exc:
                QMessageBox.warning(self, "网址无效", f"第 {row + 1} 行：{exc}")
                self.table.setCurrentCell(row, 2)
                return None

            path = str(Path(path).expanduser())
            if not Path(path).is_dir():
                answer = QMessageBox.question(
                    self,
                    "目录不存在",
                    f"第 {row + 1} 行的工作目录不存在：\n{path}\n\n仍然保存吗？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    self.table.setCurrentCell(row, 1)
                    return None
            wikis.append({"name": name, "path": path, "url": url})
        return wikis

    def _save(self):
        # End in-progress cell editing before reading values.
        self.table.setFocus()
        wikis = self._validate_table()
        if wikis is None:
            return
        try:
            _write_wikis(wikis)
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", f"无法保存插件配置：\n{exc}")
            return
        self._wikis = wikis
        self._populate_table()
        self.status.setText(f"已保存 {len(wikis)} 个 Wiki。")

    def _discard(self):
        if self._dirty:
            answer = QMessageBox.question(
                self,
                "放弃更改",
                "放弃所有尚未保存的更改吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._wikis = _read_wikis()
        self._populate_table()
        self.status.setText("已重新载入已保存的配置。")

    def _open_selected(self):
        row = self._selected_row()
        if row < 0:
            QMessageBox.information(self, "打开 Wiki", "请先选择一个 Wiki。")
            return
        url = self.table.item(row, 2).text().strip()
        try:
            url = _normalize_url(url)
        except ValueError as exc:
            QMessageBox.warning(self, "网址无效", str(exc))
            return
        if not webbrowser.open(url):
            QMessageBox.warning(
                self, "无法打开链接", f"请检查系统浏览器配置，或手动访问：\n{url}"
            )

    def _update_buttons(self):
        has_selection = self._selected_row() >= 0
        self.delete_button.setEnabled(has_selection)
        self.open_button.setEnabled(has_selection)
        self.browse_button.setEnabled(has_selection)
        self.save_button.setEnabled(self._dirty)
        self.cancel_button.setEnabled(self._dirty)


_dialog: WikiManagerDialog | None = None


def show_wiki_manager(parent=None):
    """Open the manager as a modeless dialog and keep it alive."""
    global _dialog
    if _dialog is not None:
        _dialog.close()
        _dialog.deleteLater()
    _dialog = WikiManagerDialog(parent or mw)
    _dialog.show()
    _dialog.raise_()
    _dialog.activateWindow()
    return _dialog