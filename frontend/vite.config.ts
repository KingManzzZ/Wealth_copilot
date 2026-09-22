import path from "node:path"
import { fileURLToPath } from "node:url"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(fileURLToPath(new URL(".", import.meta.url)), "./src"),
    },
  },
  server: {
    proxy: {
      // 约定：后端 FastAPI 跑在 http://localhost:8000
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
})
