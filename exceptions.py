"""同步前校验的领域异常。

这些异常只描述失败并携带面向用户的 ``title`` / ``summary`` / ``details``，
不导入 ``aqt``、Qt 或 ``exporter``，也不弹窗；展示由 ``__init__.py`` 统一负责。
"""

from __future__ import annotations

# Wiki 引用用普通元组表示，避免异常模块依赖 exporter/config 的类型。
WikiRef = tuple[str, str]  # (name, path)
NoteRef = tuple[str, str, str]  # (name, path, tf_id)

DETAIL_SAMPLE_LIMIT = 10
DETAIL_GROUP_LIMIT = 50


class TiddlyFlashcardsError(Exception):
    """同步前校验失败：携带可展示的标题、摘要与折叠详情。"""

    def __init__(self, title: str, summary: str, details: str) -> None:
        super().__init__(summary)
        self.title = title
        self.summary = summary
        self.details = details


def _format_wiki_list(wikis: list[WikiRef]) -> str:
    return "\n".join(f"  {name} ({path})" for name, path in wikis)


class NoEnabledWikiError(TiddlyFlashcardsError):
    def __init__(self) -> None:
        super().__init__(
            title="No enabled wiki sources",
            summary=(
                "TiddlyFlashcards has no enabled wiki sources to export. "
                "Please enable at least one wiki in the plugin config."
            ),
            details="",
        )


class EmptyModelExportError(TiddlyFlashcardsError):
    def __init__(self, wikis: list[WikiRef]) -> None:
        super().__init__(
            title="No notetypes exported",
            summary=(
                "No notetype definitions were exported across the enabled wikis. "
                "Continuing could delete all cards managed by TiddlyFlashcards."
            ),
            details="Enabled wikis:\n" + _format_wiki_list(wikis),
        )


class ConflictingModelError(TiddlyFlashcardsError):
    def __init__(self, conflicts: dict[str, list[WikiRef]]) -> None:
        lines = []
        for name, wikis in conflicts.items():
            lines.append(f"- {name}")
            lines.extend(
                f"  - {wiki_name} ({wiki_path})" for wiki_name, wiki_path in wikis
            )
        super().__init__(
            title="Conflicting notetype definitions",
            summary=(
                "Different wikis define the same notetype differently. "
                "Sync was cancelled."
            ),
            details="Found conflicting notetype definitions across wikis.\n\n"
            + "\n".join(lines),
        )


class EmptyNoteExportError(TiddlyFlashcardsError):
    def __init__(self, wikis: list[WikiRef]) -> None:
        super().__init__(
            title="No cards exported",
            summary="No cards were exported across the enabled wikis.",
            details="Enabled wikis:\n" + _format_wiki_list(wikis),
        )


class UnknownModelError(TiddlyFlashcardsError):
    def __init__(self, unknown: dict[str, list[NoteRef]]) -> None:
        lines = []
        for model_name, refs in unknown.items():
            lines.append(f"- {model_name}: {len(refs)} note(s)")
            by_wiki: dict[WikiRef, list[str]] = {}
            for name, path, tf_id in refs:
                by_wiki.setdefault((name, path), []).append(tf_id)
            for (name, path), tf_ids in by_wiki.items():
                samples = tf_ids[:DETAIL_SAMPLE_LIMIT]
                suffix = (
                    f" (showing {len(samples)} of {len(tf_ids)})"
                    if len(tf_ids) > DETAIL_SAMPLE_LIMIT
                    else ""
                )
                lines.append(f"  - {name} ({path}): {', '.join(samples)}{suffix}")
        super().__init__(
            title="Unknown notetype",
            summary="Notes reference notetypes that were not exported.",
            details="\n".join(lines),
        )


class DuplicateTfIdError(TiddlyFlashcardsError):
    def __init__(self, duplicates: dict[str, list[WikiRef]]) -> None:
        total = len(duplicates)
        wiki_set = {wiki for refs in duplicates.values() for wiki in refs}

        groups: dict[tuple[WikiRef, ...], list[str]] = {}
        for tf_id, refs in duplicates.items():
            signature = tuple(sorted(set(refs)))
            groups.setdefault(signature, []).append(tf_id)

        lines = [f"Found {total} tf_id conflicts across {len(wiki_set)} wikis."]
        for index, (signature, tf_ids) in enumerate(groups.items()):
            if index == DETAIL_GROUP_LIMIT:
                lines.append(
                    f"... and {len(groups) - DETAIL_GROUP_LIMIT} more group(s)"
                )
                break
            wiki_names = " + ".join(f"{name} ({path})" for name, path in signature)
            samples = tf_ids[:DETAIL_SAMPLE_LIMIT]
            suffix = (
                f" (showing {len(samples)} of {len(tf_ids)})"
                if len(tf_ids) > DETAIL_SAMPLE_LIMIT
                else ""
            )
            lines.append(f"- {wiki_names}: {len(tf_ids)} conflict(s)")
            lines.append(f"  e.g. {', '.join(samples)}{suffix}")

        super().__init__(
            title="Duplicate TFNoteId",
            summary="The same TFNoteId appears in more than one wiki. Sync was cancelled.",
            details="\n".join(lines),
        )


class WikiExportError(TiddlyFlashcardsError):
    def __init__(self, wiki: WikiRef, reason: str) -> None:
        name, path = wiki
        super().__init__(
            title="TiddlyWiki export failed",
            summary=f"Failed to export wiki '{name}'. Sync was cancelled.",
            details=f"Wiki: {name} ({path})\n\n{reason}",
        )


class TiddlyWikiBinaryError(TiddlyFlashcardsError):
    def __init__(self, bin_path: str) -> None:
        super().__init__(
            title="TiddlyWiki binary not found",
            summary="The TiddlyWiki executable could not be found.",
            details=f"Tried: {bin_path}",
        )
