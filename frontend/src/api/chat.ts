import type { ToolData, ChatDoneData, ErrorData, RouteData, PlanData, ReplanData } from '../types'

export interface ChatHandlers {
  onToken: (content: string) => void
  onTool?: (data: ToolData) => void
  onRoute?: (data: RouteData) => void
  onPlan?: (data: PlanData) => void
  onReplan?: (data: ReplanData) => void
  onDone: (data: ChatDoneData) => void
  onError: (data: ErrorData) => void
}

// 用 fetch + ReadableStream 读取 SSE（EventSource 不支持 POST + body）。
export async function streamChat(
  sid: string,
  query: string,
  handlers: ChatHandlers,
  signal?: AbortSignal,
  skillId?: string | null,
  mode?: string | null,
) {
  const resp = await fetch(`/api/sessions/${sid}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, skill_id: skillId || null, mode: mode || null }),
    signal,
  })

  if (!resp.ok || !resp.body) {
    handlers.onError({ message: `请求失败：HTTP ${resp.status}` })
    return
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // SSE 事件以空行（\n\n）分隔
    let sep: number
    while ((sep = buffer.indexOf('\n\n')) >= 0) {
      const raw = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      const evt = parseSse(raw)
      if (!evt) continue
      switch (evt.event) {
        case 'token':
          handlers.onToken(evt.data.content)
          break
        case 'tool':
          handlers.onTool?.(evt.data)
          break
        case 'route':
          handlers.onRoute?.(evt.data)
          break
        case 'plan':
          handlers.onPlan?.(evt.data)
          break
        case 'replan':
          handlers.onReplan?.(evt.data)
          break
        case 'done':
          handlers.onDone(evt.data)
          break
        case 'error':
          handlers.onError(evt.data)
          break
      }
    }
  }
}

function parseSse(raw: string): { event: string; data: any } | null {
  let event = 'message'
  let dataStr = ''
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) {
      event = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataStr += line.slice(5).trim()
    }
  }
  if (!dataStr) return null
  try {
    return { event, data: JSON.parse(dataStr) }
  } catch {
    return null
  }
}
