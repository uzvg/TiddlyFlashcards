"""
Export TiddlyFlashcards JSON data from TiddlyWiki workspace(s).

Architecture:
    WikiExporter —— 针对单个 WikiSource（folder 或单文件 HTML）调用 tiddlywiki
                    CLI 渲染成结构化的 WikiExportData。folder 与 html 两种类型
                    会使用不同的命令参数。
    WikiManager  —— 编排导出、校验与合并，经 build_export() 产出导入所需数据。
    ModelSource / NoteSource —— 结构化产物，分别对应 tf_notetypes / tf_notes 中的一项。
"""

import json
import os
import shutil
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import WIKI_TYPE_HTML, PluginConfig, WikiSource
from .exceptions import (
    ConflictingModelError,
    DuplicateTfIdError,
    EmptyModelExportError,
    EmptyNoteExportError,
    NoEnabledWikiError,
    TiddlyWikiBinaryError,
    UnknownModelError,
    WikiExportError,
)
from .models import ModelSource, NoteSource

# tiddlywiki 中生成 flashcards JSON 数据的导出模板
RENDER_TEMPLATE = "$:/plugins/uzvg/TiddlyFlashcards/templates/tf-render-template"

# tiddlywiki 导出子进程的超时时间（秒），防止进程卡死挂起 Anki 界面。
EXPORT_TIMEOUT = 60


# ---------------------------------------------------------------------------
# 结构化产物
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ExportModel:
    wiki_info: WikiSource
    model_info: ModelSource


@dataclass(slots=True)
class ExportNote:
    wiki_info: WikiSource
    note_info: NoteSource


