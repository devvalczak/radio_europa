import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { registerSW } from "./push";
import { applyTheme, currentTheme } from "./theme";
import "./styles.css";

applyTheme(currentTheme()).catch(() => undefined);
registerSW();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
