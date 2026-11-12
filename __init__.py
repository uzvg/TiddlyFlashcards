import importlib

from aqt import mw
from aqt.qt import QAction

from . import config
from .config import load_config, open_config_dialog
from .exceptions import TiddlyFlashcardsError
from .exporter import WikiExporter, WikiManager
from .importer import AnkiSyncer
from .models import compose_model_css
from .utils import (
    ErrorReport,
    confirmDeletion,
    showError,
    showException,
    showReport,
)


def begin_sync():
    assert mw.col is not None
    cfg = load_config()
    css = compose_model_css(cfg)

    try:
        manager = WikiManager(
            cfg,
            WikiExporter(
                cfg.tiddlywiki_path,
                css=css,
                plugin_path=cfg.tiddlywiki_plugin_path,
            ),
        )
        merged = manager.build_export()
    except TiddlyFlashcardsError as exc:
        showError(ErrorReport(exc.title, exc.summary, exc.details))
        return

    service = AnkiSyncer(merged, mw.col)

    try:
        report = service.sync_all(confirm_deletion=confirmDeletion)
    except (ValueError, RuntimeError) as exc:
        # 写阶段无事务：持久化已完成的部分写，避免集合与磁盘不一致。
        mw.col.save()
        showException(
            exc,
            summary=(
                "TiddlyFlashcards stopped during the write phase. "
                "Some changes may already have been applied."
            ),
        )
        return

    if report is None:
        # 用户取消删除：本次同步未做任何写入。
        return

    showReport(report)
    mw.col.save()
    mw.reset()


# Register config action (called once when the add-on loads)
mw.addonManager.setConfigAction(__name__, open_config_dialog)

sync_cards_action = QAction("TiddlyFlashcards Sync", mw)
sync_cards_action.setShortcut("Alt+S")
sync_cards_action.triggered.connect(begin_sync)
mw.form.menuTools.addAction(sync_cards_action)
