"""
TiddlyFlashcards 模板notetype 同步：定义与逻辑分离，
    五阶段流程（Render → Diff → Plan → Apply → Verify）。

定义源为 tf_notetypes.toml（声明式，只读不改写），代码只负责解析与渲染：
    |parse_notetypes()  |读取 tf_notetypes.toml → List[ModelSpec]（含 __HOST__ 占位，用于替换用户自义的url端口地址）
    |render_specs()     |注入配置（api_url / card_css）→ List[ModelSpec]
    |compute_checksums()|一次算齐双指纹（结构 + 内容），Plan 与 Apply 之间复用
    |plan_models()      |纯函数，只读 Anki 集合、产出动作清单（可 dry-run）
    |apply_plan()       |按计划落库：最小写入 + 批量原子性（失败回滚）
    |verify_models()    |执行后重算 content_checksum，核对写入的 tf_checksum
    |sync_models()      |五阶段总编排入口

变更分级：
    先比结构指纹（字段名序列 + model_type + 模板名序列），再比内容指纹（整个ModelSpec的所有内容）。
    结构不同 → 说明模板更新会产生破坏性（REBUILD_PENDING，跳过 + 警告，不自动更新，比如为模板增加了新的字段值，或者改变了模板名称/数量）；
    结构相同、内容不同 → 非破坏性（CONTENT_UPDATE，比如只是CSS、qfmt、afmt发生了改变，可以更新，执行最小写入）。
"""

import copy
import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from textwrap import dedent

import tomllib
from anki.collection import Collection
from anki.models import NotetypeDict
from aqt import mw
from aqt.utils import showInfo, tooltip

from .config import load_config

# --------------------------------------------------
# 常量 / 插件内置资源
# --------------------------------------------------

NOTETYPES_FILE = Path(__file__).parent / "tf_notetypes.toml"
DEFAULT_CSS_FILE = Path(__file__).parent / "tf_card.css"

HOST_PLACEHOLDER = "__HOST__"
TF_MANAGED_KEY = "tf_managed"
TF_CHECKSUM_KEY = "tf_checksum"


# --------------------------------------------------
# 动作常量（SCREAMING_SNAKE 枚举）
# --------------------------------------------------


class ModelAction(Enum):
    CREATE = "CREATE"
    NOOP = "NOOP"
    CONTENT_UPDATE = "CONTENT_UPDATE"
    REBUILD_PENDING = "REBUILD_PENDING"
    SKIP_CONFLICT = "SKIP_CONFLICT"


# --------------------------------------------------
# DSL 数据结构
# --------------------------------------------------


@dataclass
class Field:
    name: str


@dataclass
class Template:
    name: str
    front: str
    back: str


@dataclass
class ModelSpec:
    name: str
    fields: list[Field]
    templates: list[Template]
    model_type: int = 0  # 0 普通卡片，1 cloze
    css: str = ""


@dataclass(frozen=True)
class ChecksumPair:
    structural: str
    content: str


@dataclass
class ModelPlanItem:
    spec: ModelSpec
    action: ModelAction
    checksums: ChecksumPair
    model: NotetypeDict | None = None


