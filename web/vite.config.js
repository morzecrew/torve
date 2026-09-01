import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// The bundle lands directly in the wheel's package data (RFC 0032 D-32.4).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "../src/torve/_web",
    emptyOutDir: true,
  },
  server: {
    proxy: { "/api": "http://127.0.0.1:7433" },
  },
});