@dataclass(slots=True)
class WikiExportData:
    """单个 wiki 的导出产物：notetype 定义 + 卡片数据。

    每条 model_sources / note_sources 均通过 wiki_info（WikiSource）携带
    该 wiki 的 name、url、path，以及各自的 model_info / note_info。
    """

    model_sources: list[ExportModel]
    note_sources: list[ExportNote]


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
        self,
        tiddlywiki_path: str,
        template: str = RENDER_TEMPLATE,
        css: str = "",
    ) -> None:
        self.tiddlywiki_path = self._resolve_bin(tiddlywiki_path)
        self.template = template
        self.css = css

    def export(self, source: WikiSource) -> WikiExportData:
        """导出单个 wiki，渲染结果解析为 WikiExportData。"""
        rendered = self._render_json(source)
        models = ModelSource.from_json(rendered, self.css)
        notes = NoteSource.from_json(rendered)
        return WikiExportData(
            model_sources=[
                ExportModel(wiki_info=source, model_info=model) for model in models
            ],
            note_sources=[
                ExportNote(wiki_info=source, note_info=note) for note in notes
            ],
        )

    # -- 内部辅助 --

    @staticmethod
    def _resolve_bin(configured_bin: str) -> str:
        """空路径时自动探测，返回可执行文件的绝对路径。

        通过一次 shutil.which 校验并解析：既能处理裸命令名（按 PATH 查找），
        也能校验绝对/相对路径的存在与可执行性；失败时给出实际尝试的路径。
        """
        bin_path = configured_bin.strip() or "tiddlywiki"
        resolved = shutil.which(bin_path)
        if not resolved:
            raise TiddlyWikiBinaryError(bin_path)
        return resolved

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

    def _build_command(self, wiki_path: Path, wiki_type: str, output: str) -> list[str]:
        """按 wiki 类型拼装 tiddlywiki 命令。

        html 类型没有工作目录，需用 --load 直接载入单文件 wiki 的存储；
        folder 类型直接把目录作为 wikipath 传入。
        """
        base = [self.tiddlywiki_path]
        if wiki_type == WIKI_TYPE_HTML:
            base += ["--load", str(wiki_path)]
        else:
            base += [str(wiki_path)]
        return [*base, "--render", self.template, output, "text/plain"]

    def _render_json(self, source: WikiSource) -> dict[str, Any]:
        """调用 tiddlywiki 渲染 schema，将结果解析为 JSON（临时文件自动清理）。"""
        try:
            wiki_path = self._validate_source(source)
        except FileNotFoundError as exc:
            raise WikiExportError((source.name, source.path), str(exc)) from exc

        fd, tmp_path = tempfile.mkstemp()
        os.close(fd)

        try:
            subprocess.run(
                self._build_command(wiki_path, source.type, tmp_path),
                check=True,
                capture_output=True,
                text=True,
                timeout=EXPORT_TIMEOUT,
            )
            with open(tmp_path, encoding="utf-8") as file:
                output = file.read()

            return json.loads(output)
        except subprocess.CalledProcessError as exc:
            raise WikiExportError(
                (source.name, source.path),
                f"TiddlyWiki exited with an error:\n\n{exc.stderr or exc}",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise WikiExportError(
                (source.name, source.path),
                "TiddlyWiki timed out. Please check whether it is hung "
                "or the wiki is too large.",
            ) from exc
        except json.JSONDecodeError as exc:
            raise WikiExportError(
                (source.name, source.path),
                "Invalid JSON output. Please check your render template.",
            ) from exc
        except OSError as exc:
            # 二进制缺失、无执行权限等。
            raise WikiExportError(
                (source.name, source.path),
                f"Could not run the TiddlyWiki binary:\n\n{exc}",
            ) from exc
        finally:
            Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 管理器
# ---------------------------------------------------------------------------


class WikiManager:
    """编排所有启用 wiki 的导出、校验与合并。"""

    def __init__(self, config: PluginConfig, exporter: WikiExporter) -> None:
        self.config = config
        self.exporter = exporter

    def get_enabled_sources(self) -> list[WikiSource]:
        return [wiki for wiki in self.config.wiki_sources if wiki.enabled]

    def build_export(self) -> WikiExportData:
        """导出、校验并合并所有启用 wiki，返回导入所需数据。

        任一校验失败都会抛出 TiddlyFlashcardsError，不会执行合并。
        """
        sources = self.get_enabled_sources()
        if not sources:
            raise NoEnabledWikiError()

        exported_wikis = [self.exporter.export(source) for source in sources]

        self._validate_results(exported_wikis)

        return self._merge_results(exported_wikis)

    def _validate_results(self, exported_wikis: list[WikiExportData]) -> None:
        """依序校验所有 wiki 的导出产物，任一问题即抛出领域异常。

        顺序见 docs/sync_validation.md：空 model → model 冲突 → 空 note
        → 未知 model → 重复 tf_id。
        """
        enabled_wikis = [(wiki.name, wiki.path) for wiki in self.get_enabled_sources()]

        all_models = [
            model for wiki_data in exported_wikis for model in wiki_data.model_sources
        ]
        if not all_models:
            raise EmptyModelExportError(enabled_wikis)

        models_by_name: dict[str, list[ExportModel]] = defaultdict(list)
        for model in all_models:
            models_by_name[model.model_info.name].append(model)
        conflicts = {
            name: [
                (model.wiki_info.name, model.wiki_info.path) for model in definitions
            ]
            for name, definitions in models_by_name.items()
            if len(definitions) > 1
            and any(
                definition.model_info != definitions[0].model_info
                for definition in definitions[1:]
            )
        }
        if conflicts:
            raise ConflictingModelError(conflicts)

        all_notes = [
            note for wiki_data in exported_wikis for note in wiki_data.note_sources
        ]
        if not all_notes:
            raise EmptyNoteExportError(enabled_wikis)

        model_names = {model.model_info.name for model in all_models}
        unknown: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        for note in all_notes:
            if note.note_info.model_name not in model_names:
                unknown[note.note_info.model_name].append(
                    (note.wiki_info.name, note.wiki_info.path, note.note_info.tf_id)
                )
        if unknown:
            raise UnknownModelError(dict(unknown))

        notes_by_id: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for note in all_notes:
            notes_by_id[note.note_info.tf_id].append(
                (note.wiki_info.name, note.wiki_info.path)
            )
        duplicates = {
            tf_id: refs for tf_id, refs in notes_by_id.items() if len(refs) > 1
        }
        if duplicates:
            raise DuplicateTfIdError(duplicates)

    @staticmethod
    def _merge_results(exported_wikis: list[WikiExportData]) -> WikiExportData:
        """合并所有 wiki 的导出产物；同名 model 只保留第一个。"""
        models: dict[str, ExportModel] = {}
        notes: list[ExportNote] = []
        for wiki_data in exported_wikis:
            for export_model in wiki_data.model_sources:
                models.setdefault(export_model.model_info.name, export_model)
            notes.extend(wiki_data.note_sources)
        return WikiExportData(
            model_sources=list(models.values()),
            note_sources=notes,
        )
