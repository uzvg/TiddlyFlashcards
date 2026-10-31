# AGENTS.md

TiddlyFlashcards is an Anki 2.1 add-on (minimum Anki 26.09) that exports flashcard data from configured TiddlyWiki wikis and syncs it into the Anki collection. There is no package build: Anki loads this repository directory directly.

## Setup and verification

- On this machine the add-on folder `~/.local/share/Anki2/addons21/TiddlyFlashcards` is a symlink to this repo. Edits take effect after reloading Anki; there is no install or build step.
- No test suite and no CI. Verify with `ruff check .`, `ruff format --check .`, `basedpyright`, then a manual sync via **Tools → TiddlyFlashcards Sync Cards**.
- Target Python is 3.13 (`pyproject.toml`), even though the system `python3` may be newer.
- `guard.py` is not imported by `__init__.py`; it is currently unwired and has lint/typecheck errors. Do not treat those as regressions unless changing that file.
- Modules cannot be exercised standalone: they import `aqt`/`anki` and open Qt dialogs. Test runtime behavior from inside Anki.
- The "Reload Config Dialg(For Debugging)" menu action only reloads `config.py`; use Anki's add-on reload/restart for changes to other modules.

## Architecture

- `__init__.py` — add-on entrypoint. Registers the config dialog and Tools menu actions. Its imports run at Anki startup, so an import error prevents the add-on from loading.
- `config.py` — config dataclasses/defaults plus the Qt config dialog. Settings persist through `mw.addonManager` into the add-on's `meta.json` (gitignored).
- `exporter.py` — invokes the TiddlyWiki CLI for every enabled `WikiSource` and parses the rendered JSON into `WikiExportData`. Folder and single-file HTML wikis use different command forms.
- `models.py` — dataclasses for notetypes/notes and CSS composition. Anki-internal properties (`id`, `ord`, `mod`, etc.) are intentionally excluded.
- `importer.py` — `AnkiModelRepository` / `AnkiNoteRepository` CRUD plus `AnkiSyncer` diff orchestration. Sync policy lives here.
- `docs/` — reference notes on Anki internal `FieldDict` / `NotetypeDict` / `TemplateDict` / `Note` schemas, not user documentation.

## Sync rules that are easy to break

- Notetypes are matched by `name`; notes are matched by `tf_id`, stored in the Anki field `TFNoteId`. Both keys must be unique, including across wikis.
- Sync is create/update only. Missing notetypes and notes are never deleted; do not add deletion without an explicit opt-in policy.
- Notetype field/template layouts are append-only. The existing name sequence must be an exact prefix of the target sequence; reordering, deleting, or inserting in the middle raises. Notes cannot switch notetypes.
- Missing decks are created automatically. `status == "suspend"` suspends all of a note's cards; any other value unsuspends them.
- Sync aborts before any writes when there are no enabled wikis, the export yields zero notes, notetype definitions conflict across wikis, or a `TFNoteId` is duplicated.
- Export needs a working `tiddlywiki` binary (configured path or `PATH`) and the render template tiddler `$:/plugins/uzvg/TiddlyFlashcards/templates/tf-render-template` in each wiki. That template ships with the TiddlyWiki plugin, not this repo. CLI forms are documented in `docs/tiddlywiki_render.md`.

## Data files and codegen

- `tf_notetypes.toml` is the declarative notetype source. Regenerate `tf_notetypes.json` with `python tools/render_notetypes.py` (optionally `--url ...`) instead of hand-editing the JSON.
- Comments in `tf_notetypes.toml` reference `parse_notetypes()` / `render_specs()` / `REBUILD_PENDING`; those symbols do not exist in this repo.

## Git workflow for substantial changes

- Treat a task as substantial when it changes behavior or touches multiple files: new features, refactors, schema/data-format changes, tooling/config changes, and non-trivial bug fixes. Create a dedicated branch as soon as the scope is clear, before the first commit:
  `git switch -c <type>/<short-kebab-slug>` using `feat`, `fix`, `refactor`, `docs`, or `chore`.
- Commit the verified change automatically on that branch; do not wait for confirmation. Use an English imperative subject matching existing history (`add ...`, `fix ...`, `refactor ...`) and explain the reason in the body if it is not obvious.
- Stage only files that belong to the task. Never include unrelated worktree changes, secrets, or generated caches (`__pycache__`, `.ruff_cache`, `.mypy_cache`, `meta.json`).
- Keep one branch per logical task. If the user is already on a task branch with related uncommitted work, commit there instead of nesting another branch.
- Do not branch or commit for trivial edits (typos, comments, formatting, read-only investigation) unless asked.
- Never push, force-push, rewrite history, or open a PR unless explicitly asked.
- If lint, typecheck, or the manual Anki check fails, do not commit; report the failure instead.
- In the final response, state the branch name and commit hash for any automatic commit.

## Conventions

- Comments and docstrings are Chinese; identifiers and UI strings are English. Preserve this split.
- ruff targets Python 3.13 with line length 88, double quotes, and isort/pyupgrade/bugbear/simplify rules. E501 and full-width punctuation checks are intentionally disabled.
