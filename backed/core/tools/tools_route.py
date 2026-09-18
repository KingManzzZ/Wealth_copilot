import os
import json
import importlib
import inspect

# 自动发现 tools_hub 下所有 .py 模块，构建 工具名 -> 函数 的注册表
TOOL_REGISTRY = {}

_hub_dir = os.path.join(os.path.dirname(__file__), "tools_hub")

for entry in os.listdir(_hub_dir):
    if entry.startswith("_") or not entry.endswith(".py"):
        continue
    module_name = entry[:-3]  # "calculate.py" → "calculate"
    module = importlib.import_module(f".tools_hub.{module_name}", __package__)
    for name, func in inspect.getmembers(module, inspect.isfunction):
        TOOL_REGISTRY[name] = func
    print(f"[tools_route] 自动发现并注册的工具: {list(TOOL_REGISTRY.keys())}")


def route(tool_name: str, arguments: str) -> str:
    """
    路由函数：根据工具名查找注册表 -> 解析参数 -> 执行 -> 返回 JSON 字符串。
    """
    func = TOOL_REGISTRY.get(tool_name)
    if func is None:
        return json.dumps({"error": f"未知工具: {tool_name}"}, ensure_ascii=False)

    try:
        kwargs = json.loads(arguments)
        result = func(**kwargs)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)