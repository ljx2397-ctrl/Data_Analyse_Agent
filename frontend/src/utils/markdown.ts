import MarkdownIt from 'markdown-it'

// 共享的 markdown-it 实例。
// 关闭 html（防 XSS），开启 linkify 和 breaks（单换行→<br>）。
const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})

export function renderMd(text: string): string {
  if (!text) return ''
  return md.render(text)
}
