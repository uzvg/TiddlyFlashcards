# Note Class 总结

## 来源与位置
- **文件**: `pylib/anki/notes.py`
- **仓库**: ankitects/anki
- **角色**: Python 库层，是 Anki 核心架构中的重要组件

## 定义与基础结构

### 继承关系
```python
class Note(DeprecatedNamesMixin):
```
继承自 `DeprecatedNamesMixin`，用于兼容旧版 API

### 核心属性
- `id: NoteId` - 笔记唯一标识
- `mid: NotetypeId` - 笔记类型ID
- `guid: str` - 全局唯一标识符
- `mod: int` - 修改时间戳
- `usn: int` - USN（更新序列号）
- `tags: list[str]` - 标签列表
- `fields: list[str]` - 字段内容列表
- `flags: int` - 标记（未当前暴露）
- `data: str` - 数据（未当前暴露）

## 主要功能模块

### 1. **生命周期管理**
- `__init__()` - 初始化（可从现有笔记或笔记类型创建）
- `load()` - 从后端加载笔记数据
- `_load_from_backend_note()` - 从 protobuf 对象加载
- `_to_backend_note()` - 转换为 protobuf 对象
- `flush()` - 保存到后端（已弃用，推荐用 `col.update_note()`）

### 2. **字段操作**
- `joined_fields()` - 合并所有字段
- `__getitem__()` / `__setitem__()` - 字典式字段访问
- `_field_index()` - 获取字段索引

### 3. **卡片相关**
- `cards()` - 获取该笔记的所有卡片
- `card_ids()` - 获取卡片ID列表
- `ephemeral_card()` - 创建临时卡片用于渲染预览
- `cloze_numbers_in_fields()` - 获取完形填空号

### 4. **标签管理**
- `has_tag()` - 检查是否有标签
- `add_tag()` - 添加标签
- `remove_tag()` - 移除标签
- `string_tags()` - 获取标签字符串
- `set_tags_from_str()` - 从字符串设置标签

### 5. **验证与检查**
- `fields_check()` / `duplicate_or_empty()` - 检查字段是否重复或为空
- `note_type()` - 获取笔记类型定义

### 6. **字典接口**
- `keys()`, `values()`, `items()` - 类似字典操作
- `__contains__()` - 成员检查

## 架构中的角色

Note Class 是 **Python 库层** 的一部分，作为 **Rust 核心层** 和上层应用（PyQt GUI、Web 前端）之间的桥梁：

```
Web Frontend (Svelte/TS) ─┐
PyQt GUI                  ├──→ Python Library (Note Class) ──→ Rust Core (rslib)
                          │    via Backend Interface
```

通过 protobuf 与后端通信，实现数据的双向转换和同步。