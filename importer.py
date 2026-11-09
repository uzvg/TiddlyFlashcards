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
- 同步是 TiddlyWiki → Anki 的单向映射：导出结果中缺失的受管对象会被删除，
  删除前须经确认回调同意；删除范围与顺序见 `docs/import_flow.md`。
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from anki import search_pb2
from anki.collection import Collection
from anki.consts import QUEUE_TYPE_SUSPENDED
from anki.decks import DeckId
from anki.models import NotetypeDict
from anki.notes import Note, NoteId

from .exporter import WikiExportData
from .models import FieldSource, ModelSource, NoteSource, TemplateSource

# NoteSource.tf_id 在 Anki Note 中存储的字段名。
TF_ID_FIELD = "TFNoteId"


def _content_fields(source: NoteSource) -> dict[str, str]:
    """返回不含身份字段 TFNoteId 的内容字段。"""
    return {k: v for k, v in source.fields.items() if k != TF_ID_FIELD}


# ---------------------------------------------------------------------------
# AnkiModelRepository
# ---------------------------------------------------------------------------


class AnkiModelRepository:
    """负责 Anki 内部 Notetype 的读取与增删改查。"""

    def __init__(self, col: Collection) -> None:
        self.col = col

    # -- 读取 --

    def get_all(self) -> list[ModelSource]:
        """读取所有受管 Notetype（含 TFNoteId 字段）并转换为 ModelSource。"""
        return [
            self._to_source(m) for m in self.col.models.all() if self._is_managed(m)
        ]

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
            field_dict = mm.new_field(f.name)
            field_dict["collapsed"] = f.collapsed
            field_dict["prevent_deletion"] = f.prevent_deletion
            mm.add_field(model, field_dict)

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
        for field_dict, field_source in zip(
            existing_dict["flds"], source.flds, strict=False
        ):
            field_dict["name"] = field_source.name
            field_dict["collapsed"] = field_source.collapsed
            field_dict["prevent_deletion"] = field_source.prevent_deletion

        # 追加新增字段。
        for idx in range(len(existing_dict["flds"]), len(source.flds)):
            field_source = source.flds[idx]
            field_dict = mm.new_field(field_source.name)
            field_dict["collapsed"] = field_source.collapsed
            field_dict["prevent_deletion"] = field_source.prevent_deletion
            mm.add_field(existing_dict, field_dict)

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
        """删除指定受管 Notetype。

        受管 note 已由同步器显式删除；若仍有非受管 note 使用该 notetype，
        会随 notetype 一并被移除（单向同步的既定语义，见 docs/import_flow.md）。
        """
        model = self.col.models.by_name(name)
        if model is None:
            raise ValueError(f"Notetype not found: {name}")

        self.col.models.remove(model["id"])

    # -- 内部辅助 --

    @staticmethod
    def _is_managed(model: NotetypeDict) -> bool:
        """判断 Notetype 是否受管（含 TFNoteId 字段）。"""
        return any(f["name"] == TF_ID_FIELD for f in model["flds"])

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

        note[self.tf_id_field] = source.tf_id

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

        search = self.col.build_search_string(
            search_pb2.SearchNode(
                field=search_pb2.SearchNode.Field(
                    field_name=self.tf_id_field,
                    text=tf_id,
                )
            )
        )
        matches = self.col.find_notes(search)
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


@dataclass(slots=True)
class SyncPlan:
    """一次同步的写入计划：create / update / delete / status 集合（只读计算产物）。"""

    models_to_create: list[ModelSource] = field(default_factory=list)
    models_to_update: list[ModelSource] = field(default_factory=list)
    models_to_delete: list[ModelSource] = field(default_factory=list)
    notes_to_create: list[NoteSource] = field(default_factory=list)
    notes_to_update: list[NoteSource] = field(default_factory=list)
    notes_to_delete: list[NoteSource] = field(default_factory=list)
    notes_to_suspend: list[NoteSource] = field(default_factory=list)
    notes_to_unsuspend: list[NoteSource] = field(default_factory=list)

    @property
    def has_deletions(self) -> bool:
        return bool(self.models_to_delete or self.notes_to_delete)


