import importlib
from aqt import mw
from aqt.qt import QAction
from .config import load_config, open_config_dialog
from .importer import AnkiModelRepository, AnkiNoteRepository, AnkiSyncer
from .exporter import WikiExporter, WikiManager
from .models import compose_model_css

def reload_config_dialog():
    importlib.reload(config)
    open_config_dialog()

def begin_sync():
    assert mw.col is not None
    cfg = load_config()
    css = compose_model_css(cfg)
    manager = WikiManager(cfg, WikiExporter(cfg.tiddlywiki_path, css=css))

    exports = manager.export_all()
    model_sources = [em.model_info for exp in exports for em in exp.model_sources]
    note_sources = [en.note_info for exp in exports for en in exp.note_sources]

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

# Reload config diaglog config
reload_config_dialog_action = QAction("Reload Config Dialg(For Debugging)", mw)
reload_config_dialog_action.triggered.connect(reload_config_dialog)
mw.form.menuTools.addAction(reload_config_dialog_action)