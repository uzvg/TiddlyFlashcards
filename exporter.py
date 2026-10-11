"""
Export TiddlyFlashcards JSON data from TiddlyWiki workspace(s).

Architecture:
    WikiExporter —— 针对单个 WikiSource（folder 或单文件 HTML）调用 tiddlywiki
                    CLI 渲染成结构化的 WikiExportData。folder 与 html 两种类型
                    会使用不同的命令参数。
    WikiManager  —— 遍历 PluginConfig 中所有启用的 WikiSource，逐一导出。
    ModelSource / NoteSource —— 结构化产物，分别对应 tf_notetypes / tf_notes 中的一项。
"""

from __future__ import annotations
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn

from aqt.utils import showCritical, showWarning

from .config import WIKI_TYPE_HTML, PluginConfig, WikiSource

# tiddlywiki 中生成 flashcards JSON 数据的导出模板
RENDER_TEMPLATE = "$:/plugins/uzvg/TiddlyFlashcards/templates/tf-render-template"


# ---------------------------------------------------------------------------
# 结构化产物
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ModelSource:
    """一条 notetype 定义（对应 tf_models 中的一项）。"""

    name: str
    model_type: int = 0
    fields: list[str] = field(default_factory=list)
    templates: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, name: str, raw: dict[str, Any]) -> ModelSource:
        return cls(
            name=name,
            model_type=raw.get("model_type", 0),
            fields=list(raw.get("fields", [])),
            templates=list(raw.get("templates", [])),
        )

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> list["ModelSource"]:
        return [
            cls.from_dict(name, cfg)
            for name, cfg in raw.get("tf_models", {}).items()
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_type": self.model_type,
            "fields": self.fields,
            "templates": self.templates,
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


@dataclass(slots=True)
class WikiExportData:
    """单个 wiki 的导出产物：来源路径 + notetype 定义 + 卡片数据。"""
    name: str
    url: str
    enabled: bool
    path: str
    model_source: list[ModelSource]
    notes: list[NoteSource]


# ---------------------------------------------------------------------------
# 导出器
# ---------------------------------------------------------------------------


class WikiExporter:
    """通过 tiddlywiki CLI 渲染单个 WikiSource，产出 WikiExportData。

    针对不同 wiki 类型传入不同命令参数：
      - folder： tiddlywiki <wiki_path> --render <template> <out> text/plain
      - html：   tiddlywiki --load <wiki_path> --render <template> <out> text/plain
    """

    def __init__(
        self, tiddlywiki_path: str, template: str = RENDER_TEMPLATE
    ) -> None:
        self.tiddlywiki_path = self._resolve_bin(tiddlywiki_path)
        self.template = template

    def export(self, source: WikiSource) -> WikiExportData:
        """导出单个 wiki，渲染结果解析为 WikiExportData。"""
        wiki_path = self._validate_source(source)
        rendered = self._render_json(wiki_path, source.type)
        return WikiExportData(
            name=source.name,
            url=source.url,
            enabled=source.enabled,
            path=source.path,
            model_source=ModelSource.from_json(rendered),
            notes=NoteSource.from_json(rendered),
        )

    # -- 内部辅助 --

    @staticmethod
    def _resolve_bin(configured_bin: str) -> str:
        """空路径时自动探测；校验一次全局复用的 tiddlywiki 可执行文件。"""
        bin_path = configured_bin.strip() or shutil.which("tiddlywiki")
        if not bin_path or not shutil.which(bin_path):
            raise FileNotFoundError(f"TiddlyWiki binary not found: {configured_bin}")
        return bin_path

    @staticmethod
    def _validate_source(source: WikiSource) -> Path:
        """校验 wiki 路径，返回干净的 Path。"""
        wiki_path = Path(source.path)

        if not wiki_path.exists():
            raise FileNotFoundError(f"Wiki path not found: {wiki_path}")

        if wiki_path.is_file():
            if wiki_path.suffix.lower() not in {".html", ".htm"}:
                raise FileNotFoundError(f"Not a TiddlyWiki HTML file: {wiki_path}")
        elif not (wiki_path / "tiddlywiki.info").exists():
            raise FileNotFoundError(f"Not a valid TiddlyWiki folder: {wiki_path}")

        return wiki_path

    def _build_command(
        self, wiki_path: Path, wiki_type: str, output: str
    ) -> list[str]:
        """按 wiki 类型拼装 tiddlywiki 命令。

        html 类型没有工作目录，需用 --load 直接载入单文件 wiki 的存储；
        folder 类型直接把目录作为 wikipath 传入。
        """
        base = [self.tiddlywiki_path]
        if wiki_type == WIKI_TYPE_HTML:
            base += ["--load", str(wiki_path)]
        else:
            base += [str(wiki_path)]
        return base + ["--render", self.template, output, "text/plain"]

    def _render_json(self, wiki_path: Path, wiki_type: str) -> dict[str, Any]:
        """执行渲染命令，读取临时文件并解析为 JSON（临时文件自动清理）。"""
        fd, tmp_path = tempfile.mkstemp()
        os.close(fd)

        try:
            subprocess.run(
                self._build_command(wiki_path, wiki_type, tmp_path),
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            showCritical(f"TiddlyFlashcards export failed:\n\n{exc.stderr or exc}")
            raise RuntimeError("TiddlyWiki export failed") from exc

        try:
            with open(tmp_path, encoding="utf-8") as file:
                output = file.read()
            # 没有卡片时，模板可能产生空文件；将其视为空结果而非 JSON 错误。
            if not output.strip():
                return {}
            return json.loads(output)
        except json.JSONDecodeError:
            showCritical(
                "Invalid JSON output from TiddlyFlashcards. "
                "Please check your template output."
            )
            raise
        finally:
            Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 管理器
# ---------------------------------------------------------------------------


class WikiManager:
    """编排所有启用 wiki 的导出。"""

    def __init__(self, config: PluginConfig, exporter: WikiExporter) -> None:
        self.config = config
        self.exporter = exporter

    def get_enabled_sources(self) -> list[WikiSource]:
        return [wiki for wiki in self.config.wiki_sources if wiki.enabled]

    def export_one(self, source: WikiSource) -> WikiExportData:
        return self.exporter.export(source)

    def export_all(self) -> list[WikiExportData]:
        """导出所有启用 wiki，按配置顺序返回其 WikiExportData。

        跨 wiki 出现重复 TFNoteId 时保留首个并告警；所有 wiki 均无卡片时中止。
        """
        sources = self.get_enabled_sources()
        if not sources:
            _raise_empty_export([])

        results: list[WikiExportData] = []
        seen: set[str] = set()

        for source in sources:
            data = self.export_one(source)
            results.append(data)

            for note in data.notes:
                if note.tf_id in seen:
                    showWarning(
                        "TiddlyFlashcards found a duplicate TFNoteId and kept the "
                        f"first occurrence:\nWiki: {source.path}\nTFNoteId: {note.tf_id}"
                    )
                    continue
                seen.add(note.tf_id)

        if not any(data.notes for data in results):
            _raise_empty_export(sources)

        return results


def _raise_empty_export(sources: list[WikiSource]) -> NoReturn:
    """所有启用 wiki 都没有卡片时中止，防止 importer 误挂起现有卡片。"""
    paths = "\n".join(source.path for source in sources)
    details = f"\n\nEnabled wikis:\n{paths}" if paths else ""
    showCritical(
        "TiddlyFlashcards export produced no cards. Sync was cancelled to "
        f"protect existing cards.{details}"
    )
    raise RuntimeError("TiddlyFlashcards export produced no cards")