class AnkiSyncer:
    """协调仓库与同步过程，不直接操作 Anki 内部字典或数据库。

    同步是 TiddlyWiki → Anki 的单向映射：导出结果中缺失的受管对象会被删除。
    删除前须经 `confirm_deletion` 回调同意；回调未提供或返回 False 时中止同步，
    不产生任何写。
    """

    def __init__(
        self,
        model_repository: AnkiModelRepository,
        note_repository: AnkiNoteRepository,
    ) -> None:
        self.model_repository = model_repository
        self.note_repository = note_repository
        self.report: dict[str, dict[str, int]] = {}

    @staticmethod
    def extract_sources(
        data: WikiExportData,
    ) -> tuple[list[ModelSource], list[NoteSource]]:
        """从 WikiExportData 提取 ModelSource / NoteSource 列表。

        这是把 wiki 派生信息（WikiSource 的 name / url）折入 note 字段的唯一接缝；
        在此把 WikiName / WikiUrl 注入每个 note 的 fields（见 docs/import_flow.md 第 2 节）。
        """
        models = [em.model_info for em in data.model_sources]
        notes: list[NoteSource] = []
        for en in data.note_sources:
            note = en.note_info
            note.fields["WikiName"] = en.wiki_info.name
            note.fields["WikiUrl"] = en.wiki_info.url
            notes.append(note)
        return models, notes

    def sync_all(
        self,
        data: WikiExportData,
        confirm_deletion: Callable[[SyncPlan], bool] | None = None,
    ) -> str | None:
        """同步入口：提取 → 计划（只读）→ 确认 → 执行。

        返回报告文本；用户取消删除时返回 None（不产生任何写）。
        """
        model_sources, note_sources = self.extract_sources(data)
        plan = self._compute_plan(model_sources, note_sources)

        if plan.has_deletions and (
            confirm_deletion is None or not confirm_deletion(plan)
        ):
            return None

        self._execute(plan)
        self._record_report(plan, len(model_sources), len(note_sources))
        return self._format_report()

    # -- 计划（只读） --

    def _compute_plan(
        self,
        model_sources: list[ModelSource],
        note_sources: list[NoteSource],
    ) -> SyncPlan:
        """计算 create / update / delete 集合，不产生任何写。"""
        existing_models = {m.name: m for m in self.model_repository.get_all()}
        incoming_models = {m.name: m for m in model_sources}

        models_to_create = [
            incoming_models[name]
            for name in incoming_models.keys() - existing_models.keys()
        ]
        models_to_update = [
            incoming_models[name]
            for name in incoming_models.keys() & existing_models.keys()
            if existing_models[name] != incoming_models[name]
        ]
        models_to_delete = [
            existing_models[name]
            for name in existing_models.keys() - incoming_models.keys()
        ]

        existing_notes = {n.tf_id: n for n in self.note_repository.get_all()}
        incoming_notes = {n.tf_id: n for n in note_sources}

        notes_to_create = [
            incoming_notes[tf_id]
            for tf_id in incoming_notes.keys() - existing_notes.keys()
        ]
        notes_to_update = [
            incoming_notes[tf_id]
            for tf_id in incoming_notes.keys() & existing_notes.keys()
            if not self._notes_equal(existing_notes[tf_id], incoming_notes[tf_id])
        ]
        notes_to_delete = [
            existing_notes[tf_id]
            for tf_id in existing_notes.keys() - incoming_notes.keys()
        ]

        notes_to_suspend, notes_to_unsuspend = self._compute_status_changes(
            existing_notes, incoming_notes
        )

        return SyncPlan(
            models_to_create=models_to_create,
            models_to_update=models_to_update,
            models_to_delete=models_to_delete,
            notes_to_create=notes_to_create,
            notes_to_update=notes_to_update,
            notes_to_delete=notes_to_delete,
            notes_to_suspend=notes_to_suspend,
            notes_to_unsuspend=notes_to_unsuspend,
        )

    @staticmethod
    def _compute_status_changes(
        existing_notes: dict[str, NoteSource],
        incoming_notes: dict[str, NoteSource],
    ) -> tuple[list[NoteSource], list[NoteSource]]:
        """计算需要挂起 / 恢复的 Note。

        新建 note 若目标 status 为 suspend 也需要挂起；
        已有 note 仅当 status 变化时才同步。
        """
        to_suspend: list[NoteSource] = []
        to_unsuspend: list[NoteSource] = []

        for tf_id in incoming_notes.keys() - existing_notes.keys():
            source = incoming_notes[tf_id]
            if source.status == "suspend":
                to_suspend.append(source)

        for tf_id in incoming_notes.keys() & existing_notes.keys():
            incoming = incoming_notes[tf_id]
            existing = existing_notes[tf_id]
            if incoming.status == existing.status:
                continue
            if incoming.status == "suspend":
                to_suspend.append(incoming)
            else:
                to_unsuspend.append(incoming)

        return to_suspend, to_unsuspend

    # -- 执行（写） --

    def _execute(self, plan: SyncPlan) -> None:
        """按固定顺序执行写入：model → note → status → note 删除 → model 删除。

        状态同步在 create / update 之后、delete 之前执行，确保所有卡片数据已落盘。
        """
        for source in plan.models_to_create:
            self.model_repository.create(source)
        for source in plan.models_to_update:
            self.model_repository.update(source)

        for source in plan.notes_to_create:
            self.note_repository.create(source)
        for source in plan.notes_to_update:
            self.note_repository.update(source)

        for source in plan.notes_to_suspend:
            self.note_repository.sync_status(source)
        for source in plan.notes_to_unsuspend:
            self.note_repository.sync_status(source)

        for source in plan.notes_to_delete:
            self.note_repository.delete(source.tf_id)
        for source in plan.models_to_delete:
            self.model_repository.delete(source.name)

    # -- 报告 --

    def _record_report(
        self,
        plan: SyncPlan,
        model_total: int,
        note_total: int,
    ) -> None:
        """填充报告统计数据。

        "unchanged" 统计的是字段/标签/牌组/状态四项均未改变的 note；
        suspend / unsuspend 与 update 可重叠（一个 note 可能同时变更字段与状态）。
        """
        self.report["models"] = {
            "total": model_total,
            "added": len(plan.models_to_create),
            "updated": len(plan.models_to_update),
            "unchanged": model_total
            - len(plan.models_to_create)
            - len(plan.models_to_update),
            "deleted": len(plan.models_to_delete),
        }

        all_changed_ids = (
            {s.tf_id for s in plan.notes_to_create}
            | {s.tf_id for s in plan.notes_to_update}
            | {s.tf_id for s in plan.notes_to_suspend}
            | {s.tf_id for s in plan.notes_to_unsuspend}
        )
        self.report["notes"] = {
            "total": note_total,
            "added": len(plan.notes_to_create),
            "updated": len(plan.notes_to_update),
            "unchanged": note_total - len(all_changed_ids),
            "deleted": len(plan.notes_to_delete),
            "suspended": len(plan.notes_to_suspend),
            "unsuspended": len(plan.notes_to_unsuspend),
        }

    @staticmethod
    def _notes_equal(a: NoteSource, b: NoteSource) -> bool:
        """比较两个 NoteSource 是否内容等价（标签按无序集合比较）。

        status 不参与比较：挂起状态由独立的 suspend/unsuspend 动作处理，
        避免 status 变化触发 update_note 重新生成卡片而干扰挂起同步。
        """
        if a.model_name != b.model_name or a.deck != b.deck:
            return False
        if _content_fields(a) != _content_fields(b):
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
            f"unchanged {model_report.get('unchanged', 0)}, "
            f"deleted {model_report.get('deleted', 0)}\n"
            f"Notes     - total {note_report.get('total', 0)}, "
            f"added {note_report.get('added', 0)}, "
            f"updated {note_report.get('updated', 0)}, "
            f"unchanged {note_report.get('unchanged', 0)}, "
            f"deleted {note_report.get('deleted', 0)}, "
            f"suspended {note_report.get('suspended', 0)}, "
            f"unsuspended {note_report.get('unsuspended', 0)}"
        )


__all__ = [
    "TF_ID_FIELD",
    "AnkiModelRepository",
    "AnkiNoteRepository",
    "AnkiSyncer",
    "SyncPlan",
]