// Which page opens when the address has no route. The dashboard (tiles) is the default everywhere; on phones the user
// can switch to "Dziś" in Settings. The choice is per device (localStorage), so no server setting or migration is needed.
const KEY = "jarvis.homeRoute";
const phone = () => window.matchMedia("(max-width: 720px)").matches;

export type Home = "today" | "dashboard";

export const getHome = (): Home => {
  try {
    return localStorage.getItem(KEY) === "today" ? "today" : "dashboard";
  } catch {
    return "dashboard";
  }
};

export const setHome = (h: Home) => {
  try {
    localStorage.setItem(KEY, h);
  } catch {
    /* private window: the default stays */
  }
};

export const homeRoute = (): Home => (phone() ? getHome() : "dashboard");
