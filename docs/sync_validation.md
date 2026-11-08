# 多 Wiki 同步校验与合并

本文描述 `exporter.py` 中 `WikiManager` 的导出后校验与合并设计。校验在**所有启用 wiki 导出完成后、任何 Anki 写入之前**执行；任一检查失败都会中止本次同步，并保持 Anki 集合不变。

## 1. 数据映射与不变量

- Anki 中的 notetype / note 是 TiddlyWiki 工作空间的单向映射（TiddlyWiki → Anki），不是独立数据源。目标设计包含删除：导出结果中缺失的受管对象最终应被删除。当前尚未实现删除逻辑，但删除是映射语义的一部分；在校验失败时直接中止，是为了避免在半套数据上执行后续映射。删除的执行顺序、受管范围与确认交互详见 `docs/import_flow.md`。
- Notetype 以 `ModelSource.name` 为唯一键：同一 `name` 在所有启用 wiki 中只能对应**一种定义**。同名且定义完全相同的 model 可以重复出现在多个 wiki；同名但定义不同即为冲突。
- Note 以 `NoteSource.tf_id` 为唯一键：同一 `tf_id` 在所有启用 wiki 中只能出现一次。
- 冲突判定的对象是最终写入 Anki 的“有效 note”：`NoteSource` 加上从 `WikiSource` 派生的字段（wiki name、path、url 等）。wiki 派生字段不同，跨 wiki 的同 `tf_id` 就必然是不同的 note；因此即使 `NoteSource` 内容相同，重复 `tf_id` 也一律视为冲突，不做“内容相同就放行”的特判。
- 匹配使用精确字符串比较，区分大小写，不做 trim 或大小写归一化。
- 同步只处理导出结果中的对象；校验失败时不得产生部分写入。

## 2. 入口与职责

`WikiManager.build_export()` 是同步前唯一的公开入口，内部依次执行：

1. 取启用 wiki；为空则抛 `NoEnabledWikiError`；逐个导出，按配置顺序得到 `wiki_exports: list[WikiExportData]`。
2. 调用 `_validate_results(sources, wiki_exports)` 对全部导出产物做独立校验。
3. 调用 `_merge_results(wiki_exports)` 合并数据，返回一个 `WikiExportData`；同名 model 只保留第一个 `ExportModel`，note 全部拼接。校验失败时不会执行合并。

`_validate_results(sources, wiki_exports)` 是独立的内部校验方法，直接接收 `build_export()` 已取到的 `sources` 与导出产物，便于单独测试，也避免重复读取配置。空 model / 空 note 的 details 需要列出启用 wiki，而 `ExportModel` / `ExportNote` 此时可能为空，无法从中取得 `wiki_info`，因此从 `sources` 构造 `(name, path)` 列表；不为此给 `WikiExportData` 增加字段。

`_merge_results(exported_wikis)` 返回的 `WikiExportData` 仍然是 `ExportModel` / `ExportNote` 列表，因此 `wiki_info` 自然保留；它只在校验阶段有意义，importer 导入时只读取 `model_info` / `note_info`。model 按 `model_info.name` 去重并保留第一个，是为了避免 importer 的全局唯一检查拒绝“同名且定义相同”的重复 model；note 不去重，`tf_id` 全局唯一已由 `_validate_results()` 保证。

`exceptions.py` 只定义异常类型、结构化冲突数据和用户可读的 `title` / `summary` / `details`，不导入 `aqt`、Qt 或 `exporter.py`，避免循环导入并保持可测试性。异常只负责描述失败，不负责弹窗。

UI 展示由 `__init__.py` 的 `begin_sync()` 统一完成：

```python
try:
    merged = manager.build_export()
    service.sync_all(merged, confirm_deletion=confirmDeletion)
except TiddlyFlashcardsError as exc:
    showError(ErrorReport(exc.title, exc.summary, exc.details))
    return
```

“停止程序”指中止本次同步并返回，不退出 Anki。底层 `_render_json()` 的失败（路径无效、子进程退出、超时、二进制缺失、JSON 解析失败）统一转换为 `WikiExportError`；配置的 `tiddlywiki` 二进制找不到时由 `_resolve_bin()` 抛 `TiddlyWikiBinaryError`。二者都继承 `TiddlyFlashcardsError`，由 `begin_sync()` 捕获并走同一错误对话框展示。

