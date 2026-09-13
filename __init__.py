from aqt import mw
from aqt.qt import QAction
from .config import open_config_dialog
from .models import sync_models
def begin_sync_models():
    sync_models(mw.col)
# Register config action (called once when the add-on loads)
mw.addonManager.setConfigAction(__name__, open_config_dialog)
action = QAction("TiddlyFlashcards Sync models", mw)
action.triggered.connect(begin_sync_models)
mw.form.menuTools.addAction(action)
