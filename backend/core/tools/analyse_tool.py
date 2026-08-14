# backend/core/tools/analyse_tool.py
# 复用原 analyse_tool.py，import 路径改为 core 包。
# 执行 LLM 生成的 Pandas 代码：静态安全检查 → 受限沙箱 → 超时 exec。
import io
import sys
import json
import traceback
import threading
import pandas as pd
import numpy as np

from langchain.tools import tool
from core.config import MAX_ROWS_RESULT, SANDBOX_TIMEOUT

_df_accessor = None
_dfs_accessor = None  # 返回 {表名: DataFrame} 字典，用于多表 join
_reload_fn = None
_sid_getter = None    # 返回当前会话 sid，用于缓存 last_result

def set_df_accessor(fn):
    global _df_accessor
    _df_accessor = fn

def set_dfs_accessor(fn):
    """注入多表访问器：返回 {表名: DataFrame} 字典。"""
    global _dfs_accessor
    _dfs_accessor = fn

def set_reload_fn(fn):
    """注入 reload_df 函数：用指定列名重新加载原始数据（供 Agent 恢复字段名）。"""
    global _reload_fn
    _reload_fn = fn

def set_sid_getter(fn):
    """注入 sid 访问器：返回当前会话 sid（用于缓存 last_result 供后续工具复用）。"""
    global _sid_getter
    _sid_getter = fn


_FORBIDDEN_CALLS = {
    "eval", "exec", "compile", "open", "input", "breakpoint",
    "globals", "locals", "vars", "getattr", "setattr", "delattr",
    "__import__", "memoryview",
}


def check_code_safety(code: str) -> None:
    """静态扫描拦截明显危险写法（import / 下划线属性 / 越权调用 / 无界 while）。"""
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
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in _FORBIDDEN_CALLS):
            raise ValueError(f"禁止调用函数：{node.func.id!r}")
        if (isinstance(node, ast.While)
                and isinstance(node.test, ast.Constant)
                and bool(node.test.value)):
            raise ValueError("禁止无界 while 循环（如 while True / while 1）")


def _build_sandbox(df: pd.DataFrame, dfs: dict | None = None) -> dict:
    """受限全局命名空间：白名单 builtins + df/pd/np/datetime/re/json + dfs（多表）。

    __import__ 必须包含在内：pandas/numpy 内部机制（如序列化 numpy 类型、
    延迟加载子模块）会触发 Python 导航到 __builtins__['__import__']。
    缺少它会导致 KeyError: '__import__'。
    安全性由 check_code_safety 的 AST 检查保障：
      - ast.Import / ast.ImportFrom → 禁止（拦截 import 语句）
      - ast.Call(func=Name('__import__')) → 禁止（拦截直接调用）
    因此用户代码无法通过 __import__ 引入新模块。
    """
    import datetime, re, json, builtins
    return {
        "__builtins__": {
            "print": print, "len": len, "range": range, "enumerate": enumerate,
            "zip": zip, "map": map, "filter": filter, "list": list, "dict": dict,
            "set": set, "tuple": tuple, "int": int, "float": float, "str": str,
            "bool": bool, "round": round, "abs": abs, "min": min, "max": max,
            "sum": sum, "sorted": sorted, "reversed": reversed,
            "isinstance": isinstance, "repr": repr,
            # __import__ 供 Python 内部导入机制使用（pandas/numpy 延迟加载），
            # 用户代码无法直接调用（AST 检查已拦截）
            "__import__": builtins.__import__,
            # 异常基类：用户代码 try/except 需要
            "Exception": Exception, "ValueError": ValueError,
            "KeyError": KeyError, "TypeError": TypeError,
            "IndexError": IndexError, "AttributeError": AttributeError,
            "ZeroDivisionError": ZeroDivisionError,
            "StopIteration": StopIteration,
            # type/hasattr：pandas 内部及用户代码常用
            "type": type, "hasattr": hasattr,
        },
        "df":       df,
        "dfs":      dfs or {},
        "pd":       pd,
        "np":       np,
        "datetime": datetime,
        "re":       re,
        "json":     json,
        "reload_df": _reload_fn or (lambda columns: "重新加载功能未配置"),
    }


def _df_to_markdown(df: pd.DataFrame) -> str:
    try:
        # floatfmt=",.2f" 让浮点数显示千分位 + 2 位小数，禁用科学计数法
        return df.to_markdown(index=False, floatfmt=",.2f")
    except ImportError:
        cols   = [str(c) for c in df.columns]
        lines  = ["| " + " | ".join(cols) + " |",
                  "| " + " | ".join(["---"] * len(cols)) + " |"]
        for row in df.itertuples(index=False):
            lines.append("| " + " | ".join(str(v) for v in row) + " |")
        return "\n".join(lines)


def _jsonable(obj):
    """把 dict/list 里的 pandas/numpy 对象转为 JSON 可序列化的原生类型。
    避免 LLM 写 result = {"排名": df.groupby(...).reset_index()} 时
    json.dumps 报 "Object of type DataFrame is not JSON serializable"。
    """
    if isinstance(obj, pd.DataFrame):
        return obj.to_dict(orient="records")
    if isinstance(obj, pd.Series):
        return obj.to_dict()
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, tuple):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


