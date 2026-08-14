# backend/core/config.py
# 给 core 模块（tools / data_loader / agent）使用的统一配置入口。
# 从 backend.settings 导入，并完成 matplotlib 中文字体初始化（chart_tool 依赖）。
import matplotlib
matplotlib.use("Agg")   # 无界面后端，必须在使用 pyplot 前设置
import matplotlib.pyplot as plt

# 中文字体支持
plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# 从 settings 重新导出，core 内部统一用 `from core.config import X`
from settings import (   # noqa: E402
    CHART_COLORS,
    CHARTS_DIR,
    OUTPUT_DIR,
    STORAGE_DIR,
    DATA_DIR,
    BASE_DIR,
    LLM_MODEL,
    LLM_API_BASE,
    LLM_API_KEY_ENV,
    LLM_TEMPERATURE,
    LLM_STREAMING,
    QUALITY_NULL_WARN,
    QUALITY_NULL_BAD,
    QUALITY_IQR_MULT,
    QUALITY_EXCEL_MAX_ROWS,
    QUALITY_MONEY_KEYWORDS,
    MAX_HISTORY_TURNS,
    MAX_ROWS_PREVIEW,
    MAX_ROWS_RESULT,
    SANDBOX_TIMEOUT,
    SUMMARY_TRIGGER_TURNS,
    SUMMARY_KEEP_TURNS,
    KNOWLEDGE_DIR,
    EMBEDDING_MODEL,
    KNOWLEDGE_TOP_K,
)
