import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The client always talks to `/api` and `/ws`, in both setups. Under
// `docker compose up` nginx serves the built client and proxies those two prefixes
// to the `backend` container; in development this dev server proxies them to the
// published backend port, which is the same 8800 the launcher serves on the host.
// Keeping the client's own paths byte-identical across both is what makes "it works
// in dev" mean something for the one command.
const gatewayHttp = process.env.COMPANY_OS_GATEWAY_URL ?? 'http://127.0.0.1:8800'
const gatewayWs = gatewayHttp.replace(/^http/, 'ws')

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: gatewayHttp,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
      '/ws': {
        target: gatewayWs,
        ws: true,
        changeOrigin: true,
        // The stream refuses a handshake whose `Origin` is not the authority the
        // socket was opened against (R26), and `changeOrigin` rewrites only `Host`.
        // Without this the dev server would hand the backend `Host: 127.0.0.1:8800`
        // with `Origin: http://127.0.0.1:5173` — a genuinely foreign-looking pair,
        // refused for a correct reason by a check the dev server had defeated.
        // Verified against a real handshake: `Origin` arrives as the target.
        rewriteWsOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.ts', 'tests/**/*.test.tsx'],
  },
})
