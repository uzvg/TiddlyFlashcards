# TemplateDict 定义笔记

## 类型定义位置
**文件**: `pylib/anki/models.py` (第36行)
```python
TemplateDict = dict[str, Union[str, int, None]]
```

## 特点
- Python 中是 `dict[str, Union[str, int, None]]` 的类型别名
- 值类型限制为 `str`、`int` 或 `None`
- 实际结构由 Rust 的 JSON 序列化/反序列化决定

## 完整的结构定义
**文件**: `rslib/src/notetype/schema11.rs` (Rust) — `CardTemplateSchema11` 结构体 (第352-373行)

### 核心字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | String | 卡片模板名称 |
| `ord` | Option\<u16\> | 模板序号（可选） |
| `qfmt` | String | 问题面模板格式 (question format) |
| `afmt` | String | 答案面模板格式 (answer format) |
| `bqfmt` | String | 浏览器问题面格式 (browser question format) |
| `bafmt` | String | 浏览器答案面格式 (browser answer format) |
| `did` | Option\<DeckId\> | 目标 Deck ID（可选） |
| `bfont` | String | 浏览器字体名称 |
| `bsize` | u8 | 浏览器字体大小 |
| `id` | Option\<i64\> | 模板 ID（可选） |
| `other` | HashMap | 其他自定义字段 |

### JSON 序列化字段名 (camelCase)

```
name, ord, qfmt, afmt, bqfmt, bafmt, did, bfont, bsize, id
```

### 默认值 (CardTemplateSchema11::default)
```rust
CardTemplateSchema11 {
    name: String::new(),
    ord: None,
    qfmt: String::new(),
    afmt: String::new(),
    bqfmt: String::new(),
    bafmt: String::new(),
    did: None,
    bfont: String::new(),
    bsize: 0,
    id: None,
    other: Default::default(),
}
```

### 保留字段（不能在 other 中重复）
```
name, ord, did, afmt, bafmt, qfmt, bqfmt, bfont, bsize, id
```

## 关键字段说明

### 模板格式字段
- **`qfmt`** - 前面（question）模板，包含 `{{字段名}}` 占位符
- **`afmt`** - 背面（answer）模板，通常包含 `{{FrontSide}}` 和字段
- **`bqfmt`** - 浏览器前面模板（browsing mode）
- **`bafmt`** - 浏览器背面模板（browsing mode）

### 浏览器字体设置
- **`bfont`** - 浏览器显示时使用的字体
- **`bsize`** - 浏览器显示时的字体大小

## Python 中的使用

模板在 Python 中通常这样创建：

```python
template = {
    "name": "Card 1",
    "ord": None,
    "qfmt": "{{Front}}",
    "afmt": "{{FrontSide}}<hr id=answer>\n{{Back}}",
    "bqfmt": "",
    "bafmt": "",
    "did": None,
    "bfont": "",
    "bsize": 0,
    "id": None
}
```