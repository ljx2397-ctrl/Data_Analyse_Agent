# backend/settings.py
# 全局配置：路径、Redis、对象存储、LLM、运行时限制。
# 所有其他模块从这里读取配置，不在各自文件里硬编码。
import os
import sys

# ── 目录结构 ────────────────────────────────────────────────────────────────
# PyInstaller 打包后：代码在 _MEIPASS 临时解压目录，但用户数据（storage/）
# 必须写到 exe 旁边以持久化跨次运行。源码运行时：BASE_DIR = backend/ 所在目录。
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)        # exe 所在目录
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # backend/
BACKEND_DIR  = BASE_DIR
PROJECT_DIR  = os.path.dirname(BASE_DIR) if not getattr(sys, "frozen", False) else BASE_DIR

# 本地对象存储根目录（LocalStorageBackend 使用）
STORAGE_DIR  = os.path.join(BASE_DIR, "storage")
CHARTS_DIR   = os.path.join(STORAGE_DIR, "charts")    # chart_tool 写入
OUTPUT_DIR   = os.path.join(STORAGE_DIR, "exports")   # excel_tool / quality 写入
DATA_DIR     = os.path.join(STORAGE_DIR, "sessions")  # 每会话原始数据字节
META_DIR     = os.path.join(STORAGE_DIR, "meta")      # 会话元数据持久化（文件存储模式）
KNOWLEDGE_DIR = os.path.join(STORAGE_DIR, "knowledge")  # 业务知识库持久化目录
TRACE_DIR    = os.path.join(META_DIR, "traces")       # 运行轨迹 JSONL（每会话一个文件）

# 启动时自动创建目录
for _d in (STORAGE_DIR, CHARTS_DIR, OUTPUT_DIR, DATA_DIR, META_DIR, KNOWLEDGE_DIR, TRACE_DIR):
    os.makedirs(_d, exist_ok=True)

# ── Redis 配置 ──────────────────────────────────────────────────────────────
# 未配置 REDIS_URL 时自动降级为本地文件持久化（META_DIR，重启不丢失）
REDIS_URL    = os.environ.get("REDIS_URL", "")
REDIS_PREFIX = "da_agent:"   # 所有 key 前缀，避免与其他项目冲突

# ── 对象存储 ────────────────────────────────────────────────────────────────
# storage backend 类型：local（默认） | minio（预留）
STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")

# ── CORS（前端地址）──────────────────────────────────────────────────────────
CORS_ORIGINS = [
    "http://localhost:5173",   # vite dev server
    "http://127.0.0.1:5173",
    "http://localhost:4173",   # vite preview
    "http://127.0.0.1:4173",
]

# ── 图表配色 ─────────────────────────────────────────────────────────────────
CHART_COLORS = [
    "#2E75B6", "#ED7D31", "#A9D18E",
    "#FFC000", "#5B9BD5", "#70AD47",
    "#FF6B6B", "#9B59B6", "#1ABC9C",
]

# ── LLM 配置 ─────────────────────────────────────────────────────────────────
LLM_MODEL       = "qwen3.7-flash-2026-07-15"
LLM_API_BASE    = "https://dashscope.aliyuncs.com/compatible-mode/v1"
LLM_API_KEY_ENV = "DASHSCOPE_API_KEY"   # 存放 API Key 的环境变量名
LLM_TEMPERATURE = 0
LLM_STREAMING   = True

# ── 数据质量检测阈值 ──────────────────────────────────────────────────────────
QUALITY_NULL_WARN      = 0.05
QUALITY_NULL_BAD       = 0.30
QUALITY_IQR_MULT       = 1.5
QUALITY_EXCEL_MAX_ROWS = 2000
QUALITY_MONEY_KEYWORDS = ("金额", "价格", "费用", "成本", "余额",
                          "aum", "amount", "amt", "price", "money")

# ── 运行时限制 ────────────────────────────────────────────────────────────────
MAX_HISTORY_TURNS = 8    # 发给 agent 的最大历史轮数（原文保留）
MAX_ROWS_PREVIEW  = 5    # schema_tool 中每列展示的样本行数
MAX_ROWS_RESULT   = 50 # analyse_tool 结果截断行数
SANDBOX_TIMEOUT   = 30   # 工具单次代码执行超时（秒）

# ── 上下文压缩（滚动摘要）─────────────────────────────────────────────────────
# 消息数超过此阈值时触发摘要：老消息压缩成一条 SystemMessage，保留最近 N 轮原文。
# 阈值 = SUMMARY_TRIGGER_TURNS * 2（每轮含 user + assistant 各一条）
SUMMARY_TRIGGER_TURNS = 6   # 累计超过 6 轮（12 条消息）时触发摘要
SUMMARY_KEEP_TURNS    = 4   # 摘要后保留最近 4 轮（8 条消息）原文

# ── 业务知识库（向量检索）─────────────────────────────────────────────────────
# Chroma 本地持久化，embedding 复用 DashScope（兼容 OpenAI 接口，复用 DASHSCOPE_API_KEY）
EMBEDDING_MODEL  = "text-embedding-v4"   # DashScope 中文 embedding 模型
KNOWLEDGE_TOP_K  = 3                    # Agent 检索 / 前端预览检索返回条数
# KNOWLEDGE_COLLECTION = "business_knowledge"  # Chroma collection 名称
