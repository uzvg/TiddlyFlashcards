# AGENTS.md

TiddlyFlashcards 是一个 Anki 2.1 插件（最低要求 Anki 26.09），用于从已配置的 TiddlyWiki 维基中导出闪卡数据，并将其同步到 Anki 集合。本项目没有打包构建流程：Anki 直接加载此仓库目录。

## 环境准备与验证

- 在本机上，插件目录 `~/.local/share/Anki2/addons21/TiddlyFlashcards` 是指向本仓库的符号链接。修改后重新加载 Anki 即可生效；无需安装或构建步骤。
- 没有测试套件，也没有 CI。验证方式为依次运行 `ruff check .`、`ruff format --check .`、`basedpyright`，然后通过 **Tools → TiddlyFlashcards Sync Cards** 手动执行一次同步。
- 目标 Python 版本为 3.13（`pyproject.toml`），即使系统 `python3` 可能更新。
- `guard.py` 未被 `__init__.py` 导入；它目前未接入，且存在 lint/类型检查错误。除非要修改该文件，否则不要把这些错误视为回归。
- 各模块无法独立运行：它们会导入 `aqt`/`anki` 并打开 Qt 对话框。运行时行为需要在 Anki 内部测试。
- “Reload Config Dialg(For Debugging)” 菜单项只会重新加载 `config.py`；其他模块的改动请使用 Anki 的插件重载/重启。

## 架构

- `__init__.py` — 插件入口。注册配置对话框和 Tools 菜单项。其导入语句在 Anki 启动时执行，因此导入错误会导致插件无法加载。
- `config.py` — 配置 dataclass/默认值以及 Qt 配置对话框。设置通过 `mw.addonManager` 持久化到插件的 `meta.json`（已 gitignore）。
- `exporter.py` — 为每个启用的 `WikiSource` 调用 TiddlyWiki CLI，并将渲染出的 JSON 解析为 `WikiExportData`。文件夹型与单文件 HTML 型维基使用不同的命令形式。
- `exceptions.py` — 同步流程的领域异常（校验与导出失败），只携带 `title` / `summary` / `details` 数据，不导入 Qt；由 `__init__.py` 捕获并交给 `utils.showError` 展示。
- `models.py` — 笔记类型/笔记的 dataclass 以及 CSS 组合。Anki 内部属性（`id`、`ord`、`mod` 等）被有意排除。
- `utils.py` — 错误报告对话框（标题常驻、详情折叠、复制调试信息）。公开入口为 `showError` / `showException`，命名对齐 `aqt.utils.showCritical`。
- `importer.py` — `AnkiModelRepository` / `AnkiNoteRepository` 的 CRUD 以及 `AnkiSyncer` 的差异编排。同步策略位于此处。
- `docs/` — 设计说明与 Anki 内部 `FieldDict` / `NotetypeDict` / `TemplateDict` / `Note` schema 的参考笔记，不是用户文档。同步前校验设计见 `docs/sync_validation.md`，导入/同步与删除流程见 `docs/import_flow.md`。

## 容易破坏的同步规则

- 笔记类型按 `name` 匹配；笔记按 `tf_id` 匹配，该值存储在 Anki 字段 `TFNoteId` 中。两个键都必须唯一，包括跨维基的情况。
- Note 的有效定义包含 `WikiSource` 派生字段（name/path/url 等）；因此同一 `tf_id` 跨 wiki 重复一律冲突，即使 `NoteSource` 内容相同。
- Anki 中的 notetype / note 是 TiddlyWiki 工作空间的单向映射（TiddlyWiki → Anki），不是独立数据源；因此目标设计包含删除：导出结果中缺失的受管对象最终应被删除。删除策略（受管范围、执行顺序、每次删除前的确认弹窗）见 `docs/import_flow.md`；当前尚未实现删除逻辑，落地前不得添加删除行为。
- 笔记类型的字段/模板布局仅允许追加。现有名称序列必须是目标序列的精确前缀；重排、删除或在中间插入都会抛出异常。笔记不能切换笔记类型。
- 缺失的牌组会自动创建。`status == "suspend"` 会暂停某条笔记的所有卡片；其他任何值都会取消暂停。
- 当没有启用的维基、所有启用维基合计导出零个 model 或零个 note、跨维基 model 定义冲突、note 引用未导出的 model，或 `TFNoteId` 重复时，同步会在任何写入之前中止。
- 同步前唯一公开入口是 `WikiManager.build_export()`：内部依次导出所有启用 wiki、调用 `_validate_results()` 校验、调用 `_merge_results()` 合并，返回合并后的 `WikiExportData`（同名 model 保留第一个）。校验必须保持独立，不要内联到导出循环；顺序、异常类型和 details 格式见 `docs/sync_validation.md`。
- 导出需要一个可用的 `tiddlywiki` 可执行文件（配置的路径或 `PATH`），以及每个维基中的渲染模板 tiddler `$:/plugins/uzvg/TiddlyFlashcards/templates/tf-render-template`。该模板随 TiddlyWiki 插件提供，不在本仓库中。CLI 形式记录在 `docs/tiddlywiki_render.md`。

## 数据文件与代码生成

- `tf_notetypes.toml` 是声明式的笔记类型源文件。使用 `python tools/render_notetypes.py`（可附加 `--url ...`）重新生成 `tf_notetypes.json`，不要手动编辑该 JSON。
- `tf_notetypes.toml` 中的注释引用了 `parse_notetypes()` / `render_specs()` / `REBUILD_PENDING`；这些符号在本仓库中并不存在。

## 约定

- ruff 目标为 Python 3.13，行宽 88，双引号，并启用 isort/pyupgrade/bugbear/simplify 规则。E501 和全角标点检查被有意禁用。
