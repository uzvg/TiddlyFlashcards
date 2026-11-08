"""
Anki 内部数据仓库与同步器。

本模块把外部导出的结构化数据（ModelSource / NoteSource）同步进 Anki
Collection。三层职责划分：

    AnkiModelRepository   -- Anki Notetype 的读取与 CRUD（Anki 内部数据仓库）
    AnkiNoteRepository    -- Anki Note 的读取与 CRUD，以及挂起状态同步
    AnkiSyncer            -- 基于集合差集的同步编排，不直接触碰 Anki 内部字典/数据库

约定：
- Notetype 以 `name` 为唯一匹配键；Note 以 `NoteSource.tf_id`（存储在 Anki
  Note 的 `TFNoteId` 字段）为唯一匹配键。
- 所有仓库类的 Collection 均通过依赖注入传入，不在类内硬编码全局 `mw.col`。
- 同步算法默认不删除外部列表中缺失的对象；删除仅当显式启用删除策略时执行。
"""

from anki.collection import Collection
from anki.consts import QUEUE_TYPE_SUSPENDED
from anki.decks import DeckId
from anki.models import NotetypeDict
from anki.notes import Note, NoteId
from aqt.utils import showInfo

from .exporter import WikiExportData
from .models import FieldSource, ModelSource, NoteSource, TemplateSource

# NoteSource.tf_id 在 Anki Note 中存储的字段名。
TF_ID_FIELD = "TFNoteId"


