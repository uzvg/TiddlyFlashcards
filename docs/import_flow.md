# 导入（同步）流程

本文描述 `importer.py` 如何把 `WikiManager.build_export()` 产出的合并数据写进 Anki Collection，是 TiddlyWiki → Anki 单向同步的**写端**设计。导出前的校验与合并见 `docs/sync_validation.md`；本文覆盖创建、更新、删除与挂起状态同步，以及删除前的确认交互。

## 1. 定位与分层

importer 不关心数据从哪里来，只消费干净的结构化输入。三层职责（与 `importer.py` 模块注释一致）：

| 层 | 职责 |
|---|---|
| `AnkiModelRepository` | Anki Notetype 的读取与 CRUD，`name` 为唯一键 |
| `AnkiNoteRepository` | Anki Note 的读取与 CRUD，`TFNoteId` 字段值为唯一键；并负责挂起状态同步 |
| `AnkiSyncer` | 接受 `WikiExportData`，内部合并提取 `ModelSource`/`NoteSource`，再做集合差集编排：计算同步计划、触发确认、按序执行写操作 |

`AnkiSyncer` 不直接触碰 Anki 内部字典/数据库；所有读写都经仓库类完成。UI（报告、删除确认弹窗）由 `__init__.py` / `utils.py` 负责，同步器只通过返回值或回调把「待删除计划」交给 UI 层。

## 2. 输入契约

`AnkiSyncer` 直接接受 `build_export()` 返回的 `WikiExportData`，并通过内部提取函数 `extract_sources()` 把它提取为 `ModelSource` / `NoteSource` 列表：

- 同名 model 已在导出阶段去重并保留第一个，此处仅从每个 `ExportModel` 取出 `model_info`。
- note 从每个 `ExportNote` 取出 `note_info`。

`extract_sources()` 是唯一同时拿到 `note_info` 与 `wiki_info` 的位置，也是把 wiki 派生信息折入 note 字段的指定接缝：未来若需把 `WikiSource` 的 name / url 写入 note 字段（如 `WikiName` / `WikiUrl`，用于多 wiki 的逐卡片来源标识与 wiki 链接），只在此处完成，不得散落到仓库层或同步编排。注入字段只作为数据/溯源进入 `fields`，不改变 `tf_id` 作为唯一匹配键的身份语义。

该输入已由导出阶段保证（详见 `sync_validation.md`）：

- model / note 均非空（空则已在导出阶段中止）。
- `tf_id` 全局唯一。
- note 引用的 `model_name` 必然存在于导出的 model 集合中。
- 跨 wiki 的同名 model 定义一致。

importer 信任这份契约，不再重复校验；校验集中在上游导出阶段，避免同一规则在两处实现而漂移。

## 3. 身份与匹配键

- **Notetype** 以 `ModelSource.name` 为唯一匹配键。
- **Note** 以 `NoteSource.tf_id`（存储在 Anki note 的 `TFNoteId` 字段）为唯一匹配键。
- 匹配使用精确字符串比较，区分大小写，不做 trim 或大小写归一化。
- Note 不能跨 notetype 移动：`update()` 遇到 `model_name` 不一致即报错。

## 4. 受管范围（决定删除边界）

删除只能作用于插件「拥有」的对象。当前没有显式所有权记录，因此用标记字段推断：

- **受管 Note** = `TFNoteId` 字段值非空。这等价于 `AnkiNoteRepository.get_all()` 的筛选条件 `"TFNoteId:*"`。
- **受管 Model** = notetype 含 `TFNoteId` 字段。这等价于 `get_all()` 里能检索到 `TFNoteId` 字段的 notetype 集合。

只有受管对象可被删除。用户自建的 notetype / note 不满足上述标记，绝不触碰。

> **无 per-wiki 标识**：Anki note 中不持久化「来自哪个 wiki」的信息（`WikiSource` 的 name/path/url 只存在于导出阶段的 `ExportNote` 上）。因此删除**无法按 wiki 范围化**，只能是全局——见第 6 节的风险说明。

## 5. 同步阶段

一次同步分三步，且**先计划、后确认、再执行**，保证删除确认发生在任何写操作之前。入口 `sync_all(data: WikiExportData)` 先调用 `extract_sources()` 提取出 `ModelSource` / `NoteSource`，再进入下面的计划 / 确认 / 执行。

### 5.1 计算计划（只读，不写）

用仓库的读方法（`get_all` / `get_by_name` / `get_by_tf_id`）取得当前状态，与输入做集合差集，得到：

- `models_to_create` / `models_to_update`（按 `name`）
- `notes_to_create` / `notes_to_update`（按 `tf_id`）
- `models_to_delete`：受管且不在输入中的 notetype
- `notes_to_delete`：受管且不在输入中的 note

