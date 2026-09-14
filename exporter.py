"""
Export TiddlyFlashcards JSON file from the tiddlywiki workspace
"""

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aqt.utils import showCritical, showWarning

from .config import PluginConfig, WikiSource, load_config

RENDER_TEMPLATE = "$:/uzvg/renderTemplates/flashcards-json-test"


@dataclass(frozen=True, slots=True)
class WikiExportSummary:
    """单个启用 wiki 的导出统计。"""

    path: str
    card_count: int


@dataclass(slots=True)
class ExportResult:
    """聚合后的卡片数据及各 wiki 的导出统计。"""

    data: dict[str, Any]
    wiki_summaries: list[WikiExportSummary]


def export_all(cfg: PluginConfig | None = None) -> ExportResult:
    """导出所有启用的 wiki，并按配置顺序合并其卡片数据。"""
    cfg = cfg or load_config()
    enabled_wikis = [wiki for wiki in cfg.wikis if wiki.enabled]
    if not enabled_wikis:
        return _raise_empty_export([])

    tiddlywiki_bin = _resolve_tiddlywiki_bin(cfg.tiddlywiki_bin)
    merged: dict[str, Any] = {}
    wiki_summaries: list[WikiExportSummary] = []

    for wiki in enabled_wikis:
        exported = _export_wiki(wiki, tiddlywiki_bin)
        wiki_summaries.append(WikiExportSummary(wiki.path, len(exported)))

        for tf_note_id, note in exported.items():
            if tf_note_id in merged:
                showWarning(
                    "TiddlyFlashcards found a duplicate TFNoteId and kept the "
                    f"first occurrence:\n\nWiki: {wiki.path}\nTFNoteId: {tf_note_id}"
                )
                continue
            merged[tf_note_id] = note

    if not merged:
        return _raise_empty_export(wiki_summaries)

    return ExportResult(merged, wiki_summaries)


def _raise_empty_export(wiki_summaries: list[WikiExportSummary]) -> None:
    """所有启用 wiki 都没有卡片时中止，防止 importer 误挂起现有卡片。"""
    wiki_paths = "\n".join(summary.path for summary in wiki_summaries)
    details = f"\n\nEnabled wikis:\n{wiki_paths}" if wiki_paths else ""
    showCritical(
        "TiddlyFlashcards export produced no cards. Sync was cancelled to "
        f"protect existing cards.{details}"
    )
    raise RuntimeError("TiddlyFlashcards export produced no cards")


def _resolve_tiddlywiki_bin(configured_bin: str) -> str:
    """解析并校验一次全局复用的 TiddlyWiki 可执行文件。"""
    tiddlywiki_bin = configured_bin.strip() or shutil.which("tiddlywiki")
    if not tiddlywiki_bin or not shutil.which(tiddlywiki_bin):
        raise FileNotFoundError(f"TiddlyWiki binary not found: {configured_bin}")
    return tiddlywiki_bin


def _export_wiki(wiki: WikiSource, tiddlywiki_bin: str) -> dict[str, Any]:
    """导出单个 TiddlyWiki 中的卡片为 JSON（临时文件自动清理）。"""
    wiki_path = Path(wiki.path)

    # ---- 检查 wiki_path ----
    if not wiki_path.exists():
        raise FileNotFoundError(f"Wiki path not found: {wiki_path}")

    # ---- 检查是否是合法 wiki（folder 或单文件 html）----
    if wiki_path.is_file():
        # 单文件 html wiki：必须带 html 扩展名
        if wiki_path.suffix.lower() not in {".html", ".htm"}:
            raise FileNotFoundError(f"Not a TiddlyWiki HTML file: {wiki_path}")
    elif not (wiki_path / "tiddlywiki.info").exists():
        raise FileNotFoundError(f"Not a valid TiddlyWiki folder: {wiki_path}")

    # ---- 创建临时文件（必须关闭 fd，CLI 才能覆盖写入）----
    fd, tmp_path = tempfile.mkstemp()
    os.close(fd)

    try:
        try:
            subprocess.run(
                [
                    tiddlywiki_bin,
                    str(wiki_path),
                    "--render",
                    RENDER_TEMPLATE,
                    tmp_path,
                    "text/plain",
                ],
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
            # 没有卡片时，模板可能产生空文件；将其视为零张卡片而非 JSON 错误。
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
