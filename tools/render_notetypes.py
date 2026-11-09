#!/usr/bin/env python3
"""把 tf_notetypes.toml 一对一翻译成 tf_notetypes.json（供调试/预览）。

仅做 TOML → JSON 原样转换：不改键名（model_type / front / back 等保持原样）、
不注入 sortf / css 等额外字段、不替换 {{WikiUrl}} 占位符。
{{WikiUrl}} 由笔记中的 WikiUrl 字段在 Anki 中解析。

用法：
    python tools/render_notetypes.py
    python tools/render_notetypes.py --input tf_notetypes.toml --output tf_notetypes.json
"""

import argparse
import json
import sys
import tomllib
from pathlib import Path

DEFAULT_INPUT = Path(__file__).resolve().parent.parent / "tf_notetypes.toml"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "tf_notetypes.json"


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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.input.is_file():
        print(f"Input file not found: {args.input}", file=sys.stderr)
        return 1

    with args.input.open("rb") as handle:
        toml = tomllib.load(handle)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(toml, handle, ensure_ascii=False, indent=2)

    print(f"Rendered {len(toml)} notetypes ({args.input}) -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
