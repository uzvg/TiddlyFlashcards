from aqt import mw
from aqt.qt import QAction
from .config import open_config_dialog
from .models import sync_models
from .importer import TiddlyFlashcardsImporter
from .exporter import export_all

def begin_sync():
    sync_models(mw.col)
    result = export_all()
    TiddlyFlashcardsImporter(result.data, result.wiki_summaries).run()

def begin_sync_models():
    sync_models(mw.col)

# Register config action (called once when the add-on loads)
mw.addonManager.setConfigAction(__name__, open_config_dialog)

sync_cards_action = QAction("TiddlyFlashcards Sync Cards", mw)
sync_cards_action.triggered.connect(begin_sync)
mw.form.menuTools.addAction(sync_cards_action)

sync_models_action = QAction("TiddlyFlashcards Sync Models", mw)
sync_models_action.triggered.connect(begin_sync_models)
mw.form.menuTools.addAction(sync_models_action)
