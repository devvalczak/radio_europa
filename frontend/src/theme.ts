export interface ThemeInfo {
  id: string;
  name: string;
  file: string;
}

const KEY = "jarvis.theme";
const VARS: Record<string, string> = {
  bg: "--bg", panel: "--panel", panel2: "--panel-2", border: "--border", text: "--text",
  muted: "--muted", accent: "--accent", accentContrast: "--accent-contrast", danger: "--danger",
  ok: "--ok", radius: "--radius", glow: "--glow", font: "--font",
};

export const listThemes = (): Promise<ThemeInfo[]> => fetch("/themes/index.json").then((r) => r.json());

export const currentTheme = () => {
  try {
    return localStorage.getItem(KEY) ?? "default";
  } catch {
    return "default";
  }
};

/** Themes are plain JSON token files in /themes; add a file + an index.json entry to add one. */
export async function applyTheme(id: string): Promise<void> {
  const themes = await listThemes();
  const info = themes.find((x) => x.id === id) ?? themes[0];
  const tokens: Record<string, string> = await fetch(`/themes/${info.file}`).then((r) => r.json());
  const root = document.documentElement;
  for (const [k, cssVar] of Object.entries(VARS)) if (tokens[k]) root.style.setProperty(cssVar, tokens[k]);
  try {
    localStorage.setItem(KEY, info.id);
  } catch {
    /* private mode */
  }
}
