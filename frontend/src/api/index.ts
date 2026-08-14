import axios from 'axios'

// axios 实例。baseURL=/api，由 vite proxy 转发到后端 8000。
export const http = axios.create({
  baseURL: '/api',
  timeout: 60000,
})
