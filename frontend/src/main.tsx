import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { registerSW } from "./push";
import { applyTheme, currentTheme } from "./theme";
// fonts are bundled (no requests to Google); a face is downloaded only when a theme uses it
import "@fontsource-variable/inter";
import "@fontsource-variable/space-grotesk";
import "@fontsource-variable/jetbrains-mono";
import "@fontsource-variable/orbitron";
import "./styles.css";

applyTheme(currentTheme()).catch(() => undefined);
registerSW();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