## 3. 校验顺序

顺序固定如下；先发现的问题先报告，避免在不可用数据上继续做无意义的检查。第 1 项由 `build_export()` 在导出前检查，第 2-6 项由 `_validate_results()` 按顺序检查。

| 顺序 | 条件 | 异常 |
|---|---|---|
| 1 | 没有启用的 wiki | `NoEnabledWikiError` |
| 2 | 所有启用 wiki 的 `model_sources` **合计**为空 | `EmptyModelExportError` |
| 3 | 同一 model name 存在不同定义 | `ConflictingModelError` |
| 4 | 所有启用 wiki 的 `note_sources` **合计**为空 | `EmptyNoteExportError` |
| 5 | note 引用了所有启用 wiki 都未导出的 `model_name` | `UnknownModelError` |
| 6 | 同一 `tf_id` 在多个位置出现（不比较内容） | `DuplicateTfIdError` |

空 model / 空 note 都按**所有启用 wiki 合计**判断，而不是要求每个 wiki 都非空。多 wiki 场景下，“只定义 model、不写卡片”和“只写卡片、引用其他 wiki 的 model”都是合法工作区，逐 wiki 报错会误杀正常配置。

当合计为空时，说明整个工作区或插件配置已经不可信。此时即使当前尚未实现删除，也必须硬中止；删除逻辑落地后，该条件会直接影响受管数据，更不能绕过。

## 4. 定义比较

`ModelSource` 使用 dataclass 相等性：`name`、`type`、`sortf`、`css`、`flds`、`tmpls` 全部参与比较，字段与模板的**顺序敏感**，与字段/模板只能追加的规则一致。

当前 `css` 由插件配置统一生成，所有 wiki 相同；如果未来支持 per-wiki CSS，需要重新评估该比较，否则每个重复 model 都会被判为冲突。

同一 model name 在多处出现但定义完全相同时，不报错；`_merge_results()` 合并时按 `model_info.name` 去重，只保留第一个 `ExportModel`。保留的 `wiki_info` 仅用于溯源，importer 不读取它。

Note 的 `model_name` 必须存在于导出的 model 集合中。该检查放在 Anki 写入之前，避免出现“model 已创建、note 创建时才失败”的部分写入。

Note 的重复判定不比较 `NoteSource` 内容。Anki 以 `TFNoteId`（即 `tf_id`）匹配 note，而 wiki 派生字段会进入 note 字段；一旦允许重复，后导入的 wiki 会覆盖同一条 Anki note 的 wiki 字段，结果依赖 wiki 遍历顺序。因此重复即中止，内容是否相同只影响错误说明，不影响结论。

## 5. 异常目录

所有异常继承 `TiddlyFlashcardsError`，至少提供：

- `title`：对话框顶部标题。
- `summary`：一句话说明影响范围或下一步操作，默认表达“同步已在写入前取消”。
- `details`：结构化详情，可能较长，由对话框折叠展示。

| 异常 | 载荷 |
|---|---|
| `NoEnabledWikiError` | 无 |
| `EmptyModelExportError` | 启用 wiki 的 `(name, path)` 列表 |
| `ConflictingModelError` | `{model_name: [(wiki_name, wiki_path), ...]}` |
| `EmptyNoteExportError` | 启用 wiki 的 `(name, path)` 列表 |
| `UnknownModelError` | `{model_name: [(wiki_name, wiki_path, tf_id), ...]}` |
| `DuplicateTfIdError` | `{tf_id: [(wiki_name, wiki_path), ...]}` |
| `WikiExportError` | `wiki (name, path)` + `reason`（导出失败原因） |
| `TiddlyWikiBinaryError` | 尝试的二进制路径字符串 |

载荷中的 wiki 信息使用 `(name, path)`，不传递 `WikiSource` 或 `ExportModel` / `ExportNote`，避免 `exceptions.py` 依赖 exporter 的类型。`WikiExportError` / `TiddlyWikiBinaryError` 在导出阶段抛出（先于上述校验），同样由 `begin_sync()` 统一展示。

## 6. Details 格式

### Model 冲突

model 数量有限，直接逐条列出：

