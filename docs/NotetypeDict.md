# NotetypeDict 定义笔记

## 类型定义位置
**文件**: `pylib/anki/models.py` (第34行)
```python
NotetypeDict = dict[str, Any]
```

## 特点
- Python 中仅是 `dict[str, Any]` 的类型别名（通用字典）
- 没有明确的字段定义
- 实际结构由序列化/反序列化决定

## 完整的结构定义
**文件**: `rslib/src/notetype/schema11.rs` (Rust)

### 核心字段
| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | NotetypeId | Notetype ID |
| `name` | String | Notetype 名称 |
| `type` | NotetypeKind | 类型（0=Standard, 1=Cloze） |
| `mod` | TimestampSecs | 修改时间戳 |
| `usn` | Usn | 更新序列号 |
| `sortf` | u16 | 排序字段索引 |
| `did` | Option<DeckId> | 目标 Deck ID（可选） |
| `tmpls` | Vec | 卡片模板列表 |
| `flds` | Vec | 字段定义列表 |
| `css` | String | 样式表 |
| `latexPre` | String | LaTeX 前缀 |
| `latexPost` | String | LaTeX 后缀 |
| `latexsvg` | bool | 是否使用 LaTeX SVG |
| `req` | CardRequirements | 字段需求 |
| `other` | HashMap | 其他字段 |

## 关键点
- Python 中通过 `from_json_bytes()` / `to_json_bytes()` 与后端 Rust 结构互转
- 实际的精确定义在 Rust 代码中
- Python 层面保持灵活的 `dict[str, Any]` 设计