@dataclass
class ModelPlan:
    items: list[ModelPlanItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ModelSyncReport:
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    verify_errors: list[str] = field(default_factory=list)
    dry_run: bool = False


# --------------------------------------------------
# ① 定义 + 渲染 Render
# --------------------------------------------------


def parse_notetypes() -> list[ModelSpec]:
    """读取 tf_notetypes.toml，解析为原始 ModelSpec（含 __HOST__ 占位）。

    定义文件是唯一来源，解析失败即中止同步，避免带病定义入库。
    """
    try:
        with open(NOTETYPES_FILE, "rb") as f:
            raw = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError(
            f"解析 notetype 定义文件失败（{NOTETYPES_FILE}）：{exc}"
        ) from exc

    specs: list[ModelSpec] = []
    for name, cfg in raw.items():
        specs.append(
            ModelSpec(
                name=name,
                model_type=cfg.get("model_type", 0),
                fields=[Field(n) for n in cfg["fields"]],
                templates=[
                    Template(name=t["name"], front=t["front"], back=t["back"])
                    for t in cfg["templates"]
                ],
            )
        )
    return specs


def render_specs(specs: list[ModelSpec]) -> list[ModelSpec]:
    """注入配置：替换 __HOST__ 占位、组装 css。

    配置与 CSS 只解析/读取一次，所有 spec 共享结果。
    """
    config = load_config()
    host = config.api_url
    user_css = config.card_css
    css = build_css(user_css)

    rendered_specs: list[ModelSpec] = []
    for spec in specs:
        templates = [
            Template(
                name=t.name,
                front=dedent(t.front).replace(HOST_PLACEHOLDER, host).strip(),
                back=dedent(t.back).replace(HOST_PLACEHOLDER, host).strip(),
            )
            for t in spec.templates
        ]
        rendered_specs.append(
            ModelSpec(
                name=spec.name,
                fields=spec.fields,
                model_type=spec.model_type,
                templates=templates,
                css=dedent(css).strip(),
            )
        )
    return rendered_specs


def build_css(user_css: str) -> str:
    """组装默认 CSS（tf_card.css）与用户自定义 CSS（card_css）。"""
    default_css = DEFAULT_CSS_FILE.read_text(encoding="utf-8")

    final_css = f"""
        /* ===== Default CSS ===== */
        {default_css}

        /* ===== User CSS ===== */
        {user_css}
    """
    return final_css


# --------------------------------------------------
# ② 比对 Diff · 双指纹
# --------------------------------------------------


def checksum(data) -> str:
    """返回 JSON 可序列化数据的 SHA-256 指纹。"""
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _structural_data(
    field_names: list[str], model_type: int, template_names: list[str]
) -> dict:
    return {
        "fields": field_names,
        "model_type": model_type,
        "template_names": template_names,
    }


def _content_data(field_names: list[str], model_type: int, templates, css: str) -> dict:
    return {
        "fields": field_names,
        "model_type": model_type,
        "templates": [
            {"name": name, "front": front, "back": back}
            for name, front, back in templates
        ],
        "css": css,
    }


def compute_checksums(spec: ModelSpec) -> ChecksumPair:
    """一次算齐双指纹（结构 + 内容），供 Plan 与 Apply 复用。"""
    field_names = [f.name for f in spec.fields]
    template_names = [t.name for t in spec.templates]

    structural_checksum = checksum(
        _structural_data(field_names, spec.model_type, template_names)
    )
    templates = [(t.name, t.front, t.back) for t in spec.templates]
    content_checksum = checksum(
        _content_data(field_names, spec.model_type, templates, spec.css)
    )
    return ChecksumPair(structural=structural_checksum, content=content_checksum)


def anki_checksums(model: NotetypeDict) -> ChecksumPair:
    """计算 Anki 现有 notetype 的双指纹，与 ModelSpec 口径一致。"""
    field_names = [f["name"] for f in model["flds"]]
    template_names = [t["name"] for t in model["tmpls"]]

    structural_checksum = checksum(
        _structural_data(field_names, model["type"], template_names)
    )
    templates = [
        (t["name"], t["qfmt"].strip(), t["afmt"].strip()) for t in model["tmpls"]
    ]
    content_checksum = checksum(
        _content_data(field_names, model["type"], templates, model["css"].strip())
    )
    return ChecksumPair(structural=structural_checksum, content=content_checksum)


# --------------------------------------------------
# ③ 规划 Plan · 纯函数
# --------------------------------------------------


def plan_models(col: Collection, specs: list[ModelSpec]) -> ModelPlan:
    """只读 Anki 集合，产出动作清单（不落库），可 dry-run。

    判定顺序：先比结构（破坏性），再比内容（非破坏性）。
    """
    mm = col.models
    plan = ModelPlan()

    for spec in specs:
        spec_checksums = compute_checksums(spec)

        # 找到Anki中对应的model
        model = mm.by_name(spec.name)

        # 不存在 → 全新创建
        if model is None:
            plan.items.append(
                ModelPlanItem(
                    spec=spec,
                    action=ModelAction.CREATE,
                    checksums=spec_checksums,
                )
            )
            continue

        # 存在但未被本插件托管 → 命名冲突，跳过并提示
        if not model.get(TF_MANAGED_KEY):
            plan.warnings.append(
                f"模型 {spec.name} 存在同名 but 未被本插件管理的 notetype，"
                "已跳过（SKIP_CONFLICT）。如需接管，请人工重命名或删除该 notetype 后重新同步。"
            )
            plan.items.append(
                ModelPlanItem(
                    spec=spec,
                    action=ModelAction.SKIP_CONFLICT,
                    model=model,
                    checksums=spec_checksums,
                )
            )
            continue

        anki_pair = anki_checksums(model)

        # 先比结构：不同 → 破坏性变更，不自动执行
        if spec_checksums.structural != anki_pair.structural:
            plan.warnings.append(
                f"模型 {spec.name} 发生破坏性结构变更（字段/模板增删改名、类型互换），"
                "已跳过（REBUILD_PENDING）。不自动重建以免字段错位或丢调度，"
                "请人工处理：删除旧 notetype 后重新同步，或手动执行迁移。"
            )
            action = ModelAction.REBUILD_PENDING
        # 结构相同、内容不同 → 非破坏性更新（只影响卡片外观/排版）
        elif spec_checksums.content != anki_pair.content:
            action = ModelAction.CONTENT_UPDATE
        # 完全一致 → 零写入（幂等保证）
        else:
            action = ModelAction.NOOP

        plan.items.append(
            ModelPlanItem(
                spec=spec,
                action=action,
                model=model,
                checksums=spec_checksums,
            )
        )

    return plan


# --------------------------------------------------
# ④ 执行 Apply · 最小写入 + 原子性
# --------------------------------------------------


def apply_plan(col: Collection, plan: ModelPlan) -> list[ModelPlanItem]:
    """按计划落库；只写有差异的项，任一失败即回滚并报错。"""
    mm = col.models
    applied: list[ModelPlanItem] = []

    # 备份待更新 notetype 的现状，供失败回滚
    snapshots: dict[int, dict] = {}
    created_ids: list[int] = []

    try:
        for item in plan.items:
            if item.action is ModelAction.CREATE:
                created = _apply_create(mm, item)
                created_ids.append(created["id"])
                applied.append(item)
            elif item.action is ModelAction.CONTENT_UPDATE:
                assert item.model is not None
                snapshots[item.model["id"]] = copy.deepcopy(item.model)
                _apply_content_update(mm, item)
                applied.append(item)
            # NOOP / REBUILD_PENDING / SKIP_CONFLICT：零写入
    except Exception as exc:
        _rollback(mm, snapshots, created_ids)
        raise RuntimeError(f"模型同步失败，已回滚：{exc}") from exc

    return applied


def _apply_create(mm, item: ModelPlanItem) -> NotetypeDict:
    """全新创建 notetype，打 tf_managed 标记 + 双指纹（tf_checksum）。"""
    spec = item.spec
    model = mm.new(spec.name)
    model["type"] = spec.model_type

    for spec_field in spec.fields:
        f = mm.new_field(spec_field.name)
        mm.add_field(model, f)

    for tmpl in spec.templates:
        t = mm.new_template(tmpl.name)
        t["qfmt"] = tmpl.front
        t["afmt"] = tmpl.back
        mm.add_template(model, t)

    model["css"] = spec.css
    model[TF_MANAGED_KEY] = True
    model[TF_CHECKSUM_KEY] = item.checksums.content

    mm.add(model)
    item.model = model
    return model


def _apply_content_update(mm, item: ModelPlanItem) -> None:
    """最小写入：逐字段、逐模板、逐 CSS 比对，只写有差异的项。

    结构指纹相同 ⇒ 字段/模板数量与名称、model_type 均一致，
    此路径只允许非破坏性变化（模板内容 qfmt/afmt、css）。
    """
    spec = item.spec
    assert item.model is not None
    model = copy.deepcopy(item.model)
    changed = False

    # 显式确保 model["type"] 与 model_type 一致（一旦真的变化，已归入 REBUILD_PENDING）
    if model["type"] != spec.model_type:
        model["type"] = spec.model_type
        changed = True

    for f, spec_field in zip(model["flds"], spec.fields):
        if f["name"] != spec_field.name:
            f["name"] = spec_field.name
            changed = True

    for t, spec_tmpl in zip(model["tmpls"], spec.templates):
        if t["name"] != spec_tmpl.name:
            t["name"] = spec_tmpl.name
            changed = True
        if t["qfmt"] != spec_tmpl.front:
            t["qfmt"] = spec_tmpl.front
            changed = True
        if t["afmt"] != spec_tmpl.back:
            t["afmt"] = spec_tmpl.back
            changed = True

    if model["css"] != spec.css:
        model["css"] = spec.css
        changed = True

    if not changed:
        return

    model[TF_MANAGED_KEY] = True
    model[TF_CHECKSUM_KEY] = item.checksums.content
    mm.update_dict(model)


def _rollback(mm, snapshots: dict[int, dict], created_ids: list[int]) -> None:
    """尽力回滚：恢复已更新的 notetype、删除新建的 notetype。"""
    try:
        for mid, snapshot in snapshots.items():
            mm.update_dict(snapshot)
        for mid in created_ids:
            mm.remove(mid)
    except Exception as exc:
        raise RuntimeError(f"模型同步失败，且回滚未完全成功：{exc}") from exc


# --------------------------------------------------
# ⑤ 校验 Verify · 闭环保证
# --------------------------------------------------


def verify_models(col: Collection, applied: list[ModelPlanItem]) -> list[str]:
    """执行后重算 content_checksum，核对写入的 tf_checksum 一致。"""
    errors: list[str] = []
    mm = col.models

    for item in applied:
        model = mm.by_name(item.spec.name)
        if model is None:
            errors.append(f"模型 {item.spec.name} 同步后未找到，校验失败。")
            continue
        stored = model.get(TF_CHECKSUM_KEY)
        if stored != item.checksums.content:
            errors.append(
                f"模型 {item.spec.name} 校验失败：tf_checksum 与内容指纹不一致。"
            )
    return errors


# --------------------------------------------------
# 同步入口 · 五阶段总编排
# --------------------------------------------------


def sync_models(col: Collection, dry_run: bool = False) -> ModelSyncReport:
    """五阶段同步：Render → Diff → Plan → Apply → Verify。

    dry_run=True 时只做 ①-③（渲染 + 比对 + 规划），不落库，供预览。
    """
    # ① 定义 + 渲染
    specs = render_specs(parse_notetypes())

    # ② 比对 + ③ 规划（纯函数，可 dry-run）
    plan = plan_models(col, specs)

    report = ModelSyncReport(dry_run=dry_run)

    # ④ 执行 + ⑤ 校验（dry-run 不触碰 Anki）
    if not dry_run:
        applied = apply_plan(col, plan)
        report.verify_errors = verify_models(col, applied)
        col.save()
        if applied:
            mw.reset()

    report.warnings = plan.warnings
    for item in plan.items:
        report.counts[item.action.value] = report.counts.get(item.action.value, 0) + 1

    show_report(report)
    return report


def show_report(report: ModelSyncReport) -> None:
    """展示同步摘要（与 importer 的摘要报告口径一致）。"""
    prefix = "DRY-RUN " if report.dry_run else ""
    lines = [f"TiddlyFlashcards Model Sync {prefix}Summary:"]
    for action in ModelAction:
        lines.append(f"  {action.value}: {report.counts.get(action.value, 0)}")
    for warning in report.warnings:
        lines.append(f"  WARN: {warning}")
    for error in report.verify_errors:
        lines.append(f"  FAIL: {error}")

    body = "\n".join(lines)

    is_clean = not (report.warnings or report.verify_errors)
    is_quiet = (
        report.counts.get(ModelAction.CREATE.value, 0) == 0
        and report.counts.get(ModelAction.CONTENT_UPDATE.value, 0) == 0
    )
    if is_clean and is_quiet:
        tooltip("TiddlyFlashcards: 所有 notetype 已是最新")
    else:
        showInfo(body)
