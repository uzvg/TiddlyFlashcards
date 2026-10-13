# TiddlyWiki 不同类型的导出命令

各类型 wiki 在执行导出时的 tiddlywiki 命令：

- folder（wiki 目录）类型：
  ```
  tiddlywiki <wiki_path> --render <模板> <输出> text/plain
  ```
  `<wiki_path>` 是位置参数（wikipath / 工作目录）。

- html（单文件）类型：
  ```
  tiddlywiki --load <wiki_path> --render <模板> <输出> text/plain
  ```
  `<wiki_path>` 通过 `--load` 传入；无需位置参数 wikipath，命令可直接执行。

（来源：`exporter.py` 的 `WikiExporter._build_command()`）