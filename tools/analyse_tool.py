# tools/analyse_tool.py
"""
analyse_tool — 执行 LLM 生成的 Pandas 代码并返回结果。

约定：
  LLM 必须将最终结果赋值给名为 result 的变量，其类型可以是：
    - DataFrame → 作为 Markdown 表格返回
    - 标量      → 作为字符串返回
    - 字典      → 作为 JSON 返回

  工具将原始结果返回，LLM 再用其生成自然语言结论。

  沙箱为"约束层"而非绝对安全边界：
  - check_code_safety 静态检查：禁止 import、下划线属性、eval/exec/open/getattr 等
  - _build_sandbox 运行时：白名单 builtins + 仅暴露 df/pd/np/datetime/re/json
  对完全不可信的代码，建议放到子进程或容器中运行。
"""
import io
import sys
import json
import traceback
import threading
import pandas as pd
import numpy as np

from langchain.tools import tool
from config import MAX_ROWS_RESULT, SANDBOX_TIMEOUT

_df_accessor = None

def set_df_accessor(fn):
    global _df_accessor
    _df_accessor = fn


# 静态检查：在 exec 之前扫描代码，拦截明显的越权/逃逸写法。
# 注意：这只是一层"约束/防护"，不是绝对安全边界；
# 对完全不可信的代码应放到子进程或容器中运行。
_FORBIDDEN_CALLS = {
    "eval", "exec", "compile", "open", "input", "breakpoint",
    "globals", "locals", "vars", "getattr", "setattr", "delattr",
    "__import__", "memoryview",
}


def check_code_safety(code: str) -> None:
    """检查代码是否包含明显危险的写法，违规时抛出 ValueError。"""
    import ast

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise ValueError(f"代码语法错误：{e.msg}") from e

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError("禁止 import 新模块")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError(f"禁止访问以下划线开头的属性：{node.attr!r}")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _FORBIDDEN_CALLS
        ):
            raise ValueError(f"禁止调用函数：{node.func.id!r}")
        # 无界 while 循环是卡死页面的头号元凶，直接静态拦截
        if (
            isinstance(node, ast.While)
            and isinstance(node.test, ast.Constant)
            and bool(node.test.value)
        ):
            raise ValueError("禁止无界 while 循环（如 while True / while 1）")


def _build_sandbox(df: pd.DataFrame) -> dict:
    """
    为 exec 提供受限的全局命名空间。

    注意：这只是一层"约束/防护"，不是绝对安全边界。
    白名单 builtins 不含 open()、__import__()、exec()、eval()，
    且去掉了 getattr / hasattr / type 等可用于对象链逃逸的能力；
    配合 check_code_safety 的静态检查拦截下划线属性访问。
    对完全不可信的代码，仍应放入子进程或容器中运行。
    """
    import datetime, re, json
    return {
        "__builtins__": {
            # 白名单 builtins — 不允许 open()、__import__()、exec()、eval()
            "print": print, "len": len, "range": range, "enumerate": enumerate,
            "zip": zip, "map": map, "filter": filter, "list": list, "dict": dict,
            "set": set, "tuple": tuple, "int": int, "float": float, "str": str,
            "bool": bool, "round": round, "abs": abs, "min": min, "max": max,
            "sum": sum, "sorted": sorted, "reversed": reversed,
            "isinstance": isinstance, "repr": repr,
        },
        "df":       df,
        "pd":       pd,
        "np":       np,
        "datetime": datetime,
        "re":       re,
        "json":     json,
    }


def _df_to_markdown(df: pd.DataFrame) -> str:
    """DataFrame → Markdown 表格；无 tabulate 时降级为手工拼表。"""
    try:
        return df.to_markdown(index=False)
    except ImportError:
        cols   = [str(c) for c in df.columns]
        lines  = ["| " + " | ".join(cols) + " |",
                  "| " + " | ".join(["---"] * len(cols)) + " |"]
        for row in df.itertuples(index=False):
            lines.append("| " + " | ".join(str(v) for v in row) + " |")
        return "\n".join(lines)


def _exec_with_timeout(code: str, sandbox: dict, local_vars: dict,
                       timeout: int) -> tuple[bool, str | None]:
    """
    在守护线程中执行 exec，超过 timeout 秒返回 (False, 超时提示)。

    注意：线程超时后无法强制终止，孤儿线程会继续跑完（内部工具可接受；
    对完全不可信的代码仍需子进程/容器隔离）。超时返回后工具会立即
    把错误交还给 LLM，页面不会卡死。
    """
    outcome: dict = {}

    def _runner() -> None:
        try:
            exec(code, sandbox, local_vars)
            outcome["ok"] = True
        except Exception:
            outcome["ok"] = False
            outcome["err"] = "代码执行出错：\n```\n" + \
                traceback.format_exc(limit=4) + "```"

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return False, (
            f"⏱️ 代码执行超过 {timeout} 秒，已终止返回。"
            "请简化计算（例如先 .head() 抽样、避免逐行循环）。"
        )
    if not outcome.get("ok"):
        return False, outcome.get("err", "未知执行错误")
    return True, None