计划阶段不得产生任何写。`models_to_update` 的判定沿用 `ModelSource` 的 dataclass 相等性（`name`/`type`/`sortf`/`css`/`flds`/`tmpls`，顺序敏感）；`notes_to_update` 沿用 `_notes_equal`（字段、牌组、标签、状态，标签按无序集合比较）。

### 5.2 确认（仅当有删除）

若 `len(notes_to_delete) + len(models_to_delete) > 0`，在**任何写之前**弹出警告对话框，展示：

- 待删除的 note 数量与 notetype 数量；
- 样本预览（受管 note 的 `tf_id` 与受管 model 的 `name`，样本与分组数量受限，参考 `sync_validation.md` 第 6 节的 `DETAIL_SAMPLE_LIMIT` / `DETAIL_GROUP_LIMIT` 思路，避免详情随删除规模线性膨胀）。

用户**确认**则继续执行；**取消**则中止本次同步，不产生任何写（包括 create/update）。

### 5.3 执行（写，按固定顺序）

确认通过（或本次无删除）后，按以下顺序执行：

1. **Model 创建/更新**：先建缺失 notetype、再更新变更 notetype。notetype 字段/模板仅允许在原有序列之后追加（`_verify_structure_compatible`）。
2. **Note 创建/更新**：先建缺失 note、再更新变更 note；对新增与已有 note 均执行 `sync_status` 纠正挂起状态。
3. **Note 删除**：删除 `notes_to_delete`。
4. **Model 删除**：删除 `models_to_delete`。

顺序理由：

- Note 依赖其所属 notetype 存在，故 model 必须先于 note 建立。
- 受管 note 须先于 notetype 删除，使每张受管 note 的删除都被显式统计、计入确认弹窗，而不是随 notetype 删除被隐式级联；故 note 先删、model 后删。

## 6. 删除策略

- **无配置开关**：删除始终是单向同步语义的一部分；每次同步只要会产生删除，就按第 5.2 节弹确认。
- **全局范围**：所有 TF 受管对象统一参与差集。因无 per-wiki 标识，**禁用或移除某个 wiki 会使其卡片不再出现在导出结果中，从而在下次同步被删除**。这是单向同步的既定语义，已在 `sync_validation.md` 第 1 节声明；用户在弹窗中看到数量预览，需自行确认。
- **空导出护栏**：`build_export()` 在合计空 model / 空 note 时即中止（`EmptyModelExportError` / `EmptyNoteExportError`），避免空导出触发「全量删除」。这是删除安全的第一道、也是最重要的一道防线。
- **model 直接删除**：若某受管 notetype 仍被**非受管** note 使用（例如用户手动用 TF notetype 建了不带 `TFNoteId` 的 note），直接删除该 notetype，这些 note 会随 notetype 一并被移除；此行为会在用户手册中说明，不做拒绝。受管 note 已在第 5.3 节第 3 步显式删除，不计入此处级联。

## 7. 报告

同步结束的报告在现有 `total / added / updated / unchanged` 基础上增加 `deleted` 计数，分别对 model 与 note 展示。删除计数仅在有删除的同步中出现。

示例：

```text
TiddlyFlashcards Sync Summary:
Models    - total 5, added 0, updated 1, unchanged 4, deleted 0
Notes     - total 120, added 3, updated 7, unchanged 105, deleted 5
```

## 8. 失败处理

两类失败需区分：

- **写前领域异常**（`TiddlyFlashcardsError`：导出失败、校验失败、空导出等）：在计划阶段之前抛出，由 `begin_sync()` 捕获展示，Anki 集合保持原样。
- **写阶段异常**（`ValueError` / `RuntimeError`，例如 notetype 字段布局不兼容、note 跨 notetype 移动）：发生在执行阶段，会中止后续写，但**已完成的写不会回滚**（当前无事务）。这类异常需在 `begin_sync()` 中以 `showException` 兜底，向用户说明「部分写入可能已发生」，而不是静默失败。

## 9. 已知风险与非目标

- 无 per-wiki 标识：禁用/移除 wiki 在下次同步会删除其卡片，删除范围无法缩小到单个 wiki。
- 不实现 `(wiki, tf_id)` 复合身份；若未来需要同一 `tf_id` 在不同 wiki 各自独立存在，须先设计 `WikiName` 字段或复合键，并连同匹配、迁移与删除语义一并设计（见 `sync_validation.md` 第 8 节）。
- 无事务/回滚：写阶段失败可能留下部分写入，需靠报告与错误提示让用户知情。
