import { fileURLToPath, URL } from "node:url"

import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// The dashboard ships as wheel package data at src/torve/_web (D-32.4): the
// build output lands inside the Python package so `torve serve` serves it
// from a development checkout and the wheel carries it as package data.
// Node is a build-time concern and stays one — nothing at runtime or install
// touches it.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: fileURLToPath(new URL("../src/torve/_web", import.meta.url)),
    emptyOutDir: true,
  },
})
