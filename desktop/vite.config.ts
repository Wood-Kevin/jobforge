import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri serves the built files; the dev server is only for `tauri dev`.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 1420, strictPort: true, host: "127.0.0.1" },
  build: { outDir: "dist", target: "es2022" },
});
