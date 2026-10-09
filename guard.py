from __future__ import annotations

import aqt
from aqt import gui_hooks, mw
from aqt.addcards import AddCards
from aqt.utils import showWarning

from anki.notes import Note
from anki.models import NotetypeDict
from typing import Set


# =========================
# 配置
# =========================

def get_managed_models() -> Set[str]:
    if mw.col is None:
        return set()
    managed_models = {
        m["name"]
        for m in mw.col.models.all()
        if m.get("tf_managed", False)
    }
    return managed_models

# =========================
# 判定逻辑
# =========================

def is_tf_model_name(name: str) -> bool:
    return name in get_managed_models()


def is_tf_note(note: Note | None) -> bool:
    return note is not None and "TFNoteId" in note.keys()

# =========================
# Warning
# =========================

def show_tf_warning(note_type_name: str) -> None:
    showWarning(
        f"You are using the '{note_type_name}' note type.\n\n"
        "This note type is managed by TiddlyFlashcards.\n"
        "Manual edits or additions are NOT allowed.\n\n"
        "All changes will be overwritten on the next sync."
    )


# =========================
# 数据层拦截（核心）
# =========================

def patch_update_note() -> None:
    """
    拦截所有 note 修改（最关键）
    """
    original_update_note = mw.col.update_note

    def wrapped_update(note: Note):
        if is_tf_note(note):
            show_tf_warning(note.model()["name"])
            return  # ❗ 阻止保存

        return original_update_note(note)

    mw.col.update_note = wrapped_update


# =========================
# UI 层 Hook（辅助）
# =========================

def register_ui_hooks() -> None:
    """
    提示 + 阻止添加
    """

    # ---------- Add Dialog 初始化 ----------
    def on_add_init(add_cards_dialog: AddCards) -> None:
        name = add_cards_dialog.notetype_chooser.selected_notetype_name()
        if is_tf_model_name(name):
            show_tf_warning(name)

    # ---------- 切换 NoteType ----------
    def on_change_note_type(_old: NotetypeDict, new: NotetypeDict) -> None:
        if is_tf_model_name(new["name"]):
            show_tf_warning(new["name"])

    # ---------- 阻止添加 ----------
    def on_add_cards_will_add(problem: str | None, note: Note):
        if note is None:
            return problem
    
        if is_tf_note(note):
            show_tf_warning(note.model()["name"])
            return "Managed note type cannot be added manually."
    
        return problem

    # 注册 hooks
    gui_hooks.add_cards_did_init.append(on_add_init)

    if hasattr(gui_hooks, "add_cards_did_change_note_type"):
        gui_hooks.add_cards_did_change_note_type.append(on_change_note_type)  # type: ignore

    gui_hooks.add_cards_will_add_note.append(on_add_cards_will_add)


# =========================
# 总入口
# =========================

def register_note_guard() -> None:
    """
    注册所有保护逻辑
    """
    patch_update_note()
    register_ui_hooks()
