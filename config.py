# config.py
# 全局常量：路径、配色、LLM 配置。
# 所有其他模块从这里读取配置，不在各自文件里硬编码。
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# 中文字体支持
matplotlib.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

# ── 目录结构 ────────────────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
CHARTS_DIR   = os.path.join(BASE_DIR, "charts")
OUTPUT_DIR   = os.path.join(BASE_DIR, "output")
HISTORY_DIR  = os.path.join(BASE_DIR, "history")
SESSIONS_DIR = os.path.join(HISTORY_DIR, "sessions")   # 每个会话一个 JSON 文件
HISTORY_FILE = os.path.join(HISTORY_DIR, "chat_sessions.json")   # 旧版单文件，首次加载时自动迁移
HISTORY_FILE_LEGACY = os.path.join(HISTORY_DIR, "chat_sessions.pkl")  # 旧版 pickle，首次加载时自动迁移

# 启动时自动创建目录
for _d in (CHARTS_DIR, OUTPUT_DIR, HISTORY_DIR, SESSIONS_DIR):
    os.makedirs(_d, exist_ok=True)

# ── 图表配色 ─────────────────────────────────────────────────────────────────
CHART_COLORS = [
    "#2E75B6", "#ED7D31", "#A9D18E",
    "#FFC000", "#5B9BD5", "#70AD47",
    "#FF6B6B", "#9B59B6", "#1ABC9C",
]

# ── LLM 配置（根据需求改）────────────────────────────────
LLM_MODEL       =  "qwen3.7-max-preview"
LLM_API_BASE    = "https://dashscope.aliyuncs.com/compatible-mode/v1"
LLM_API_KEY_ENV = "DASHSCOPE_API_KEY"   # 存放 API Key 的环境变量名
LLM_TEMPERATURE = 0
LLM_STREAMING   = True                  # ← 原来缺失，agent.py 会 ImportError

# ── 数据质量检测阈值 ──────────────────────────────────────────────────────────
QUALITY_NULL_WARN      = 0.05   # 空值率 ≥ 5% 提示"偏高"
QUALITY_NULL_BAD       = 0.30   # 空值率 ≥ 30% 判定"严重"
QUALITY_IQR_MULT       = 1.5    # IQR 离群值倍数
QUALITY_CLEAN_PREVIEW  = 10     # 清洗预览展示行数
QUALITY_MONEY_KEYWORDS = ("金额", "价格", "费用", "成本", "余额",
                          "aum", "amount", "amt", "price", "money")

# ── 运行时限制 ────────────────────────────────────────────────────────────────
MAX_HISTORY_TURNS = 4    # 发给 agent 的最大历史轮数，控制 token 消耗
MAX_ROWS_PREVIEW  = 5    # schema_tool 中每列展示的样本行数
MAX_ROWS_RESULT   = 20 # analyse_tool 结果截断行数
SANDBOX_TIMEOUT   = 30   # analyse/chart/excel 工具单次代码执行超时（秒），防止死循环卡死页面