def _exec_with_timeout(code: str, sandbox: dict, local_vars: dict,
                       timeout: int) -> tuple[bool, str | None]:
    """守护线程中 exec，超时返回 (False, 提示)。"""
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
        return False, (f"⏱️ 代码执行超过 {timeout} 秒，已终止返回。"
                       "请简化计算（例如先 .head() 抽样、避免逐行循环）。")
    if not outcome.get("ok"):
        return False, outcome.get("err", "未知执行错误")
    return True, None


def run_sandboxed(code: str, df: pd.DataFrame,
                  dfs: dict | None = None,
                  timeout: int | None = None) -> tuple[bool, str | None, dict]:
    """统一执行入口：静态检查 → 受限沙箱 → 超时 exec。返回 (ok, err, local_vars)。
    dfs 为多表字典 {表名: DataFrame}，在沙箱中可通过 dfs["表名"] 访问。
    """
    check_code_safety(code)
    sandbox    = _build_sandbox(df, dfs)
    local_vars = {}
    ok, err = _exec_with_timeout(code, sandbox, local_vars,
                                 timeout if timeout is not None else SANDBOX_TIMEOUT)
    return ok, err, local_vars


@tool
def analyse_tool(code: str, result_name: str = "") -> str:
    """
    执行 Agent 编写的 Pandas 分析代码并返回结果。
    规则：最终结果赋值给 result（DataFrame/标量/字典）；只用 df/pd/np/datetime/re/json。
    返回 Markdown 表格（DataFrame）、标量字符串或 JSON；同时捕获 print() 输出。

    result_name（必填）：为本次结果起个简短名字（如 "支行排名"、"aum分层"）。
    后续 excel_tool / chart_tool 通过相同 result_name 即可复用完整 result，无需重跑代码。
    不命名会导致后续导出/画图时被迫重新计算，浪费 token。
    """
    df = _df_accessor()
    if df is None:
        return "❌ 没有加载数据集，请先上传文件后再分析。"

    dfs = {}
    if _dfs_accessor is not None:
        try:
            dfs = _dfs_accessor() or {}
        except Exception:
            dfs = {}

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        try:
            ok, err, local_vars = run_sandboxed(code, df, dfs)
        except ValueError as e:
            return f"❌ 代码未通过安全检查：{e}"
        printed = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    if not ok:
        return err

    result = local_vars.get("result")
    parts = []
    if printed.strip():
        parts.append(f"**print 输出：**\n```\n{printed.strip()}\n```")

    if result is None:
        dfs = [v for k, v in local_vars.items()
               if isinstance(v, pd.DataFrame) and not k.startswith("_")]
        if dfs:
            result = dfs[0]
        else:
            scalars = {k: v for k, v in local_vars.items()
                       if not k.startswith("_") and not callable(v)
                       and not isinstance(v, pd.DataFrame)}
            if scalars:
                parts.append("**计算结果：** " +
                             "  |  ".join(f"{k} = {v}" for k, v in scalars.items()))
            elif not printed.strip():
                return "代码执行完毕，但未找到名为 `result` 的变量。请确保将最终结果赋值给 `result`。"
            return "\n\n".join(parts)

    # 缓存完整 result（截断前）供 excel_tool / chart_tool 复用，避免重复计算
    if _sid_getter is not None:
        try:
            from services import df_registry
            sid = _sid_getter()
            df_registry.set_last_result(sid, result)
            # 命名缓存：LLM 指定 result_name 时存入命名寄存器，供多 Sheet 场景按名取用
            name = (result_name or "").strip()
            if name:
                df_registry.set_named_result(sid, name, result)
        except Exception:
            pass

    if isinstance(result, pd.DataFrame):
        # 先处理类型转换
        result = result.copy()
        for col in result.columns:
            if isinstance(result[col].dtype, pd.CategoricalDtype):
                result[col] = result[col].astype(str)
            elif pd.api.types.is_datetime64_any_dtype(result[col]):
                result[col] = result[col].dt.strftime("%Y-%m-%d")
        # 截断
        if len(result) > MAX_ROWS_RESULT:
            parts.append(f"⚠️ 结果共 {len(result)} 行，已截断显示前 {MAX_ROWS_RESULT} 行。")
            result = result.head(MAX_ROWS_RESULT)
        result = result.fillna("")
        parts.append(f"TABLE:\n{_df_to_markdown(result)}")
        parts.append(f"（共 {len(result)} 行 × {len(result.columns)} 列） [v2]")
    elif isinstance(result, dict):
        try:
            parts.append(f"```json\n{json.dumps(_jsonable(result), ensure_ascii=False, indent=2)}\n```")
        except Exception:
            parts.append(f"**结果：** {result}")
    else:
        parts.append(f"**结果：** {result}")

    return "\n\n".join(parts)


def run_analysis_code(code: str) -> str:
    """UI「重新执行」入口：不经过 LangChain 包装，直接调用底层实现。"""
    return analyse_tool.func(code)
