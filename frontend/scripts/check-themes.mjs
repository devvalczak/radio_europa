// Validates every theme in public/themes: all tokens present, colours readable (WCAG 2.1 contrast).
// Runs as part of `npm run build`, so a theme that would make a module unreadable cannot ship.
import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const dir = join(dirname(fileURLToPath(import.meta.url)), "..", "public", "themes");
const COLORS = ["bg", "panel", "panel2", "border", "text", "muted", "accent", "accentContrast", "danger", "ok", "warn", "focus"];
const OTHER = ["radius", "glow", "font", "fontMono", "space", "borderWidth", "shadow", "bgImage", "colorScheme",
  "fontDisplay", "headingCase", "headingTracking", "headingWeight", "controlRadius", "controlHeight"];
// personality values: src/theme-personality.json (each one styled in src/styles.css, "theme personalities")
const PERSONALITY = JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "src", "theme-personality.json"), "utf8"));
// [foreground, backgrounds, minimum ratio, why]
const PAIRS = [
  ["text", ["bg", "panel", "panel2"], 4.5, "body text"],
  ["muted", ["bg", "panel", "panel2"], 4.5, "secondary text"],
  ["accent", ["bg", "panel"], 4.5, "links and accent text"],
  ["accentContrast", ["accent"], 4.5, "text on primary buttons / active tabs"],
  ["danger", ["bg", "panel", "panel2"], 4.5, "error messages"],
  ["ok", ["panel"], 3, "status indicators"],
  ["warn", ["panel"], 3, "warning indicators"],
  ["focus", ["bg", "panel"], 3, "keyboard focus ring"],
];

const hex = (c) => {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(c);
  if (!m) return null;
  const h = m[1].length === 3 ? [...m[1]].map((x) => x + x).join("") : m[1];
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
};
const lum = (rgb) => {
  const [r, g, b] = rgb.map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
export const ratio = (a, b) => {
  const [x, y] = [lum(hex(a)), lum(hex(b))].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
};

const errors = [];
const index = JSON.parse(readFileSync(join(dir, "index.json"), "utf8"));
const ids = new Set();
for (const t of index) {
  const where = `theme "${t.id}" (${t.file})`;
  if (!/^[a-z0-9-]+$/.test(t.id ?? "") || !t.name || !t.file) { errors.push(`${where}: needs id (a-z0-9-), name and file`); continue; }
  if (ids.has(t.id)) errors.push(`${where}: duplicate id`);
  ids.add(t.id);
  if (!existsSync(join(dir, t.file))) { errors.push(`${where}: file missing`); continue; }
  const tok = JSON.parse(readFileSync(join(dir, t.file), "utf8"));
  for (const k of [...COLORS, ...OTHER]) if (typeof tok[k] !== "string" || !tok[k]) errors.push(`${where}: missing token "${k}"`);
  for (const k of COLORS) if (tok[k] && !hex(tok[k])) errors.push(`${where}: "${k}" must be #rgb or #rrggbb (got ${tok[k]})`);
  for (const [k, allowed] of Object.entries(PERSONALITY))
    if (!allowed.includes(tok[k])) errors.push(`${where}: "${k}" must be one of ${allowed.join(", ")} (got ${tok[k]})`);
  if (!["none", "uppercase"].includes(tok.headingCase)) errors.push(`${where}: headingCase must be "none" or "uppercase"`);
  if (parseInt(tok.controlHeight) < 32) errors.push(`${where}: controlHeight below 32px is too small to tap`);
  if (!["light", "dark"].includes(tok.colorScheme)) errors.push(`${where}: colorScheme must be "light" or "dark"`);
  if (COLORS.some((k) => !hex(tok[k] ?? ""))) continue;
  const light = lum(hex(tok.bg)) > 0.4;
  if ((tok.colorScheme === "light") !== light) errors.push(`${where}: colorScheme "${tok.colorScheme}" does not match bg ${tok.bg}`);
  for (const [fg, bgs, min, why] of PAIRS)
    for (const bg of bgs) {
      const r = ratio(tok[fg], tok[bg]);
      if (r < min) errors.push(`${where}: ${fg} ${tok[fg]} on ${bg} ${tok[bg]} = ${r.toFixed(2)}:1 < ${min}:1 (${why})`);
    }
}
if (errors.length) {
  console.error("Theme check failed:\n  " + errors.join("\n  "));
  process.exit(1);
}
console.log(`Themes OK: ${[...ids].join(", ")}`);
