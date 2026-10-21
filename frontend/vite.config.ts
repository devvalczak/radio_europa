import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vite";

const nm = (name: string) => path.resolve(__dirname, "node_modules", name);

export default defineConfig({
  plugins: [react()],
  resolve: {
    // module code lives outside this folder (../modules), so pin shared deps here
    alias: {
      "@jarvis/sdk": path.resolve(__dirname, "src/sdk.ts"),
      "react/jsx-runtime": nm("react/jsx-runtime"),
      "react/jsx-dev-runtime": nm("react/jsx-dev-runtime"),
      "react-dom": nm("react-dom"),
      "react-markdown": nm("react-markdown"),
      react: nm("react"),
    },
  },
  server: {
    host: true,
    port: 5173,
    fs: { allow: [".."] },
    proxy: { "/api": process.env.VITE_API_TARGET ?? "http://localhost:8000" },
  },
});
