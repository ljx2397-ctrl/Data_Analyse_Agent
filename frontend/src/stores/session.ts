import { defineStore } from 'pinia'
import { ElMessage } from 'element-plus'
import { sessionsApi } from '../api/sessions'
import { uploadApi } from '../api/upload'
import { streamChat } from '../api/chat'
import { codeApi } from '../api/code'
import { skillsApi } from '../api/skills'
import { statsApi } from '../api/stats'
import type { SessionOut, Message, DataOverview, TableOverview, DatasetInfo, SkillItem, SessionUsage, PlanStep, ReplanEventData } from '../types'

interface State {
  sessions: SessionOut[]
  currentSid: string | null
  messages: Message[]
  overview: DataOverview | null
  datasets: DatasetInfo[]
  tableOverviews: TableOverview[]
  // 流式状态按 sid 跟踪，支持后台会话继续生成
  streamingSids: Record<string, boolean>
  streamingTextBySid: Record<string, string>
  toolStatusBySid: Record<string, string | null>
  abortControllers: Record<string, AbortController | null>
  // 占位消息在 this.messages 中的索引（仅当该会话为当前会话时有效）
  streamingMsgIdxBySid: Record<string, number>
  // 回复完成但用户未查看该会话 → 列表显示提醒点
  unreadReplies: Record<string, boolean>
  // Skill 列表（全局共享，AppSidebar 管理 / ChatPanel 选择）
  skills: SkillItem[]
  // 当前消息选中的 skill id（单选；为空走关键词命中）
  activeSkillId: string | null
  // 用户选择的推理模式：auto / react / plan
  activeMode: 'auto' | 'react' | 'plan'
  // 会话级 token 累计用量（按 sid）
  usageBySid: Record<string, SessionUsage | null>
}

