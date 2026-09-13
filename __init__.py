from aqt import mw
from aqt.qt import QAction
from .config import open_config_dialog
# Register config action (called once when the add-on loads)
mw.addonManager.setConfigAction(__name__, open_config_dialog)
