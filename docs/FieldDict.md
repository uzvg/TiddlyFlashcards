# FieldDict 定义笔记

## 类型定义位置
**文件**: `pylib/anki/models.py` (第35行)
```python
FieldDict = dict[str, Any]
```

## 特点
- Python 中仅是 `dict[str, Any]` 的类型别名（通用字典）
- 没有明确的字段定义
- 实际结构由 Rust 的 JSON 序列化/反序列化决定

## 完整的结构定义
**文件**: `rslib/src/notetype/schema11.rs` (Rust) — `NoteFieldSchema11` 结构体 (第231-268行)

### 核心字段

| 字段 | 类型 | Camel Case | 说明 |
|------|------|-----------|------|
| `name` | String | name | 字段名称 |
| `ord` | Option\<u16\> | ord | 字段序号（可选） |
| `sticky` | bool | sticky | 是否粘滞（保留上一次输入） |
| `rtl` | bool | rtl | 从右到左文字方向 |
| `font` | String | font | 字体名称 |
| `size` | u16 | size | 字体大小 |
| `description` | String | description | 字段描述/占位符文本 |
| `plain_text` | bool | plainText | 使用纯文本编辑器 |
| `collapsed` | bool | collapsed | 默认折叠 |
| `exclude_from_search` | bool | excludeFromSearch | 排除搜索 |
| `id` | Option\<i64\> | id | 字段 ID（可选） |
| `tag` | Option\<u32\> | tag | 标签（可选） |
| `prevent_deletion` | bool | preventDeletion | 防止删除 |
| `other` | HashMap | - | 其他自定义字段 |

### 默认值 (NoteFieldSchema11::default)
```
{
  "name": "",
  "ord": null,
  "sticky": false,
  "rtl": false,
  "plainText": false,
  "font": "Arial",
  "size": 20,
  "description": "",
  "collapsed": false,
  "excludeFromSearch": false,
  "id": null,
  "tag": null,
  "preventDeletion": false,
  "other": {}
}
```

### 保留字段（不能在 other 中重复）
```
name, ord, sticky, rtl, plainText, font, size, 
collapsed, description, excludeFromSearch, id, tag, preventDeletion
```

## 关键点
- Python 层使用 `from_json_bytes()` / `to_json_bytes()` 与 Rust 互转
- 使用 **camelCase** 序列化格式
- 多个字段是可选的，通过 `#[serde(default, ...)]` 处理无效值
- `other` 字段用于扩展性，存储自定义属性