// Which page opens when the address has no route. On phones it can be "Dziś" instead of the dashboard;
// the choice is per device (localStorage), so no server setting or migration is needed.
const KEY = "jarvis.homeRoute";
const phone = () => window.matchMedia("(max-width: 720px)").matches;

export type Home = "today" | "dashboard";

export const getHome = (): Home => {
  try {
    return localStorage.getItem(KEY) === "dashboard" ? "dashboard" : "today";
  } catch {
    return "today";
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
