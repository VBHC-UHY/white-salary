"""
builtin/ — 内置工具分类目录。

每个.py文件导出一个 TOOLS 列表，格式：
  TOOLS = [
      {
          "name": "工具名",
          "description": "工具描述",
          "parameters": {JSON Schema},
          "handler": async_function,
      },
      ...
  ]

registry.py 会自动扫描这个目录下所有文件，加载所有TOOLS。
加新工具只需要在对应分类文件里加一条，或创建新的分类文件。
"""
