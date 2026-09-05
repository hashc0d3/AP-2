const FAV_KEY = "parser1.favorites";

export function loadFavorites(): Set<string> {
  try {
    const raw = JSON.parse(localStorage.getItem(FAV_KEY) || "[]") as unknown;
    if (!Array.isArray(raw)) return new Set();
    return new Set(raw.map((id) => String(id)));
  } catch {
    return new Set();
  }
}

export function isFavorite(id: string | number): boolean {
  return loadFavorites().has(String(id));
}

export function toggleFavorite(id: string | number): boolean {
  const key = String(id);
  const favs = loadFavorites();
  const on = !favs.has(key);
  if (on) favs.add(key);
  else favs.delete(key);
  try {
    localStorage.setItem(FAV_KEY, JSON.stringify([...favs]));
  } catch {
    /* empty */
  }
  return on;
}
