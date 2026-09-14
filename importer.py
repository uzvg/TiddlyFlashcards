"""
Import Flascards into anki from exported data
"""

import json
from collections.abc import Sequence

from anki.notes import Note
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

        self.checksum = self._compute_checksum(raw)

    def _compute_checksum(self, raw: dict) -> str:
        normalized = json.dumps(raw, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class AnkiNoteInfo:
    def __init__(self, note: Note, tf_id: str, checksum: str):
        self.note = note
        self.tf_id = tf_id
        self.checksum = checksum


class TiddlyFlashcardsImporter:
    def __init__(
        self,
        data: dict[str, dict],
        wiki_summaries: Sequence[WikiExportSummary] = (),
    ):
        self.data = data
        self.wiki_summaries = wiki_summaries
        self.col = mw.col

        self.parsed_notes: dict[str, ParsedNote] = {}
        self.anki_index: dict[str, AnkiNoteInfo] = {}

        self.to_create: list[ParsedNote] = []
        self.to_update: list[ParsedNote] = []
        self.to_suspend: list[AnkiNoteInfo] = []

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
            self.anki_index[tf_id] = AnkiNoteInfo(note, tf_id, checksum)

    # =========================
    # Layer 3: Diff
    # =========================

    def _diff(self):
        json_ids = set(self.parsed_notes.keys())
        anki_ids = set(self.anki_index.keys())

        # Create
        for tf_id in json_ids - anki_ids:
            self.to_create.append(self.parsed_notes[tf_id])

        # Suspend
        for tf_id in anki_ids - json_ids:
            self.to_suspend.append(self.anki_index[tf_id])

        # Update
        for tf_id in json_ids & anki_ids:
            parsed = self.parsed_notes[tf_id]
            anki_note = self.anki_index[tf_id]

            if parsed.checksum != anki_note.checksum:
                self.to_update.append(parsed)

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
    # Utils
    # =========================

    def _get_deck_id(self, deck_name: str) -> int:
        """
        获得deck_id，用于update_deck
        如果deck存在，返回对应的deck id
        如果deck不存在，创建新的deck，并返回deck_id
        """
        deck = self.col.decks.by_name(deck_name)
        if deck:
            return deck["id"]
        return self.col.decks.id(deck_name)

    def _show_summary(self):
        export_summary = "\n".join(
            f"- {summary.path}: {summary.card_count} cards"
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