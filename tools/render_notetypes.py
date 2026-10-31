#!/usr/bin/env python3
"""Render tf_notetypes.toml to a JSON file.

把声明式 notetype 定义（tf_notetypes.toml）渲染成 JSON，供调试/预览。
模板中的 {{WikiUrl}} 占位符会替换为 --url 指定的地址（默认 http://127.0.0.1:8080）。

用法：
    python tools/render_notetypes.py
    python tools/render_notetypes.py --input tf_notetypes.toml --output tf_notetypes.json --url http://localhost:8080
"""

import argparse
import json
import sys
import tomllib
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8080"
HOST_PLACEHOLDER = "{{WikiUrl}}"

DEFAULT_INPUT = Path(__file__).resolve().parent.parent / "tf_notetypes.toml"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "tf_notetypes.json"


def render_source(toml: dict, url: str) -> dict:
    """把 tomllib 解析结果转换为 Anki 风格的 ModelSource 字典。

    每个 notetype 段：
        [Name]
        model_type = 0
        fields = [...]
        [[Name.templates]]
        name = "Card 1"
        front = "..."
        back = "..."
    转换为：
        { "type", "sortf", "css", "flds", "tmpls": [ {"name","qfmt","afmt"} ] }
    """
    source: dict = {}
    for name, cfg in toml.items():
        if not isinstance(cfg, dict) or "templates" not in cfg:
            continue
        templates = cfg["templates"]
        source[name] = {
            "type": cfg.get("model_type", 0),
            "sortf": 0,
            "css": "",
            "flds": list(cfg.get("fields", [])),
            "tmpls": [
                {
                    "name": t.get("name", ""),
                    "qfmt": t.get("front", "").replace(HOST_PLACEHOLDER, url),
                    "afmt": t.get("back", "").replace(HOST_PLACEHOLDER, url),
                }
                for t in templates
            ],
        }
    return source


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="Source TOML file (default: tf_notetypes.toml)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output JSON file (default: tf_notetypes.json)",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"Host URL replacing {HOST_PLACEHOLDER} (default: {DEFAULT_URL})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.input.is_file():
        print(f"Input file not found: {args.input}", file=sys.stderr)
        return 1

    with args.input.open("rb") as handle:
        toml = tomllib.load(handle)

    source = render_source(toml, args.url)
    payload = {"tf_models": source}

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    print(f"Rendered {len(source)} notetypes ({args.input}) -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
