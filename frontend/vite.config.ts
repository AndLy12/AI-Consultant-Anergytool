import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Настройка сборки и dev-прокси.
 *
 * Почему прокси обязателен: backend принимает изменяющие запросы только со своего
 * Origin (переменная APP_ORIGIN, по умолчанию http://localhost:8001). Dev-сервер Vite
 * работает на другом порту, поэтому браузер прислал бы Origin http://localhost:5173
 * и получил 403. Прокси подменяет заголовок Origin на адрес backend — это повторяет
 * боевую схему, где собранный frontend раздаётся с того же origin, что и API.
 * Менять backend ради разработки не требуется.
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const target = env.VITE_API_TARGET || 'http://localhost:8001'
  const origin = env.VITE_API_ORIGIN || target

  // changeOrigin не трогаем: cookie сессии выставлена на localhost и ходит по порту
  // dev-сервера. Подменяем только заголовок Origin, который проверяет backend.
  const proxyRule = {
    target,
    changeOrigin: false,
    headers: { origin },
  }

  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        '/api': proxyRule,
        '/health': proxyRule,
      },
    },
    build: {
      // Сборка кладётся в frontend/dist; backend раздаёт её с того же origin.
      outDir: 'dist',
      sourcemap: false,
    },
  }
})
