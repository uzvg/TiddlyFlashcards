import importlib
from aqt import mw
from aqt.qt import QAction
from .config import load_config, open_config_dialog
from .importer import ModelSynchronizer, NoteSynchronizer, WikiSyncService
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
    service = WikiSyncService(ModelSynchronizer(mw.col), NoteSynchronizer(mw.col))
    service.sync(manager.export_all())

# Register config action (called once when the add-on loads)
mw.addonManager.setConfigAction(__name__, open_config_dialog)

sync_cards_action = QAction("TiddlyFlashcards Sync Cards", mw)
sync_cards_action.triggered.connect(begin_sync)
mw.form.menuTools.addAction(sync_cards_action)

# Reload config diaglog config
reload_config_dialog_action = QAction("Reload Config Dialg(For Debugging)", mw)
reload_config_dialog_action.triggered.connect(reload_config_dialog)
mw.form.menuTools.addAction(reload_config_dialog_action)