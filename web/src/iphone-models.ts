export type IphoneModelOption = {
  id: string;
  gen: number;
  label: string;
};

export const IPHONE_MODELS: IphoneModelOption[] = [
  { id: "6", gen: 6, label: "6" },
  { id: "7", gen: 7, label: "7" },
  { id: "8", gen: 8, label: "8" },
  { id: "10", gen: 10, label: "X" },
  { id: "11", gen: 11, label: "11" },
  { id: "12", gen: 12, label: "12" },
  { id: "13", gen: 13, label: "13" },
  { id: "14", gen: 14, label: "14" },
  { id: "15", gen: 15, label: "15" },
  { id: "16", gen: 16, label: "16" },
  { id: "17", gen: 17, label: "17" },
];

export const DEFAULT_IPHONE_MODELS = IPHONE_MODELS.map((item) => item.id);

const VALID_IDS = new Set(DEFAULT_IPHONE_MODELS);

export const IPHONE_MODELS_KEY = "parser1.iphoneModels";
export const IPHONE_MODELS_VERSION = 3;

function rawToId(raw: unknown): string | null {
  if (typeof raw === "number" && Number.isFinite(raw)) {
    const id = String(raw);
    return VALID_IDS.has(id) ? id : null;
  }
  const text = String(raw || "").trim().toLowerCase();
  if (!text) return null;
  if (VALID_IDS.has(text)) return text;
  if (text === "x" || text.startsWith("xs") || text === "xr") return "10";
  const head = text.split("-", 1)[0];
  if (VALID_IDS.has(head)) return head;
  return null;
}

export function normalizeIphoneModels(values: unknown): string[] {
  if (!Array.isArray(values)) return [...DEFAULT_IPHONE_MODELS];
  const out: string[] = [];
  const seen = new Set<string>();
  for (const raw of values) {
    const id = rawToId(raw);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push(id);
  }
  return out.length ? out : [...DEFAULT_IPHONE_MODELS];
}

export function loadIphoneModels(): string[] {
  try {
    const version = Number(localStorage.getItem(`${IPHONE_MODELS_KEY}.version`) || 0);
    const raw = localStorage.getItem(IPHONE_MODELS_KEY);
    if (!raw) return [...DEFAULT_IPHONE_MODELS];
    const parsed = normalizeIphoneModels(JSON.parse(raw));
    if (version < IPHONE_MODELS_VERSION) {
      saveIphoneModels(parsed);
    }
    return parsed;
  } catch {
    return [...DEFAULT_IPHONE_MODELS];
  }
}

export function saveIphoneModels(models: string[]): void {
  try {
    localStorage.setItem(IPHONE_MODELS_KEY, JSON.stringify(normalizeIphoneModels(models)));
    localStorage.setItem(`${IPHONE_MODELS_KEY}.version`, String(IPHONE_MODELS_VERSION));
  } catch {
    /* empty */
  }
}

export function isDefaultIphoneSelection(models: string[]): boolean {
  if (models.length !== DEFAULT_IPHONE_MODELS.length) return false;
  const selected = new Set(models);
  return DEFAULT_IPHONE_MODELS.every((id) => selected.has(id));
}

export function iphoneModelsToGens(models: string[]): number[] {
  const map = new Map(IPHONE_MODELS.map((item) => [item.id, item.gen]));
  return normalizeIphoneModels(models)
    .map((id) => map.get(id))
    .filter((gen): gen is number => typeof gen === "number");
}
