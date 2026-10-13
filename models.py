"""Anki 相关的结构化数据模型。

这些 dataclass 分别对应 exporter 输出的结构以及 Anki Notetype 内部
schema（FieldDict / TemplateDict）的子集，供同步器等模块复用。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any
from .config import PluginConfig
from pathlib import Path


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
    collapsed: bool = True
    # exclude_from_search: bool = False
    prevent_deletion: bool = True

    @classmethod
    def from_dict(cls, raw: Any) -> "FieldSource":
        """将字段名或字段字典解析为 FieldSource。"""
        return cls(
            name=raw.get("name", ""),
            collapsed=raw.get("collapsed", True),
            prevent_deletion=raw.get("prevent_deletion", True),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    # bfmt: str = ""
    # bsize: int = 0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TemplateSource":
        return cls(
            name=raw.get("name", ""),
            qfmt=raw.get("front", ""),
            afmt=raw.get("back", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    def from_dict(
        cls,
        name: str,
        raw: dict[str, Any],
        css: str = "",
    ) -> "ModelSource":
        return cls(
            name=name,
            type=raw.get("type", 0),
            sortf=raw.get("sortf", 0),
            css=css,
            flds=[FieldSource.from_dict(f) for f in raw.get("flds", [])],
            tmpls=[TemplateSource.from_dict(t) for t in raw.get("tmpls", [])],
        )

    @classmethod
    def from_json(
        cls,
        raw: dict[str, Any],
        css: str = "",
    ) -> list["ModelSource"]:
        return [
            cls.from_dict(name, cfg, css)
            for name, cfg in raw.get("tf_models", {}).items()
        ]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("name", None)
        return data


def compose_model_css(cfg: PluginConfig) -> str:
    """组合 notetype 的最终样式：内置 default.css + 用户自定义 custom_css。

    两段用空行分隔，避免拼接后 CSS 规则粘连；两者皆空时返回空串。
    """
    default_css_path = Path(__file__).parent.resolve() / "default.css"
    default_css = default_css_path.read_text(encoding="utf-8").strip()
    custom_css = cfg.custom_css.strip()

    if not default_css and not custom_css:
        return ""

    return f"{default_css}\n\n{custom_css}".strip()


@dataclass(slots=True)
class NoteSource:
    """一张卡片（对应 tf_notes 中的一项），tf_id 为存储时的唯一键。"""

    tf_id: str
    fields: dict[str, str]
    model_name: str
    # model_version: str = ""
    deck: str = "TFDefault"
    tags: list[str] = field(default_factory=list)
    status: str = "unsuspend"

    @classmethod
    def from_dict(
        cls,
        tf_id: str,
        raw: dict[str, Any],
    ) -> "NoteSource":
        return cls(
            tf_id=tf_id,
            fields=raw.get("fields", {}),
            model_name=raw.get("modelName", ""),
            deck=raw.get("deck", "TFDefault"),
            tags=list(raw.get("tags", [])),
            status=raw.get("status", "unsuspend"),
        )

    @classmethod
    def from_json(
        cls,
        raw: dict[str, Any],
    ) -> list["NoteSource"]:
        return [
            cls.from_dict(tf_id, cfg) for tf_id, cfg in raw.get("tf_notes", {}).items()
        ]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["modelName"] = data.pop("model_name")
        data.pop("tf_id", None)
        return data