export const useSessionStore = defineStore('session', {
  state: (): State => ({
    sessions: [],
    currentSid: null,
    messages: [],
    overview: null,
    datasets: [],
    tableOverviews: [],
    streamingSids: {},
    streamingTextBySid: {},
    toolStatusBySid: {},
    abortControllers: {},
    streamingMsgIdxBySid: {},
    unreadReplies: {},
    skills: [],
    activeSkillId: null,
    activeMode: 'react',
    usageBySid: {},
  }),

  getters: {
    currentSession: (s): SessionOut | null =>
      s.sessions.find((x) => x.sid === s.currentSid) || null,
    hasData: (s): boolean => s.overview !== null,
    multiTable: (s): boolean => s.datasets.length > 1,
    // 当前会话是否正在流式（向后兼容）
    streaming: (s): boolean => !!s.streamingSids[s.currentSid || ''],
    streamingText: (s): string => s.streamingTextBySid[s.currentSid || ''] || '',
    toolStatus: (s): string | null => s.toolStatusBySid[s.currentSid || ''] || null,
    // 已启用的 skill（用于对话框选择 chips）
    enabledSkills: (s): SkillItem[] => s.skills.filter((sk) => sk.enabled),
    // 当前会话的 token 累计用量
    currentUsage: (s): SessionUsage | null => s.usageBySid[s.currentSid || ''] || null,
  },

  actions: {
    async loadSessions() {
      this.sessions = await sessionsApi.list()
    },

    async createSession() {
      const s = await sessionsApi.create()
      this.sessions.unshift(s)
      await this.selectSession(s.sid)
      return s
    },

    async selectSession(sid: string) {
      if (this.currentSid !== sid) {
        await sessionsApi.switch(sid)
      }
      this.currentSid = sid
      const detail = await sessionsApi.get(sid)
      this.messages = detail.messages
      // 直接用后端从实际 df 计算的概览（已加载数据时非 null）。
      // 这样刷新/切换历史会话后前端能正确进入 ChatPanel 并显示字段参考。
      this.overview = detail.data_overview
      this.datasets = detail.datasets || []
      this.tableOverviews = detail.table_overviews || []
      // 切回正在后台流式的会话：追加占位消息显示实时进度
      if (this.streamingSids[sid]) {
        this.messages.push({
          role: 'assistant',
          content: this.streamingTextBySid[sid] || '',
          charts: [], xlsxs: [], tables: [], code_exec: [],
        })
        this.streamingMsgIdxBySid[sid] = this.messages.length - 1
      }
      // 查看该会话即清除未读提醒
      if (this.unreadReplies[sid]) {
        delete this.unreadReplies[sid]
      }
      // 加载会话 token 累计用量（非阻塞）
      statsApi.getUsage(sid).then((u) => { this.usageBySid[sid] = u }).catch(() => {})
    },

    async deleteSession(sid: string) {
      // 停止该会话的后台流式（如有）
      if (this.abortControllers[sid]) {
        this.abortControllers[sid]!.abort()
      }
      await sessionsApi.remove(sid)
      this.sessions = this.sessions.filter((s) => s.sid !== sid)
      // 清理该会话的流式状态
      delete this.streamingSids[sid]
      delete this.streamingTextBySid[sid]
      delete this.toolStatusBySid[sid]
      delete this.abortControllers[sid]
      delete this.streamingMsgIdxBySid[sid]
      delete this.unreadReplies[sid]
      delete this.usageBySid[sid]
      if (this.currentSid === sid) {
        if (this.sessions.length > 0) {
          await this.selectSession(this.sessions[0].sid)
        } else {
          // 无剩余会话时不再自动创建空会话，显示空状态页
          this.currentSid = null
          this.messages = []
          this.overview = null
          this.datasets = []
          this.tableOverviews = []
        }
      }
    },

    async uploadData(file: File) {
      if (!this.currentSid) return null
      return uploadApi.uploadData(this.currentSid, file)
    },

    async parseHeader(file: File) {
      if (!this.currentSid) return null
      return uploadApi.parseHeader(this.currentSid, file)
    },

    async confirmData(payload: { sheet?: string | null; columns?: string[] | null }) {
      if (!this.currentSid) return null
      const resp = await uploadApi.confirmData(this.currentSid, payload)
      this.overview = resp.overview
      this.datasets = resp.datasets || []
      this.tableOverviews = resp.table_overviews || []
      // 刷新会话列表（has_data 变化）
      await this.loadSessions()
      return resp
    },

    async removeDataset(dsId: string) {
      if (!this.currentSid) return null
      const resp = await uploadApi.removeDataset(this.currentSid, dsId)
      this.overview = resp.overview
      this.datasets = resp.datasets || []
      this.tableOverviews = resp.table_overviews || []
      return resp
    },

    // 刷新当前会话的会话列表条目（标题/消息数等）
    async refreshCurrent() {
      if (!this.currentSid) return
      await this.loadSessions()
      // 重新加载当前会话详情，刷新 data_overview / df_columns
      // （reload_df 恢复列名后 overview 会变，不刷新前端看不到新列名）
      const detail = await sessionsApi.get(this.currentSid)
      this.overview = detail.data_overview
      this.datasets = detail.datasets || []
      this.tableOverviews = detail.table_overviews || []
    },

    async sendQuery(query: string, skillId?: string | null, mode?: 'auto' | 'react' | 'plan' | null) {
      if (!this.currentSid || !query.trim() || this.streaming) return
      const sid = this.currentSid
      // 优先用参数传入的 skillId；否则用 store 里的 activeSkillId（对话框选择）
      const finalSkillId = skillId !== undefined ? skillId : this.activeSkillId
      // 优先用参数传入的 mode；否则用 store 里的 activeMode
      const finalMode = mode !== undefined && mode !== null ? mode : this.activeMode
      // 消费完即清空，避免下一次输入仍带选中状态（用户需重新选择）
      this.activeSkillId = null

      // 立即追加用户消息
      this.messages.push({
        role: 'user',
        content: query,
        charts: [],
        xlsxs: [],
        tables: [],
        code_exec: [],
      })

      // 占位 assistant 消息：必须通过 this.messages[idx] 修改代理对象，
      // 直接修改原始引用不会触发 Vue 响应式更新（这是流式不刷新的根因）。
      this.messages.push({
        role: 'assistant',
        content: '',
        charts: [],
        xlsxs: [],
        tables: [],
        code_exec: [],
        mode: null,
        plan_steps: [],
        replan_events: [],
      })
      const idx = this.messages.length - 1
      this.streamingMsgIdxBySid[sid] = idx

      this.streamingSids[sid] = true
      this.streamingTextBySid[sid] = ''
      this.toolStatusBySid[sid] = '思考中...'
      this.abortControllers[sid] = new AbortController()

      try {
        await streamChat(sid, query, {
          onToken: (content) => {
            this.toolStatusBySid[sid] = null
            this.streamingTextBySid[sid] = (this.streamingTextBySid[sid] || '') + content
            // 仅当前会话仍在视图时更新 UI（用户可能已切走）
            if (this.currentSid === sid && this.streamingMsgIdxBySid[sid] != null) {
              this.messages[this.streamingMsgIdxBySid[sid]].content = this.streamingTextBySid[sid]
            }
          },
          onTool: (data) => {
            this.toolStatusBySid[sid] = data.message || '正在执行工具...'
          },
          onRoute: (data) => {
            // 路由决策：标记本轮走 react 还是 plan
            if (this.currentSid === sid && this.streamingMsgIdxBySid[sid] != null) {
              this.messages[this.streamingMsgIdxBySid[sid]].mode = data.mode
            }
          },
          onPlan: (data) => {
            // planner 产出计划步骤
            if (this.currentSid === sid && this.streamingMsgIdxBySid[sid] != null) {
              this.messages[this.streamingMsgIdxBySid[sid]].plan_steps = data.steps
            }
          },
          onReplan: (data) => {
            // replanner 决策：replan（重规划剩余步骤）或 finish（准备生成最终回复）
            if (this.currentSid === sid && this.streamingMsgIdxBySid[sid] != null) {
              const i = this.streamingMsgIdxBySid[sid]
              const ev: ReplanEventData = {
                action: data.action as 'replan' | 'finish',
                reason: data.reason,
                new_steps: data.new_steps,
              }
              this.messages[i].replan_events = [
                ...(this.messages[i].replan_events || []),
                ev,
              ]
            }
          },
          onDone: (data) => {
            this.streamingTextBySid[sid] = ''
            // 更新会话累计用量（done 事件携带后端权威值）
            if (data.session_usage) {
              this.usageBySid[sid] = data.session_usage
            }
            if (this.currentSid === sid && this.streamingMsgIdxBySid[sid] != null) {
              const i = this.streamingMsgIdxBySid[sid]
              this.messages[i].content = data.content
              this.messages[i].charts = data.charts
              this.messages[i].xlsxs = data.xlsxs
              this.messages[i].tables = data.tables
              this.messages[i].code_exec = data.code_exec
              this.messages[i].usage = data.usage || null
            } else {
              // 用户已切走 → 标记未读提醒
              this.unreadReplies[sid] = true
            }
          },
          onError: (data) => {
            if (this.currentSid === sid && this.streamingMsgIdxBySid[sid] != null) {
              const i = this.streamingMsgIdxBySid[sid]
              this.messages[i].content =
                (this.messages[i].content || '') + `\n\n⚠️ ${data.message}`
            }
            if (data.hint) {
              ElMessage.warning(data.hint)
            } else {
              ElMessage.error(data.message)
            }
          },
        }, this.abortControllers[sid]!.signal, finalSkillId, finalMode)
      } catch (e: any) {
        // 用户主动中止时 AbortError，不当作错误处理
        if (e?.name === 'AbortError') {
          if (this.currentSid === sid && this.streamingMsgIdxBySid[sid] != null) {
            const i = this.streamingMsgIdxBySid[sid]
            const cur = this.messages[i].content || ''
            this.messages[i].content =
              cur + (cur.endsWith('\n') ? '' : '\n\n') + '⏹️ 已停止生成'
          }
        } else if (this.currentSid === sid && this.streamingMsgIdxBySid[sid] != null) {
          const i = this.streamingMsgIdxBySid[sid]
          this.messages[i].content =
            (this.messages[i].content || '') + `\n\n⚠️ 请求中断：${e?.message || e}`
        }
      } finally {
        this.streamingSids[sid] = false
        delete this.streamingTextBySid[sid]
        this.toolStatusBySid[sid] = null
        delete this.streamingMsgIdxBySid[sid]
        delete this.abortControllers[sid]
        await this.refreshCurrent()
      }
    },

    stopStreaming() {
      const sid = this.currentSid
      if (sid && this.abortControllers[sid]) {
        this.abortControllers[sid]!.abort()
      }
    },

    async runCode(code: string): Promise<string> {
      if (!this.currentSid) return ''
      return codeApi.run(this.currentSid, code)
    },

    // ── Skill ──────────────────────────────────────────────────
    async loadSkills() {
      try {
        this.skills = await skillsApi.list()
      } catch {
        // 后端未就绪时静默
      }
    },

    setActiveSkill(sid: string | null) {
      // 单选切换：再次点击同一个 → 取消选择
      this.activeSkillId = this.activeSkillId === sid ? null : sid
    },

    setActiveMode(mode: 'auto' | 'react' | 'plan') {
      this.activeMode = mode
    },

    // ── 单条消息管理（复制/修改/删除）─────────────────────────────
    async deleteMessage(index: number) {
      if (!this.currentSid || this.streaming) return
      const sid = this.currentSid
      try {
        await sessionsApi.deleteMessage(sid, index)
        // 前端同步删除（splice 直接改代理数组，触发响应式）
        this.messages.splice(index, 1)
        await this.refreshCurrent()
        ElMessage.success('已删除')
      } catch (e: any) {
        ElMessage.error(`删除失败：${e?.message || e}`)
      }
    },

    async resendFromEdit(index: number, text: string) {
      if (!this.currentSid || this.streaming || !text.trim()) return
      const sid = this.currentSid
      // 截断到 index：删除该 user 消息及之后所有（后端 + 前端同步）
      try {
        await sessionsApi.truncateMessages(sid, index)
        this.messages = this.messages.slice(0, index)
      } catch (e: any) {
        ElMessage.error(`编辑重发失败：${e?.message || e}`)
        return
      }
      // 重新发送：sendQuery 会 push user + 占位 assistant，后端 stream_chat 会 append user
      // 模式/技能沿用 store 当前选择（与普通发送一致）
      await this.sendQuery(text)
    },
  },
})
