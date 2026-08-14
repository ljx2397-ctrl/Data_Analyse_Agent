// 数据分析 Agent 前端类型定义

export interface DatasetInfo {
  id: string
  name: string
  kind: string
  columns: string[] | null
  sheet: string | null
}

export interface SessionOut {
  sid: string
  title: string
  df_name: string | null
  df_kind: string | null
  df_columns: string[] | null
  df_sheet: string | null
  has_data: boolean
  datasets: DatasetInfo[]
  created_at: string
  message_count: number
}

// 单轮对话的 token 用量（随 assistant 消息持久化）
export interface TokenUsage {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  model: string
  estimated: boolean        // true = 服务商未返回 usage，为估算值
  duration_ms?: number | null
}

// 会话级累计用量（GET /sessions/{sid}/usage）
export interface SessionUsage {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  turns: number
  estimated: boolean
}

// 运行轨迹事件（GET /sessions/{sid}/trace）
export interface TraceEvent {
  ts: string
  type: string              // turn_start / tool / skill / retry / summary / turn_end / error
  [key: string]: any
}

export interface Message {
  role: 'user' | 'assistant'
  content: string
  charts: string[]
  xlsxs: string[]
  tables: string[]
  code_exec: string[]
  usage?: TokenUsage | null
  // Plan-Execute-Replan 相关（仅 plan 模式产出的 assistant 消息有值）
  mode?: 'react' | 'plan' | null      // 本轮走的路由模式
  plan_steps?: PlanStep[]              // 计划步骤（route=plan 时由 planner 生成）
  replan_events?: ReplanEventData[]    // 重规划事件记录
}

// Plan-Execute-Replan 的单个步骤
export interface PlanStep {
  tool: string
  intent: string
  result_name: string
}

// 路由决策事件
export interface RouteData {
  mode: 'react' | 'plan'
  query: string
}

// 计划事件
export interface PlanData {
  steps: PlanStep[]
}

// 重规划事件
export interface ReplanEventData {
  action: 'replan' | 'finish'
  reason: string
  new_steps: PlanStep[]
}

// 用于 SSE 流式过程中的 replan 事件（与 ReplanEventData 一致，但 action 可能含 continue）
export interface ReplanData {
  action: string
  reason: string
  new_steps: PlanStep[]
}

export interface SessionDetail extends SessionOut {
  messages: Message[]
  // 后端从实际 df 计算的概览，刷新/切换会话时用于恢复 ChatPanel 与字段参考
  data_overview: DataOverview | null
  // 多表概览（每张表一个），单表时只含 1 条
  table_overviews: TableOverview[]
}

export interface DataOverview {
  rows: number
  cols: number
  columns: string[]
  num_cols: string[]
  cat_cols: string[]
  date_cols: string[]
}

export interface TableOverview extends DataOverview {
  table_name: string
  dataset_id: string
}

// SSE 事件 data 载荷
export interface TokenData {
  content: string
}

export interface ToolData {
  node: string
  message?: string
}

export interface ChatDoneData {
  content: string
  charts: string[]
  xlsxs: string[]
  tables: string[]
  code_exec: string[]
  usage?: TokenUsage | null         // 本轮 token 用量
  session_usage?: SessionUsage | null  // 会话累计用量
}

export interface ErrorData {
  message: string
  hint?: string
}

// 业务知识库
export interface KnowledgeItem {
  id: string
  title: string
  content: string
  category: string
  tags: string[]
  created_at: string
  score?: number | null  // 仅检索结果返回
}

export interface KnowledgePayload {
  title: string
  content: string
  category?: string
  tags?: string[]
}

// LLM 运行时配置
export interface LLMConfig {
  model: string
  api_base: string
  api_key_masked: string
  temperature: number
}

export interface LLMConfigUpdate {
  model?: string
  api_base?: string
  api_key?: string
  temperature?: number
}

// 模型配置档案（多份已保存配置）
export interface LLMProfile {
  id: string
  name: string
  model: string
  api_base: string
  api_key_masked: string
  temperature: number
  active: boolean
}

export interface LLMProfileSavePayload {
  id?: string | null
  name: string
  model: string
  api_base: string
  api_key?: string | null
  temperature: number
  activate: boolean
}

// API 数据源配置档案（精简）
export interface ApiSourceProfile {
  id: string
  name: string                 // 显示名称（可选）
  url: string
  auth_type: string            // none / bearer / api_key_header
  auth_token_masked: string
  last_connected_at: string    // 最近连接时间 ISO，未连接过为空
  active: boolean
}

export interface ApiSourceSavePayload {
  id?: string | null
  name: string
  url: string
  auth_type: string
  auth_token?: string | null
  activate: boolean
}

export interface ApiPreviewResult {
  columns: string[]
  rows: number
  cols: number
  preview_md: string
}

export interface ApiFetchResult {
  sid: string
  ds_id: string
  name: string
  rows: number
  cols: number
  columns: string[]
}

// Skill 管理（Layer 3：确定性流程编排，不走 LLM）
export interface SkillItem {
  id: string
  name: string
  description: string
  keywords: string[]
  runner: string
  enabled: boolean
  builtin: boolean
}

export interface SkillSavePayload {
  id?: string | null
  name: string
  description: string
  keywords: string[]
  enabled: boolean
  runner?: string
}
