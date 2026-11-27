// Which pages sit in the phone tab bar. The user picks them in Settings; the choice is per device (localStorage), like
// the home page, so it needs no server setting. "Więcej" (the app drawer) is always the last tab.
import { useEffect, useState } from "react";

const KEY = "jarvis.tabbar";
export const TAB_MAX = 4;
const DEFAULT = ["dashboard", "tasks", "calendar", "notes"];
const EVENT = "jarvis:tabbar";

const read = (): string[] | null => {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "null");
    return Array.isArray(v) && v.every((x) => typeof x === "string") ? v : null;
  } catch {
    return null;
  }
};

// The saved list (or the default) limited to pages that exist now: a disabled module just drops out.
// `version` (from useTabbarVersion) only makes callers re-read the saved list after a change.
export const tabbarIds = (available: string[], _version = 0): string[] => {
  const saved = read();
  const wanted = saved ?? DEFAULT;
  const ids = wanted.filter((id) => available.includes(id)).slice(0, TAB_MAX);
  // default list with missing modules: fill up from the first available ones so the bar is never nearly empty
  if (!saved) for (const id of available) if (ids.length < TAB_MAX && !ids.includes(id)) ids.push(id);
  return ids;
};

export const setTabbar = (ids: string[]) => {
  try {
    localStorage.setItem(KEY, JSON.stringify(ids.slice(0, TAB_MAX)));
  } catch {
    /* private window: the default stays */
  }
  window.dispatchEvent(new Event(EVENT));
};

export const resetTabbar = () => {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* nothing to reset */
  }
  window.dispatchEvent(new Event(EVENT));
};

export const isDefaultTabbar = () => read() === null;

// Re-renders when the choice changes (Settings and the shell are separate trees).
export const useTabbarVersion = () => {
  const [v, setV] = useState(0);
  useEffect(() => {
    const on = () => setV((n) => n + 1);
    window.addEventListener(EVENT, on);
    return () => window.removeEventListener(EVENT, on);
  }, []);
  return v;
};
