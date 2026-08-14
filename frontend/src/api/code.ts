import { http } from './index'

export const codeApi = {
  run: (sid: string, code: string) =>
    http
      .post<{ result: string }>(`/sessions/${sid}/code/run`, { code })
      .then((r) => r.data.result),
}
