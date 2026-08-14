import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
// Vite 配置：dev server 代理 /api 到后端 8000，避免跨域。
export default defineConfig({
    plugins: [vue()],
    server: {
        host: '127.0.0.1',
        port: 5173,
        proxy: {
            '/api': {
                target: 'http://127.0.0.1:8000',
                changeOrigin: true,
            },
        },
    },
});
