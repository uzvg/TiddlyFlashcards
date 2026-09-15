"""
Import Flascards into anki from exported data

JSON
 │
 ├── 不存在 → Anki → suspend
 ├── 新存在 → Anki → create
 │              └── status=suspend → create 后 suspend
 └── 都存在
       ├── checksum 不同 → update
       └── status
            ├── suspend + Anki active → suspend
            └── active + Anki suspend → active
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from anki.decks import DeckId
from anki.notes import Note
from anki.collection import Collection
from aqt import mw
from aqt.utils import showInfo

from .exporter import WikiExportSummary

TF_ID_FIELD = "TFNoteId"
TF_CHECKSUM_FIELD = "TFChecksum"


class ParsedNote:
    """
    Parse json note as ParsedNote class and Compute the card's checksum.
    """

    def __init__(self, tf_id: str, raw: dict):
        self.tf_id = tf_id
        self.fields = raw["fields"]
        self.model_name = raw["modelName"]
        self.deck = raw["deck"]
        self.tags = raw.get("tags", [])
        self.status = raw.get("status", "active")

        self.checksum = self._compute_checksum(raw)

    def _compute_checksum(self, raw: dict) -> str:
        normalized = json.dumps(raw, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

class AnkiNoteStatus(StrEnum):
    ACTIVE = "active"
    SUSPEND = "suspend"
    MIXED = "mixed"

@dataclass
class AnkiNoteInfo:
    note: Note
    tf_id: str
    checksum: str
    status: AnkiNoteStatus

class TiddlyFlashcardsImporter:
    def __init__(
        self,
        data: dict[str, dict],
        wiki_summaries: Sequence[WikiExportSummary] = (),
    ):
        self.data = data
        self.wiki_summaries = wiki_summaries
        self.col = cast(Collection, mw.col)

        self.parsed_notes: dict[str, ParsedNote] = {}
        self.anki_index: dict[str, AnkiNoteInfo] = {}

        self.to_create: list[ParsedNote] = []
        self.to_update: list[ParsedNote] = []
        self.to_suspend: list[AnkiNoteInfo] = []
        self.to_active: list[AnkiNoteInfo] = []
        self.to_delete: list[AnkiNoteInfo] = []

    # =========================
    # Entry
    # =========================

    def run(self, dry_run: bool = False):
        self._parse_json()
        self._build_anki_index()
        self._diff()

        self._apply_create()
        self._apply_update()
        self._apply_suspend()
        self._apply_active()

        self.col.save()

        # 修改完数据库后，需要reset来刷新界面
        mw.reset()

        self._show_summary()

    # =========================
    # Layer 1: Parse JSON
    # =========================

    def _parse_json(self):
        """
        parse json note as ParsedNote class.
        """

        for tf_id, raw in self.data.items():
            self.parsed_notes[tf_id] = ParsedNote(tf_id, raw)

    # =========================
    # Layer 2: Build Index
    # =========================

    def _build_anki_index(self):
        # 搜索所有带 TFNoteID 字段的 note
        note_ids = self.col.find_notes(f'"{TF_ID_FIELD}:*"')

        for nid in note_ids:
            note = self.col.get_note(nid)
            tf_id = note[TF_ID_FIELD]
            checksum = note[TF_CHECKSUM_FIELD]
            status = self._get_note_status(note)

            self.anki_index[tf_id] = AnkiNoteInfo(note, tf_id, checksum,status)

    def _get_note_status(self, note:Note) -> AnkiNoteStatus:
        """返回Note所对应的卡片的挂起状态"""

        cards = note.cards()
        suspended_status = [card.queue == -1 for card in cards]

        if all(suspended_status):
            return AnkiNoteStatus.SUSPEND

        if not any(suspended_status):
            return AnkiNoteStatus.ACTIVE

        return AnkiNoteStatus.MIXED

    # =========================
    # Layer 3: Diff
    # =========================

    def _diff(self):
        json_ids = set(self.parsed_notes.keys())
        anki_ids = set(self.anki_index.keys())

        # 1. Anki中有， JSON中没有 ➡ delete
        for tf_id in anki_ids - json_ids:
            self.to_delete.append(self.anki_index[tf_id])

        # 2. JSON中有，Anki中没有 ➡ create            
        for tf_id in json_ids - anki_ids:
            parsed = self.parsed_notes[tf_id]
            self.to_create.append(parsed)

        # 3. 两边都有，但两方的checksum不一致 ➡ Update
        for tf_id in json_ids & anki_ids:
            parsed = self.parsed_notes[tf_id]
            anki_note = self.anki_index[tf_id]

            if parsed.checksum != anki_note.checksum:
                self.to_update.append(parsed)

            # 3.1 如果JSON中的note标记为suspend，而Anki中的note没有被挂起 ➡ 添加到挂起列表
            if parsed.status == "suspend" and anki_note.status != AnkiNoteStatus.SUSPEND:
                self.to_suspend.append(self.anki_index[tf_id])

            # 3.2 如果 JSON 中的 note 被标记为active，而Anki中的note没有被active ➡ 添加到激活列表
            if parsed.status == "active" and anki_note.status != AnkiNoteStatus.ACTIVE:
                self.to_active.append(self.anki_index[tf_id])

    # =========================
    # Apply: Create
    # =========================

    def _apply_create(self):
        for parsed in self.to_create:
            model = self.col.models.by_name(parsed.model_name)
            if not model:
                raise RuntimeError(f"Model not found: {parsed.model_name}")

            note = self.col.new_note(model)

            # 填字段
            for field_name, value in parsed.fields.items():
                note[field_name] = value

            # 系统字段
            note[TF_ID_FIELD] = parsed.tf_id
            note[TF_CHECKSUM_FIELD] = parsed.checksum

            # tags
            note.tags = parsed.tags

            self.col.add_note(note, self._get_deck_id(parsed.deck))

            # 如果note在第一次添加时就被标记为“suspend”，则需要额外添加到挂起列表
            if parsed.status == "suspend":
                status = AnkiNoteStatus.SUSPEND
                self.to_suspend.append(AnkiNoteInfo(note,parsed.tf_id, parsed.checksum, status))


    # =========================
    # Apply: Update
    # =========================

    def _apply_update(self):
        for parsed in self.to_update:
            info = self.anki_index[parsed.tf_id]
            note = info.note

            self._update_fields(note, parsed.fields)
            self._update_tags(note, parsed.tags)
            self._update_deck(note, parsed.deck)

            # 更新 checksum
            note[TF_CHECKSUM_FIELD] = parsed.checksum

            self.col.update_note(note)

    def _update_fields(self, note: Note, new_fields: dict[str, str]):
        for k, v in new_fields.items():
            note[k] = v

    def _update_tags(self, note: Note, tags: list[str]):
        note.tags = tags

    def _update_deck(self, note: Note, deck_name: str):
        deck_id = self._get_deck_id(deck_name)

        for card in note.cards():
            card.did = deck_id
            self.col.update_card(card)

    # =========================
    # Apply: Suspend
    # =========================

    def _apply_suspend(self):
        card_ids = []

        for info in self.to_suspend:
            for card in info.note.cards():
                card_ids.append(card.id)

        if card_ids:
            self.col.sched.suspend_cards(card_ids)

    # =========================
    # Apply: Active
    # =========================

    def _apply_active(self):
        card_ids = []

        for info in self.to_active:
            for card in info.note.cards():
                card_ids.append(card.id)

        if card_ids:
            self.col.sched.unsuspend_cards(card_ids)

    # =========================
    # Apply: Delete
    # =========================

    def _apply_delete(self):
        note_ids = []

        for info in self.to_delete:
            note_ids.append(info.note.id)

        if note_ids:
            self.col.remove_notes(note_ids)

    # =========================
    # Utils
    # =========================

    def _get_deck_id(self, deck_name: str) -> DeckId:
        """
        获得deck_id，用于update_deck
        如果deck存在，返回对应的deck id
        如果deck不存在，创建新的deck，并返回deck_id
        """
        deck = self.col.decks.by_name(deck_name)
        if deck:
            return DeckId(deck["id"])
        did = self.col.decks.id(deck_name)
        assert did is not None
        return did

    def _show_summary(self):
        export_summary = "\n".join(
            f"- {summary.path}: {summary.note_count} cards"
            for summary in self.wiki_summaries
        )
        if export_summary:
            export_summary = f"\n\nWiki exports:\n{export_summary}"

        msg = (
            "TiddlyFlashcards Import Summary:\n"
            f"Create: {len(self.to_create)}\n"
            f"Update: {len(self.to_update)}\n"
            f"Suspend: {len(self.to_suspend)}"
            f"{export_summary}"
        )
        showInfo(msg)