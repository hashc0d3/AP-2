const KEY = "parser1.theme";

export function initTheme(): void {
  try {
    if (localStorage.getItem(KEY) === "light") {
      document.documentElement.dataset.theme = "light";
    }
  } catch {
    /* empty */
  }
}

export function isLightTheme(): boolean {
  return document.documentElement.dataset.theme === "light";
}

export function setLightTheme(on: boolean): void {
  if (on) {
    document.documentElement.dataset.theme = "light";
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
  try {
    localStorage.setItem(KEY, on ? "light" : "dark");
  } catch {
    /* empty */
  }
}