def run_sandboxed(code: str, df: pd.DataFrame,
                  timeout: int | None = None) -> tuple[bool, str | None, dict]:
    """
    chart_tool / excel_tool / analyse_tool 共用的统一执行入口：
    静态安全检查 → 受限沙箱 → 超时 exec。
    返回 (ok, err, local_vars)；ok=False 时 err 为可直接展示的错误文本。
    """
    check_code_safety(code)
    sandbox    = _build_sandbox(df)
    local_vars = {}
    ok, err = _exec_with_timeout(code, sandbox, local_vars,
                                 timeout if timeout is not None else SANDBOX_TIMEOUT)
    return ok, err, local_vars


@tool
def analyse_tool(code: str) -> str:
    """
    执行 Agent 编写的 Pandas 分析代码并返回结果。

    Agent 编写 `code` 时必须遵守的规则：
    1. 将最终输出赋值给名为 `result` 的变量；
       result 可以是 DataFrame、标量数值或字典。
    2. 只能使用 df、pd、np、datetime、re、json。
    3. 时间聚合必须先把 datetime 转为字符串，再 groupby。
       示例：
         tmp = df.copy()
         tmp["月份"] = tmp["交易日期"].dt.strftime("%Y-%m")
         result = tmp.groupby("月份")["金额"].sum().reset_index()
    4. 不要调用 plt、不要打开文件、不要导入新库、不要使用 exec/eval。
    5. 限制：result DataFrame 行数必须 ≤ 500（需要时用 .head()）。

    返回 Markdown 表格（DataFrame）、标量字符串或 JSON；
    同时捕获代码中 print() 的输出。
    """
    df = _df_accessor()
    if df is None:
        return "❌ 没有加载数据集，请先上传文件后再分析。"

    # 捕获标准输出 + 静态检查 + 超时沙箱执行（统一入口，见 run_sandboxed）
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        try:
            ok, err, local_vars = run_sandboxed(code, df)
        except ValueError as e:
            return f"❌ 代码未通过安全检查：{e}"
        printed = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    if not ok:
        return err

    # ── 提取 result ───────────────────────────────────────────────────────
    result = local_vars.get("result")

    parts = []

    if printed.strip():
        parts.append(f"**print 输出：**\n```\n{printed.strip()}\n```")

    if result is None:
        # 降级方案：在局部变量中查找任意 DataFrame
        dfs = [v for k, v in local_vars.items()
               if isinstance(v, pd.DataFrame) and not k.startswith("_")]
        if dfs:
            result = dfs[0]
        else:
            # 可能是局部变量中的标量
            scalars = {k: v for k, v in local_vars.items()
                       if not k.startswith("_") and not callable(v)
                       and not isinstance(v, pd.DataFrame)}
            if scalars:
                parts.append("**计算结果：** " +
                             "  |  ".join(f"{k} = {v}" for k, v in scalars.items()))
            elif not printed.strip():
                return "代码执行完毕，但未找到名为 `result` 的变量。请确保将最终结果赋值给 `result`。"
            return "\n\n".join(parts)

    if isinstance(result, pd.DataFrame):
        if len(result) > MAX_ROWS_RESULT:
            parts.append(f"⚠️ 结果共 {len(result)} 行，已截断显示前 {MAX_ROWS_RESULT} 行。")
            result = result.head(MAX_ROWS_RESULT)
        # 清洗以便展示
        result = result.copy()
        for col in result.columns:
            if pd.api.types.is_datetime64_any_dtype(result[col]):
                result[col] = result[col].dt.strftime("%Y-%m-%d")
        result = result.fillna("")
        parts.append(f"TABLE:\n{_df_to_markdown(result)}")
        parts.append(f"（共 {len(result)} 行 × {len(result.columns)} 列）")

    elif isinstance(result, dict):
        parts.append(f"```json\n{json.dumps(result, ensure_ascii=False, indent=2)}\n```")

    else:
        parts.append(f"**结果：** {result}")

    return "\n\n".join(parts)


def run_analysis_code(code: str) -> str:
    """
    UI「重新执行」入口：与 analyse_tool 共用同一实现和超时沙箱，
    不经过 LangChain 工具包装，供前端直接调用。
    """
    return analyse_tool.func(code)