def _escape_search_value(value: str) -> str:
    """转义用于 Anki 搜索查询的字段值（反斜杠与双引号）。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------------------
# AnkiModelRepository
# ---------------------------------------------------------------------------


class AnkiModelRepository:
    """负责 Anki 内部 Notetype 的读取与增删改查。"""

    def __init__(self, col: Collection) -> None:
        self.col = col

    # -- 读取 --

    def get_all(self) -> list[ModelSource]:
        """读取所有 Notetype 并转换为 ModelSource。"""
        return [self._to_source(m) for m in self.col.models.all()]

    def get_by_name(self, name: str) -> ModelSource | None:
        """按名称查询 Notetype，不存在时返回 None。"""
        model = self.col.models.by_name(name)
        return self._to_source(model) if model is not None else None

    # -- 写入 --

    def create(self, source: ModelSource) -> None:
        """创建 Notetype。Anki 内部属性（id/ord/mod/req）由 Anki 生成，不覆盖。"""
        if self.col.models.by_name(source.name) is not None:
            raise ValueError(f"Notetype already exists: {source.name}")

        mm = self.col.models
        model = mm.new(source.name)
        model["type"] = source.type
        model["sortf"] = source.sortf
        model["css"] = source.css

        for f in source.flds:
            field = mm.new_field(f.name)
            field["collapsed"] = f.collapsed
            field["prevent_deletion"] = f.prevent_deletion
            mm.add_field(model, field)

        for t in source.tmpls:
            template = mm.new_template(t.name)
            template["qfmt"] = t.qfmt
            template["afmt"] = t.afmt
            mm.add_template(model, template)

        mm.add(model)

    def update(self, source: ModelSource) -> None:
        """更新已有 Notetype，维护其类型、样式、字段与模板定义。"""
        existing_dict = self._require_model(source.name)
        existing = self._to_source(existing_dict)
        self._verify_structure_compatible(existing, source)

        mm = self.col.models
        existing_dict["type"] = source.type
        existing_dict["sortf"] = source.sortf
        existing_dict["css"] = source.css

        # 逐个更新已有字段（仅更新可由外部自定义的属性，保留内部 ord）。
        for field, field_source in zip(
            existing_dict["flds"], source.flds, strict=False
        ):
            field["name"] = field_source.name
            field["collapsed"] = field_source.collapsed
            field["prevent_deletion"] = field_source.prevent_deletion

        # 追加新增字段。
        for idx in range(len(existing_dict["flds"]), len(source.flds)):
            field_source = source.flds[idx]
            field = mm.new_field(field_source.name)
            field["collapsed"] = field_source.collapsed
            field["prevent_deletion"] = field_source.prevent_deletion
            mm.add_field(existing_dict, field)

        # 逐个更新已有模板。
        for template, template_source in zip(
            existing_dict["tmpls"], source.tmpls, strict=False
        ):
            template["name"] = template_source.name
            template["qfmt"] = template_source.qfmt
            template["afmt"] = template_source.afmt

        # 追加新增模板。
        for idx in range(len(existing_dict["tmpls"]), len(source.tmpls)):
            template_source = source.tmpls[idx]
            template = mm.new_template(template_source.name)
            template["qfmt"] = template_source.qfmt
            template["afmt"] = template_source.afmt
            mm.add_template(existing_dict, template)

        mm.update_dict(existing_dict)

    def delete(self, name: str) -> None:
        """删除指定 Notetype；执行前检查依赖，避免删除仍有关联 Note 的 Notetype。"""
        model = self.col.models.by_name(name)
        if model is None:
            raise ValueError(f"Notetype not found: {name}")

        use_count = self.col.models.use_count(model)
        if use_count > 0:
            raise RuntimeError(
                f"Cannot delete notetype '{name}': it is still used by "
                f"{use_count} note(s). Detach or delete those notes first."
            )

        self.col.models.remove(model["id"])

    # -- 内部辅助 --

    def _require_model(self, name: str) -> NotetypeDict:
        model = self.col.models.by_name(name)
        if model is None:
            raise ValueError(f"Notetype not found: {name}")
        return model

    @staticmethod
    def _to_source(model: NotetypeDict) -> ModelSource:
        """将 Anki NotetypeDict 转换为 ModelSource（丢弃 Anki 内部管理属性）。"""
        return ModelSource(
            name=model["name"],
            type=model["type"],
            sortf=model["sortf"],
            css=model["css"],
            flds=[
                FieldSource(
                    name=f["name"],
                    collapsed=f.get("collapsed", True),
                    prevent_deletion=f.get("prevent_deletion", True),
                )
                for f in model["flds"]
            ],
            tmpls=[
                TemplateSource(name=t["name"], qfmt=t["qfmt"], afmt=t["afmt"])
                for t in model["tmpls"]
            ],
        )

    @staticmethod
    def _verify_structure_compatible(
        existing: ModelSource, update: ModelSource
    ) -> None:
        """验证现有定义与目标定义是否兼容，不兼容时抛出带原因的异常。

        为安全起见，仅允许"在原有顺序之后追加"新的字段/模板；任何重排、删除
        或插入到中间都会破坏对应 Note 的数据对应关系，因此视为不兼容。
        """
        existing_field_names = [f.name for f in existing.flds]
        update_field_names = [f.name for f in update.flds]
        if update_field_names[: len(existing_field_names)] != existing_field_names:
            raise ValueError(
                f"Notetype '{update.name}': incompatible field layout. "
                f"Existing fields {existing_field_names} must be a leading prefix of "
                f"target fields {update_field_names}. Reordering, removing or inserting "
                "fields in the middle is not supported."
            )

        existing_template_names = [t.name for t in existing.tmpls]
        update_template_names = [t.name for t in update.tmpls]
        if (
            update_template_names[: len(existing_template_names)]
            != existing_template_names
        ):
            raise ValueError(
                f"Notetype '{update.name}': incompatible template layout. "
                f"Existing templates {existing_template_names} must be a leading prefix "
                f"of target templates {update_template_names}. Reordering or removing "
                "templates is not supported."
            )


# ---------------------------------------------------------------------------
# AnkiNoteRepository
# ---------------------------------------------------------------------------


class AnkiNoteRepository:
    """负责 Anki Note 的读取与 CRUD，以及 Card 的挂起状态同步。"""

    def __init__(
        self,
        col: Collection,
        model_repository: AnkiModelRepository,
    ) -> None:
        self.col = col
        self.model_repository = model_repository
        self.tf_id_field = TF_ID_FIELD
        # 可重用索引，仅在 get_all 或写入后重建，保证一致性。
        self.tf_id_to_note_id: dict[str, NoteId] = {}

    def get_all(self) -> list[NoteSource]:
        """读取插件管理范围内的 Note 并转换为 NoteSource。"""
        self.tf_id_to_note_id = {}
        managed_notes: list[NoteSource] = []

        for nid in self.col.find_notes(f'"{self.tf_id_field}:*"'):
            note = self.col.get_note(nid)
            tf_id = note[self.tf_id_field]
            if not tf_id:
                continue
            self.tf_id_to_note_id[tf_id] = nid
            managed_notes.append(self._to_source(note))

        return managed_notes

    def get_by_tf_id(self, tf_id: str) -> NoteSource | None:
        """根据 `tfnoteid` 查询 Note，不存在时返回 None。"""
        note_id = self._find_note_id(tf_id)
        if note_id is None:
            return None
        return self._to_source(self.col.get_note(note_id))

    def create(self, source: NoteSource) -> None:
        """创建 Note，按需创建其 Card，并写入 tfnoteid。"""
        if not source.tf_id:
            raise ValueError("Cannot create a note: tf_id is empty")

        if self._find_note_id(source.tf_id) is not None:
            raise ValueError(f"Duplicate tf_id when creating note: {source.tf_id}")

        model = self.model_repository.get_by_name(source.model_name)
        if model is None:
            raise ValueError(f"Notetype not found: {source.model_name}")

        notetype = self.col.models.by_name(source.model_name)
        assert notetype is not None

        note = self.col.new_note(notetype)
        for field_name in self._field_names(notetype):
            note[field_name] = source.fields.get(field_name, "")

        note[self.tf_id_field] = source.tf_id
        note.tags = list(source.tags)

        deck_id = self._deck_id(source.deck)
        self.col.add_note(note, deck_id)

        self.tf_id_to_note_id[source.tf_id] = note.id

    def update(self, source: NoteSource) -> None:
        """按 `tf_id` 查找已有 Note，更新字段、标签及目标牌组。"""
        note_id = self._find_note_id(source.tf_id)
        if note_id is None:
            raise ValueError(f"Cannot update note: tf_id not found: {source.tf_id}")

        note = self.col.get_note(note_id)

        current_model = note.note_type()
        if current_model is None:
            raise RuntimeError(
                f"Note with tf_id '{source.tf_id}' has a missing notetype and "
                "cannot be updated."
            )
        if current_model["name"] != source.model_name:
            raise RuntimeError(
                f"Note with tf_id '{source.tf_id}' uses notetype "
                f"'{current_model['name']}', expected '{source.model_name}'. "
                "Reassigning notes across notetypes is not supported."
            )

        for field_name in self._field_names(current_model):
            note[field_name] = source.fields.get(field_name, "")

        self._sync_tags(note, source.tags)
        self._sync_deck(note_id, source.deck)

        self.col.update_note(note)

    def delete(self, tf_id: str) -> None:
        """删除指定 Note 及其关联 Cards。"""
        note_id = self._find_note_id(tf_id)
        if note_id is None:
            raise ValueError(f"Cannot delete note: tf_id not found: {tf_id}")

        self.col.remove_notes([note_id])
        self.tf_id_to_note_id.pop(tf_id, None)

    def sync_status(self, source: NoteSource) -> None:
        """根据 `status` 挂起或恢复该 Note 的所有关联 Cards。

        Note 不存在时安全返回，不会操作其他 Note。
        """
        note_id = self._find_note_id(source.tf_id)
        if note_id is None:
            return

        card_ids = [card.id for card in self.col.get_note(note_id).cards()]
        if not card_ids:
            return

        if source.status == "suspend":
            self.col.sched.suspend_cards(card_ids)
        else:
            self.col.sched.unsuspend_cards(card_ids)

    # -- 内部辅助 --

    def _field_names(self, notetype: NotetypeDict) -> list[str]:
        """返回目标 Notetype 的全部字段名。"""
        return [f["name"] for f in notetype["flds"]]

    def _find_note_id(self, tf_id: str) -> NoteId | None:
        """查询真实的 Anki Note ID；无匹配返回 None，多个匹配视为数据冲突。"""
        if tf_id in self.tf_id_to_note_id:
            return self.tf_id_to_note_id[tf_id]

        escaped = _escape_search_value(tf_id)
        matches = self.col.find_notes(f'"{self.tf_id_field}":"{escaped}"')
        if not matches:
            return None
        if len(matches) > 1:
            raise RuntimeError(
                f"Multiple Anki notes share tf_id '{tf_id}'. "
                "Possibly corrupted; please reconcile duplicates first."
            )
        return matches[0]

    def _to_source(self, note: Note) -> NoteSource:
        """将 Anki Note 转换为 NoteSource。"""
        model = note.note_type()
        cards = note.cards()
        deck_name = self.col.decks.name(cards[0].did) if cards else ""
        model_name = model["name"] if model is not None else ""

        status = (
            "suspend"
            if any(card.queue == QUEUE_TYPE_SUSPENDED for card in cards)
            else "unsuspend"
        )

        return NoteSource(
            tf_id=note[self.tf_id_field],
            fields=dict(note.items()),
            model_name=model_name,
            deck=deck_name,
            tags=sorted(note.tags),
            status=status,
        )

    def _sync_deck(self, note_id: NoteId, deck_name: str) -> None:
        """将该 Note 的所有 Cards 移动到目标牌组。"""
        deck_id = self._deck_id(deck_name)
        for card in self.col.get_note(note_id).cards():
            card.did = deck_id
            self.col.update_card(card)

    @staticmethod
    def _sync_tags(note: Note, tags: list[str]) -> None:
        """将 Note 的标签同步为目标状态。"""
        note.tags = list(tags)

    def _deck_id(self, deck_name: str) -> DeckId:
        """返回牌组 ID；不存在则创建。"""
        deck = self.col.decks.by_name(deck_name)
        if deck is not None:
            return DeckId(deck["id"])
        did = self.col.decks.id(deck_name)
        assert did is not None
        return did


# ---------------------------------------------------------------------------
# AnkiSyncer
# ---------------------------------------------------------------------------


class AnkiSyncer:
    """协调仓库与同步过程，不直接操作 Anki 内部字典或数据库。"""

    def __init__(
        self,
        model_repository: AnkiModelRepository,
        note_repository: AnkiNoteRepository,
    ) -> None:
        self.model_repository = model_repository
        self.note_repository = note_repository
        self.report: dict[str, dict[str, int]] = {}

    def sync_models(self, sources: list[ModelSource]) -> None:
        """按 name 同步 Notetype；缺失项默认不删除。"""
        existing = {m.name: m for m in self.model_repository.get_all()}
        incoming = {m.name: m for m in sources}

        report = {"total": len(incoming), "added": 0, "updated": 0, "unchanged": 0}

        for name in incoming.keys() - existing.keys():
            self.model_repository.create(incoming[name])
            report["added"] += 1

        for name in incoming.keys() & existing.keys():
            if existing[name] != incoming[name]:
                self.model_repository.update(incoming[name])
                report["updated"] += 1
            else:
                report["unchanged"] += 1

        # 缺失项不做删除：Notetype 删除必须由显式删除策略控制。
        self.report["models"] = report

    def sync_notes(self, sources: list[NoteSource]) -> None:
        """按 tf_id 同步 Note；缺失项默认不删除。"""
        existing = {n.tf_id: n for n in self.note_repository.get_all()}
        incoming = {n.tf_id: n for n in sources}

        report = {"total": len(incoming), "added": 0, "updated": 0, "unchanged": 0}

        for tf_id in incoming.keys() - existing.keys():
            source = incoming[tf_id]
            self.note_repository.create(source)
            self.note_repository.sync_status(source)
            report["added"] += 1

        for tf_id in incoming.keys() & existing.keys():
            source = incoming[tf_id]
            if not self._notes_equal(existing[tf_id], source):
                self.note_repository.update(source)
                report["updated"] += 1
            else:
                report["unchanged"] += 1
            # 无论内容是否变化，都检查并纠正挂起状态。
            self.note_repository.sync_status(source)

        # 缺失项不做删除：删除策略必须与完整同步范围一起显式启用。
        self.report["notes"] = report

    @staticmethod
    def extract_sources(
        data: WikiExportData,
    ) -> tuple[list[ModelSource], list[NoteSource]]:
        """从 WikiExportData 提取 ModelSource / NoteSource 列表。

        这是把 wiki 派生信息（WikiSource 的 name / url）折入 note 字段的唯一接缝；
        目前仅取出 model_info / note_info，未来若需注入 WikiName / WikiUrl 字段
        只在此处完成（见 docs/import_flow.md 第 2 节）。
        """
        models = [em.model_info for em in data.model_sources]
        notes = [en.note_info for en in data.note_sources]
        return models, notes

    def sync_all(self, data: WikiExportData) -> None:
        """依次同步 Notetype 与 Note，并输出统计信息。

        在校验通过前不会产生任何写操作，避免前置校验失败导致的局部写入。
        """
        model_sources, note_sources = self.extract_sources(data)

        # 先同步 Notetype，再同步 Note。
        self.sync_models(model_sources)
        self.sync_notes(note_sources)

        showInfo(self._format_report())

    # -- 内部辅助 --

    @staticmethod
    def _notes_equal(a: NoteSource, b: NoteSource) -> bool:
        """比较两个 NoteSource 是否内容等价（标签按无序集合比较）。"""
        if a.model_name != b.model_name or a.deck != b.deck or a.status != b.status:
            return False
        if dict(a.fields) != dict(b.fields):
            return False
        return sorted(a.tags) == sorted(b.tags)

    def _format_report(self) -> str:
        """将统计信息格式化为可读文本。"""
        model_report = self.report.get("models", {})
        note_report = self.report.get("notes", {})
        return (
            "TiddlyFlashcards Sync Summary:\n"
            f"Models    - total {model_report.get('total', 0)}, "
            f"added {model_report.get('added', 0)}, "
            f"updated {model_report.get('updated', 0)}, "
            f"unchanged {model_report.get('unchanged', 0)}\n"
            f"Notes     - total {note_report.get('total', 0)}, "
            f"added {note_report.get('added', 0)}, "
            f"updated {note_report.get('updated', 0)}, "
            f"unchanged {note_report.get('unchanged', 0)}"
        )


__all__ = [
    "TF_ID_FIELD",
    "AnkiModelRepository",
    "AnkiNoteRepository",
    "AnkiSyncer",
]
