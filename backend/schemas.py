# backend/schemas.py
# Pydantic 请求/响应模型。
from pydantic import BaseModel


class DatasetInfo(BaseModel):
    id: str
    name: str
    kind: str = "csv"
    columns: list[str] | None = None
    sheet: str | None = None


class SessionOut(BaseModel):
    sid: str
    title: str
    df_name: str | None = None
    df_kind: str | None = None
    df_columns: list[str] | None = None
    df_sheet: str | None = None
    has_data: bool = False
    datasets: list[DatasetInfo] = []
    created_at: str
    message_count: int = 0


class DataOverview(BaseModel):
    rows: int
    cols: int
    columns: list[str]
    num_cols: list[str]
    cat_cols: list[str]
    date_cols: list[str]


class TableOverview(DataOverview):
    """单张表的数据概览，附带表名。"""
    table_name: str = ""
    dataset_id: str = ""


class SessionDetail(BaseModel):
    sid: str
    title: str
    messages: list[dict]
    df_name: str | None = None
    df_kind: str | None = None
    df_columns: list[str] | None = None
    df_sheet: str | None = None
    has_data: bool = False
    datasets: list[DatasetInfo] = []
    # 切换/刷新会话时由后端从实际 df 计算，确保前端正确显示 ChatPanel 与字段参考
    data_overview: DataOverview | None = None
    # 多表概览（每张表一个），单表时只含 1 条
    table_overviews: list[TableOverview] = []
    created_at: str


class UploadDataResponse(BaseModel):
    kind: str
    name: str
    sheets: list[str] | None = None


class ParseHeaderResponse(BaseModel):
    columns: list[str]


class ConfirmDataRequest(BaseModel):
    sheet: str | None = None
    columns: list[str] | None = None


class ConfirmDataResponse(BaseModel):
    """确认导入后的响应：包含主表概览 + 所有数据集列表。"""
    overview: DataOverview
    datasets: list[DatasetInfo] = []
    table_overviews: list[TableOverview] = []


class ChatRequest(BaseModel):
    query: str
    skill_id: str | None = None   # 前端在对话框显式选择的 Skill；为空走关键词命中
    mode: str | None = None       # 用户显式选择的模式："auto"/"react"/"plan"；None=auto


class CodeRunRequest(BaseModel):
    code: str


# ── Skill 管理 ─────────────────────────────────────────────────────────────────

class SkillItem(BaseModel):
    """Skill 配置（响应）。"""
    id: str
    name: str
    description: str = ""
    keywords: list[str] = []
    runner: str = ""
    enabled: bool = True
    builtin: bool = False


class SkillSaveRequest(BaseModel):
    """新增/更新 Skill 请求。
    id 为空时新增（自定义 skill）；非空时更新。
    内置 skill 不允许改 runner / builtin，但可改 keywords/enabled/name/description。
    """
    id: str | None = None
    name: str = ""
    description: str = ""
    keywords: list[str] = []
    enabled: bool = True
    runner: str = ""


class CodeRunResponse(BaseModel):
    result: str


# ── 业务知识库 ────────────────────────────────────────────────────────────────

class KnowledgeItem(BaseModel):
    id: str
    title: str
    content: str
    category: str = ""
    tags: list[str] = []
    created_at: str = ""
    score: float | None = None   # 仅检索结果返回


class KnowledgeCreate(BaseModel):
    title: str
    content: str
    category: str = ""
    tags: list[str] = []


class KnowledgeUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    category: str | None = None
    tags: list[str] | None = None


class KnowledgeSearchRequest(BaseModel):
    query: str
    top_k: int | None = None
    category: str | None = None


# ── LLM 运行时配置 ───────────────────────────────────────────────────────────

class LLMConfigOut(BaseModel):
    """LLM 配置响应：api_key 脱敏。"""
    model: str
    api_base: str
    api_key_masked: str = ""
    temperature: float = 0


class LLMConfigUpdate(BaseModel):
    """LLM 配置更新请求：所有字段可选，仅传需要修改的。"""
    model: str | None = None
    api_base: str | None = None
    api_key: str | None = None
    temperature: float | None = None


class LLMProfile(BaseModel):
    """单个模型配置档案（api_key 脱敏）。"""
    id: str
    name: str = ""
    model: str
    api_base: str
    api_key_masked: str = ""
    temperature: float = 0
    active: bool = False


class LLMProfileSaveRequest(BaseModel):
    """新增/更新配置档案请求。"""
    id: str | None = None   # 为空时新增
    name: str = ""
    model: str = ""
    api_base: str = ""
    api_key: str | None = None   # None=不修改，非空=更新
    temperature: float = 0
    activate: bool = True


# ── API 数据源配置 ──────────────────────────────────────────────────────────

class ApiSourceProfile(BaseModel):
    """单个 API 数据源配置档案（auth_token 脱敏）。"""
    id: str
    name: str = ""            # 显示名称（可选）
    url: str                  # API URL
    auth_type: str = "none"   # none / bearer / api_key_header
    auth_token_masked: str = ""
    last_connected_at: str = ""   # 最近一次连接时间（ISO 字符串），未连接过为空
    active: bool = False


class ApiSourceSaveRequest(BaseModel):
    """新增/更新 API 数据源配置请求。"""
    id: str | None = None        # 为空时新增
    name: str = ""
    url: str = ""
    auth_type: str = "none"
    auth_token: str | None = None   # None=不修改，非空=更新
    activate: bool = True


class ApiPreviewResult(BaseModel):
    """预览接口返回：字段 + 行数 + 前 5 行样本 Markdown。"""
    columns: list[str]
    rows: int
    cols: int
    preview_md: str


class ApiFetchResult(BaseModel):
    """拉取 API 数据注册到会话后的返回结果。"""
    sid: str
    ds_id: str
    name: str
    rows: int
    cols: int
    columns: list[str]