```text
Found conflicting notetype definitions across wikis.

- TF Basic
  - Wiki A (/path/a)
  - Wiki B (/path/b)
```

同一定义重复出现在多个 wiki 时也会列出，便于用户判断重复范围；只有定义不同才会触发该异常。

### 未知 model

按 model name 聚合，避免同一缺失 model 被大量 note 重复刷屏：

```text
Notes reference notetypes that were not exported.

- TF Basic: 42 note(s)
  - Wiki A (/path/a): card-001, card-002, ... (showing 10 of 42)
```

### tf_id 冲突

重复判定不看 `note_info` 是否相同；内容相同只说明重复来源更可能是重复配置或共享卡片，不构成放行理由。若需要辅助诊断，可以在样本后附加 `content identical` / `content differs`，但这只是展示信息，不能改变中止行为。

**不要**为每个 `tf_id` 展开完整 wiki 列表。批量冲突时（例如两个 wiki 各自完整导出了同一批卡片），输出量会达到 `冲突数 × wiki 数` 行，details 文本可能达到数 MB，既难阅读也拖慢对话框。

改为按“冲突所涉及的 wiki 集合”分组，并限制样本数量：

```text
Found 512 tf_id conflicts across 3 wikis.

- Wiki A (/path/a) + Wiki B (/path/b): 500 conflict(s)
  e.g. card-001, card-002, ..., card-010 (showing 10 of 500)
- Wiki A (/path/a) + Wiki C (/path/c): 12 conflict(s)
  e.g. card-501, card-502, ..., card-510 (showing 10 of 12)
```

建议常量：

- `DETAIL_SAMPLE_LIMIT = 10`：每组最多展示的 `tf_id` 样本。
- `DETAIL_GROUP_LIMIT = 50`：最多展示的 wiki 组合组数，超出时追加 `... and N more group(s)`。

这样 details 长度只与“不同 wiki 组合数”相关，不随冲突卡片数量线性膨胀；同时仍然告知用户冲突发生在哪些 wiki、冲突规模多大、典型冲突卡片是哪些。若以后需要完整清单，应另提供“导出报告到文件”，而不是塞进对话框。

## 7. 算法与成本

### tf_id 重复检测

不需要为每个 `tf_id` 保存全部 `ExportNote` 或完整 wiki 列表。一次线性扫描即可：

1. 按配置顺序遍历启用 wiki，再遍历每个 wiki 的 notes。
2. `first_wiki: dict[tf_id, wiki_index]` 记录首次出现的位置。
3. 再次遇到同一 `tf_id` 时，把当前 wiki index 加入该 `tf_id` 的冲突 wiki 集合。
4. 同时累计冲突总数，并按 `frozenset(wiki_indices)` 聚合组计数与样本。

复杂度为 `O(N)` 时间、`O(唯一 tf_id 数)` 空间（`N` 为所有启用 wiki 的 note 总数）。这才是检测重复所必需的最小开销，量级为几千到几万条 note 时完全可接受。

真正需要控制的是**文本生成**：旧实现对每个冲突都保存 `ExportNote` 列表并逐条格式化，内存和输出都随冲突规模线性膨胀。新设计只保存 wiki 索引、分组计数和上限为 `DETAIL_SAMPLE_LIMIT` 的样本，因此不会出现批量冲突导致 details 超长的问题。

### Model 冲突与未知 model

- Model 冲突：按 name 分组，`O(M)`，`M` 为所有 wiki 的 model 总数，通常很小。
- 未知 model：先建立导出 model name 的集合，再线性扫描 notes，`O(N + M)`；details 同样按 model name 聚合并限制样本。

## 8. 非目标

- 不修改 `importer.py` 的同步行为；其同步/删除编排的权威设计见 `docs/import_flow.md`。
- 不在本次实现删除逻辑；但文档与规则必须承认 Anki 数据是 TiddlyWiki 工作空间的单向映射，删除最终会存在。
- 不支持 `(wiki, tf_id)` 复合身份。若未来需要同一 `tf_id` 在不同 wiki 中作为独立 note 共存，必须先设计稳定的 wiki id、`WikiName` 字段或复合键、importer 匹配逻辑、旧 note 迁移以及删除语义；不能仅靠放宽 `_validate_results()` 实现。
