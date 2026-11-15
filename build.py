#!/usr/bin/env python3
"""把插件源码打包成 Anki 2.1 的 `.ankiaddon` 分发包。

产物：dist/{package}-{human_version}.ankiaddon
- zip 根目录直接存放插件文件（__init__.py / manifest.json / ...），
  与 Anki 2.1.28+ 的 .ankiaddon 导入格式一致。
- 该包既可用于 GitHub Release，也可直接上传到 AnkiWeb。

用法：
    python build.py
"""

import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"

# 随包分发的数据文件（相对 ROOT）。根目录下的 *.py 会自动全部包含。
DATA_FILES = ("config.json", "default.css", "manifest.json")

# 根目录下这些文件是开发/参考文件，不应随插件分发。
EXCLUDE = {
    "build.py",
    "meta.json",
    "anki_template.html",
    "tf_notetypes.toml",
    "tf_notetypes.json",
}


def load_manifest() -> dict:
    return json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))


def collect_files() -> list[str]:
    py_files = {p.name for p in ROOT.glob("*.py")}
    return sorted((py_files | set(DATA_FILES)) - EXCLUDE)


def main() -> int:
    manifest = load_manifest()
    package = manifest.get("package") or "TiddlyFlashcards"
    version = manifest.get("human_version") or manifest.get("version") or "0.0.0"

    files = collect_files()
    DIST.mkdir(exist_ok=True)
    out = DIST / f"{package}-{version}.ankiaddon"

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            # arcname 保持顶层文件，不打入额外目录层级。
            archive.write(ROOT / name, arcname=name)

    print(f"Built {out} ({len(files)} files):")
    for name in files:
        print(f"  - {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())