import personality from "./theme-personality.json";

export interface ThemeInfo {
  id: string;
  name: string;
  file: string;
}

const KEY = "jarvis.theme";
/** Theme token -> CSS variable. Required tokens are checked by scripts/check-themes.mjs (part of `npm run build`). */
export const VARS: Record<string, string> = {
  bg: "--bg", panel: "--panel", panel2: "--panel-2", border: "--border", text: "--text",
  muted: "--muted", accent: "--accent", accentContrast: "--accent-contrast", danger: "--danger",
  ok: "--ok", warn: "--warn", radius: "--radius", glow: "--glow", font: "--font", fontMono: "--font-mono",
  space: "--space", borderWidth: "--border-width", shadow: "--shadow", bgImage: "--bg-image", focus: "--focus",
  fontDisplay: "--font-display", headingCase: "--heading-case", headingTracking: "--heading-tracking",
  headingWeight: "--heading-weight", controlRadius: "--control-radius", controlHeight: "--control-height",
};

/** Personality: changes the SHAPE of the UI, not only colours. Values are styled in styles.css ("theme personalities"). */
export const PERSONALITY: Record<string, string[]> = personality; // first value = default

export const listThemes = (): Promise<ThemeInfo[]> => fetch("/themes/index.json").then((r) => r.json());

export const currentTheme = () => {
  try {
    return localStorage.getItem(KEY) ?? "default";
  } catch {
    return "default";
  }
};

/** Themes are plain JSON token files in /themes; add a file + an index.json entry to add one.
 *  Tokens a theme leaves out fall back to the defaults in styles.css (:root). */
export const loadTheme = (info: ThemeInfo): Promise<Record<string, string>> => fetch(`/themes/${info.file}`).then((r) => r.json());

export async function applyTheme(id: string): Promise<void> {
  const themes = await listThemes();
  const info = themes.find((x) => x.id === id) ?? themes[0];
  const tokens = await loadTheme(info);
  const root = document.documentElement;
  for (const [k, cssVar] of Object.entries(VARS)) {
    if (tokens[k]) root.style.setProperty(cssVar, tokens[k]);
    else root.style.removeProperty(cssVar);
  }
  root.style.colorScheme = tokens.colorScheme === "light" ? "light" : "dark"; // native controls, scrollbars
  for (const [key, allowed] of Object.entries(PERSONALITY))
    root.dataset[key] = allowed.includes(tokens[key]) ? tokens[key] : allowed[0];
  root.dataset.theme = info.id;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", tokens.bg ?? "#0f1320");
  try {
    localStorage.setItem(KEY, info.id);
  } catch {
    /* private mode */
  }
}
