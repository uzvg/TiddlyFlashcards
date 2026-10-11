"""Anki 相关的结构化数据模型。

这些 dataclass 分别对应 exporter 输出的结构以及 Anki Notetype 内部
schema（FieldDict / TemplateDict）的子集，供同步器等模块复用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class FieldSource:
    """一个字段定义（对应 Anki FieldDict / NoteFieldSchema11 的子集）。

    仅保留可由导出/同步逻辑自定义的字段配置；`ord`、`id`、`tag` 等
    Anki 内部管理字段由 Anki 自动分配，不应在此自定义。
    """

    name: str
    # sticky: bool = False
    # rtl: bool = False
    # font: str = "Arial"
    # size: int = 20
    # description: str = ""
    # plain_text: bool = False
    collapsed: bool = False
    # exclude_from_search: bool = False
    # prevent_deletion: bool = False


@dataclass(slots=True)
class TemplateSource:
    """一个卡片模板定义（对应 Anki TemplateDict / CardTemplateSchema11 的子集）。

    仅保留可由导出/同步逻辑自定义的模板内容；`ord`、`did`、`id` 等
    Anki 内部管理字段由 Anki 自动分配，不应在此自定义。
    """

    name: str
    qfmt: str = ""
    afmt: str = ""
    # bqfmt: str = ""
    # bafmt: str = ""
    # bfont: str = ""
    # bsize: int = 0


@dataclass(slots=True)
class ModelSource:
    """一条 notetype 定义（对应 Anki NotetypeDict 中可由外部自定义的子集）。

    `id`、`mod`、`usn`、`req`、`other` 等属于 Anki 内部管理字段，
    不在此定义。
    """

    name: str
    type: int = 0
    sortf: int = 0
    css: str = ""
    # latex_pre: str = ""
    # latex_post: str = ""
    # latex_svg: bool = False
    flds: list[FieldSource] = field(default_factory=list)
    tmpls: list[TemplateSource] = field(default_factory=list)

    @classmethod
    def from_dict(cls, name: str, raw: dict[str, Any]) -> "ModelSource":
        return cls(
            name=name,
            type=raw.get("type", 0),
            sortf=raw.get("sortf", 0),
            css=raw.get("css", ""),
            flds=[cls._parse_field(f) for f in raw.get("flds", [])],
            tmpls=[
                TemplateSource(
                    name=t.get("name", ""), qfmt=t.get("front", ""), afmt=t.get("back", "")
                )
                for t in raw.get("tmpls", [])
            ],
        )

    @staticmethod
    def _parse_field(raw: Any) -> FieldSource:
        """将字段名或字段字典解析为 FieldSource。"""
        if isinstance(raw, dict):
            return FieldSource(name=raw.get("name", ""))
        return FieldSource(name=raw)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> list["ModelSource"]:
        return [
            cls.from_dict(name, cfg)
            for name, cfg in raw.get("tf_models", {}).items()
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "sortf": self.sortf,
            "css": self.css,
            "flds": [f.name for f in self.flds],
            "tmpls": [
                {"name": t.name, "qfmt": t.qfmt, "afmt": t.afmt}
                for t in self.tmpls
            ],
        }


@dataclass(slots=True)
class NoteSource:
    """一张卡片（对应 tf_notes 中的一项），tf_id 为存储时的唯一键。"""

    tf_id: str
    fields: dict[str, str]
    model_name: str
    model_version: str = ""
    deck: str = "TFDefault"
    tags: list[str] = field(default_factory=list)
    status: str = "active"

    @classmethod
    def from_dict(cls, tf_id: str, raw: dict[str, Any]) -> "NoteSource":
        return cls(
            tf_id=tf_id,
            fields=raw.get("fields", {}),
            model_name=raw.get("modelName", ""),
            model_version=raw.get("modelVersion", ""),
            deck=raw.get("deck", "TFDefault"),
            tags=list(raw.get("tags", [])),
            status=raw.get("status", "active"),
        )

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> list["NoteSource"]:
        return [
            cls.from_dict(tf_id, cfg)
            for tf_id, cfg in raw.get("tf_notes", {}).items()
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fields": self.fields,
            "modelName": self.model_name,
            "modelVersion": self.model_version,
            "deck": self.deck,
            "tags": self.tags,
            "status": self.status,
        }