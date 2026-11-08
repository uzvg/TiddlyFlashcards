import importlib

from aqt import mw
from aqt.qt import QAction

from . import config
from .config import load_config, open_config_dialog
from .exceptions import TiddlyFlashcardsError
from .exporter import WikiExporter, WikiManager
from .importer import AnkiModelRepository, AnkiNoteRepository, AnkiSyncer
from .models import compose_model_css
from .utils import ErrorReport, showError


def reload_config_dialog():
    importlib.reload(config)
    open_config_dialog()


def show_sync_error_demo():
    # 演示错误对话框：标题常驻、详情折叠、可一键复制完整报告。
    try:
        raise RuntimeError("Failed to write note 'demo-note-1' to the collection.")
    except RuntimeError as exc:
        report = ErrorReport.from_exception(
            exc,
            title="Sync aborted: could not write note",
            summary=(
                "TiddlyFlashcards stopped before making further changes. "
                "Existing cards were left untouched."
            ),
            details=(
                "Wiki: Example wiki (folder)\n"
                "Notetype: TiddlyFlashcards Basic\n"
                "TFNoteId: demo-note-1\n"
                "Deck: Default\n\n"
                "Backend output:\n"
                "  writing fields ... ok\n"
                "  writing tags ... ok\n"
                "  writing cards ... failed\n\n"
                "This is sample text used to demonstrate a long error report. "
                "Click 'Show Details' to expand it, or 'Copy Debug Info' to "
                "copy the full report together with the Anki environment info."
            ),
        )
    showError(report)


def begin_sync():
    assert mw.col is not None
    cfg = load_config()
    css = compose_model_css(cfg)

    try:
        manager = WikiManager(cfg, WikiExporter(cfg.tiddlywiki_path, css=css))
        merged = manager.build_export()
    except TiddlyFlashcardsError as exc:
        showError(ErrorReport(exc.title, exc.summary, exc.details))
        return

    model_sources = [em.model_info for em in merged.model_sources]
    note_sources = [en.note_info for en in merged.note_sources]

    model_repository = AnkiModelRepository(mw.col)
    note_repository = AnkiNoteRepository(mw.col, model_repository)
    service = AnkiSyncer(model_repository, note_repository)
    service.sync_all(model_sources, note_sources)

    mw.col.save()
    mw.reset()


# Register config action (called once when the add-on loads)
mw.addonManager.setConfigAction(__name__, open_config_dialog)

sync_cards_action = QAction("TiddlyFlashcards Sync Cards", mw)
sync_cards_action.triggered.connect(begin_sync)
mw.form.menuTools.addAction(sync_cards_action)

# Show the error report dialog with sample data (for debugging the dialog)
show_error_dialog_action = QAction(
    "TiddlyFlashcards Show Error Dialog(For Debugging)", mw
)
show_error_dialog_action.triggered.connect(show_sync_error_demo)
mw.form.menuTools.addAction(show_error_dialog_action)

# Reload config diaglog config
reload_config_dialog_action = QAction("Reload Config Dialg(For Debugging)", mw)
reload_config_dialog_action.triggered.connect(reload_config_dialog)
mw.form.menuTools.addAction(reload_config_dialog_action)
