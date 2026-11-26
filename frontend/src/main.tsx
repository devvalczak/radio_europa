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

// "Share to Jarvis" from another app opens "/?share_title=…&share_text=…&share_url=…": turn it into the capture form
// (hash routes survive the login screen, and the address is cleaned so a reload does not share again)
const shareParams = new URLSearchParams(window.location.search);
if (["share_title", "share_text", "share_url"].some((k) => shareParams.has(k))) {
  const shared = ["share_title", "share_text", "share_url"].map((k) => shareParams.get(k)).filter(Boolean).join(" ").trim();
  history.replaceState(null, "", `/#/capture?text=${encodeURIComponent(shared.slice(0, 500))}`);
}

applyTheme(currentTheme()).catch(() => undefined);
registerSW();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
