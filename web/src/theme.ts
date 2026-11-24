// Dark mode (2026-07-19): the theme is a data-theme attribute on <html> that the
// token overrides in styles/tokens.css key off. Persisted per browser; applied
// at boot (main.tsx) before first paint so there's no light flash.
export type Theme = "light" | "dark";

const KEY = "rs.theme";

export function currentTheme(): Theme {
  try {
    const t = localStorage.getItem(KEY);
    if (t === "dark" || t === "light") return t;
  } catch { /* storage blocked — default */ }
  return "light";
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem(KEY, theme); } catch { /* storage blocked — session-only */ }
}

export function toggleTheme(): Theme {
  const next: Theme = currentTheme() === "dark" ? "light" : "dark";
  applyTheme(next);
  return next;
}
