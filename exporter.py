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
from dataclasses import dataclass
from collections import defaultdict
from pathlib import Path
from typing import Any, NoReturn

from aqt.utils import showCritical

from .config import WIKI_TYPE_HTML, PluginConfig, WikiSource
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
            raise FileNotFoundError(f"TiddlyWiki binary not found: {bin_path}")
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

    def _render_json(self, source: WikiSource) -> dict[str, Any]:
        """调用 tiddlywiki 渲染 schema，将结果解析为 JSON（临时文件自动清理）。"""
        wiki_path = self._validate_source(source)
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
            showCritical(f"TiddlyFlashcards export failed:\n\n{exc.stderr or exc}")
            raise RuntimeError("TiddlyWiki export failed") from exc
        except subprocess.TimeoutExpired as exc:
            showCritical(
                f"TiddlyFlashcards export timed out for wiki '{source.name}'.\n\n"
                "Please check whether TiddlyWiki is hung or the wiki is too large."
            )
            raise RuntimeError("TiddlyWiki export timed out") from exc
        except OSError as exc:
            # 二进制缺失、无执行权限等；避免裸 traceback 冒泡到同步流程。
            showCritical(
                "TiddlyFlashcards could not run the TiddlyWiki binary.\n\n"
                f"Wiki: {source.name}\nPath: {source.path}\n\n{exc}"
            )
            raise RuntimeError("Failed to run TiddlyWiki") from exc
        except json.JSONDecodeError:
            showCritical(
                "Invalid JSON output from TiddlyFlashcards. "
                "Please check your template output.\n\n"
                f"Wiki: {source.name}\nPath: {source.path}"
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

        导出完成后统一校验（见 _validate_results），任一校验问题都会
        阻止本次导出被继续采用：
          - notetype 定义冲突 / 全空导出 / 跨 wiki 重复 TFNoteId——中止。
        """
        sources = self.get_enabled_sources()
        if not sources:
            self._raise_no_enabled_sources()

        results = [self.export_one(source) for source in sources]

        self._validate_results(sources, results)

        return results

    @staticmethod
    def _raise_no_enabled_sources() -> NoReturn:
        """没有启用任何 WikiSource 时中止（与"无卡片"属于不同情形）。"""
        showCritical(
            "TiddlyFlashcards has no enabled wiki sources to export. "
            "Please enable at least one wiki in the plugin config."
        )
        raise RuntimeError("No enabled wiki sources")

    @staticmethod
    def _raise_empty_export(sources: list[WikiSource]) -> NoReturn:
        """所有 wiki 均无卡片时中止，防止 importer 误挂起现有卡片。"""
        paths = "\n".join(source.path for source in sources)
        details = f"\n\nEnabled wikis:\n{paths}" if paths else ""
        showCritical(
            "TiddlyFlashcards export produced no cards. Sync was cancelled to "
            f"protect existing cards.{details}"
        )
        raise RuntimeError("TiddlyFlashcards export produced no cards")

    @staticmethod
    def _validate_results(
        sources: list[WikiSource],
        exported_wikis: list[WikiExportData],
    ) -> None:
        """依序校验所有 wiki 的导出产物，均因安全问题中止。

        校验顺序：
          1. notetype 定义冲突——列出详情并中止；
          2. 无卡片（全 wiki note_sources 为空）——中止；
          3. 跨 wiki 重复 TFNoteId——列出详情并中止。
        """
        # 1. notetype 定义冲突：列出详情并中止。
        if conflicts := WikiManager._find_conflicting_models(exported_wikis):
            details = "\n".join(
                f"- {name}: "
                + ", ".join(
                    f"{model.wiki_info.name} ({model.wiki_info.path})"
                    for model in definitions
                )
                for name, definitions in conflicts.items()
            )
            showCritical(
                "TiddlyFlashcards found conflicting notetype definitions across "
                "wikis. Sync was cancelled to protect existing cards.\n\n"
                f"{details}"
            )
            raise RuntimeError("Conflicting notetype definitions across wikis")

        # 2. 无卡片：中止，防止误挂起现有卡片。
        if not any(wiki_data.note_sources for wiki_data in exported_wikis):
            WikiManager._raise_empty_export(sources)

        # 3. 重复 TFNoteId：列出详情并中止。
        if duplicates := WikiManager._find_duplicate_note_ids(exported_wikis):
            details = "\n".join(
                f"- {tf_id}:\n"
                + "\n".join(
                    f"    {note.wiki_info.name} ({note.wiki_info.path})"
                    for note in occurrences
                )
                for tf_id, occurrences in duplicates.items()
            )
            showCritical(
                "TiddlyFlashcards found duplicate TFNoteId across wikis. "
                "Sync was cancelled to protect existing cards.\n\n"
                f"{details}"
            )
            raise RuntimeError("Duplicate TFNoteId across wikis")

    @staticmethod
    def _find_conflicting_models(
        exported_wikis: list[WikiExportData],
    ) -> dict[str, list[ExportModel]] | None:
        """检测跨 wiki 名字相同但定义不一致的 notetype 冲突。

        展平所有 wiki 的 models 后按名字分组；仅当同一名字存在多份且
        定义互不相同的 ExportModel 时视为冲突。
        有冲突时返回 {model_name: 该名字下全部冲突的 ExportModel}，无冲突返回 None。
        """
        all_models: list[ExportModel] = [
            model
            for wiki_data in exported_wikis
            for model in wiki_data.model_sources
        ]

        models_by_name: dict[str, list[ExportModel]] = defaultdict(list)
        for model in all_models:
            models_by_name[model.model_info.name].append(model)

        conflicts = {
            name: definitions
            for name, definitions in models_by_name.items()
            if len(definitions) > 1
            and any(
                definition.model_info != definitions[0].model_info
                for definition in definitions[1:]
            )
        }
        return conflicts or None

    @staticmethod
    def _find_duplicate_note_ids(
        exported_wikis: list[WikiExportData],
    ) -> dict[str, list[ExportNote]] | None:
        """检测跨 wiki 重复的 TFNoteId。

        展平所有 wiki 的 notes 后按 tf_id 分组；仅当同一 tf_id 出现多份
        ExportNote 时视为重复。
        有重复时返回 {tf_id: 该 tf_id 下全部重复的 ExportNote}，无重复返回 None。
        """
        notes_by_id: dict[str, list[ExportNote]] = defaultdict(list)
        for wiki_data in exported_wikis:
            for note in wiki_data.note_sources:
                notes_by_id[note.note_info.tf_id].append(note)

        duplicates = {
            tf_id: occurrences
            for tf_id, occurrences in notes_by_id.items()
            if len(occurrences) > 1
        }
        return duplicates or None