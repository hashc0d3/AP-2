export type IphoneModelOption = {
  id: string;
  gen: number;
  label: string;
};

export const IPHONE_MODELS: IphoneModelOption[] = [
  { id: "11", gen: 11, label: "iPhone 11" },
  { id: "11-pro", gen: 11, label: "iPhone 11 Pro" },
  { id: "11-pro-max", gen: 11, label: "iPhone 11 Pro Max" },
  { id: "12", gen: 12, label: "iPhone 12" },
  { id: "12-mini", gen: 12, label: "iPhone 12 mini" },
  { id: "12-pro", gen: 12, label: "iPhone 12 Pro" },
  { id: "12-pro-max", gen: 12, label: "iPhone 12 Pro Max" },
  { id: "13", gen: 13, label: "iPhone 13" },
  { id: "13-mini", gen: 13, label: "iPhone 13 mini" },
  { id: "13-pro", gen: 13, label: "iPhone 13 Pro" },
  { id: "13-pro-max", gen: 13, label: "iPhone 13 Pro Max" },
  { id: "14", gen: 14, label: "iPhone 14" },
  { id: "14-plus", gen: 14, label: "iPhone 14 Plus" },
  { id: "14-pro", gen: 14, label: "iPhone 14 Pro" },
  { id: "14-pro-max", gen: 14, label: "iPhone 14 Pro Max" },
  { id: "15", gen: 15, label: "iPhone 15" },
  { id: "15-plus", gen: 15, label: "iPhone 15 Plus" },
  { id: "15-pro", gen: 15, label: "iPhone 15 Pro" },
  { id: "15-pro-max", gen: 15, label: "iPhone 15 Pro Max" },
  { id: "16", gen: 16, label: "iPhone 16" },
  { id: "16-plus", gen: 16, label: "iPhone 16 Plus" },
  { id: "16-pro", gen: 16, label: "iPhone 16 Pro" },
  { id: "16-pro-max", gen: 16, label: "iPhone 16 Pro Max" },
  { id: "16-e", gen: 16, label: "iPhone 16e" },
  { id: "17", gen: 17, label: "iPhone 17" },
  { id: "17-pro", gen: 17, label: "iPhone 17 Pro" },
  { id: "17-pro-max", gen: 17, label: "iPhone 17 Pro Max" },
  { id: "17-air", gen: 17, label: "iPhone Air" },
];

export const DEFAULT_IPHONE_MODELS = IPHONE_MODELS.map((item) => item.id);

const VALID_IDS = new Set(DEFAULT_IPHONE_MODELS);
const MODEL_ORDER = new Map(DEFAULT_IPHONE_MODELS.map((id, index) => [id, index]));
const LEGACY_GEN_IDS = new Set(["6", "7", "8", "10"]);

type NormalizeOptions = {
  expandLegacyGens?: boolean;
  allowEmpty?: boolean;
};

function idsForGen(gen: number): string[] {
  return IPHONE_MODELS.filter((item) => item.gen === gen).map((item) => item.id);
}

function expandLegacyId(raw: string): string[] {
  if (VALID_IDS.has(raw)) return [raw];
  if (!/^\d+$/.test(raw)) return [];
  const gen = Number(raw);
  if (!Number.isFinite(gen) || gen < 11) return [];
  return idsForGen(gen);
}

function rawToId(raw: unknown): string | null {
  if (typeof raw === "number" && Number.isFinite(raw)) {
    const id = String(raw);
    return VALID_IDS.has(id) ? id : null;
  }
  const text = String(raw || "").trim().toLowerCase();
  if (!text) return null;
  if (VALID_IDS.has(text)) return text;
  if (LEGACY_GEN_IDS.has(text)) return null;
  if (/^\d+$/.test(text)) return VALID_IDS.has(text) ? text : null;
  if (text === "16e" || text === "16-e") return "16-e";
  return null;
}

export const IPHONE_MODELS_KEY = "parser1.iphoneModels";
export const IPHONE_MODELS_VERSION = 5;

export function normalizeIphoneModels(values: unknown, options: NormalizeOptions = {}): string[] {
  const { expandLegacyGens = false, allowEmpty = false } = options;
  if (!Array.isArray(values)) return [...DEFAULT_IPHONE_MODELS];
  const out: string[] = [];
  const seen = new Set<string>();
  for (const raw of values) {
    const text = String(raw || "").trim().toLowerCase();
    const candidates = expandLegacyGens && /^\d+$/.test(text)
      ? expandLegacyId(text)
      : ([rawToId(raw)].filter(Boolean) as string[]);
    for (const id of candidates) {
      if (!VALID_IDS.has(id) || seen.has(id)) continue;
      seen.add(id);
      out.push(id);
    }
  }
  if (!out.length) return allowEmpty ? [] : [...DEFAULT_IPHONE_MODELS];
  return out.sort((a, b) => (MODEL_ORDER.get(a) ?? 0) - (MODEL_ORDER.get(b) ?? 0));
}

export function loadIphoneModels(): string[] {
  try {
    const version = Number(localStorage.getItem(`${IPHONE_MODELS_KEY}.version`) || 0);
    const allFlag = localStorage.getItem(`${IPHONE_MODELS_KEY}.all`);
    const raw = localStorage.getItem(IPHONE_MODELS_KEY);

    if (version < IPHONE_MODELS_VERSION && allFlag !== "0") {
      saveIphoneModels([...DEFAULT_IPHONE_MODELS]);
      return [...DEFAULT_IPHONE_MODELS];
    }

    if (!raw) {
      return [...DEFAULT_IPHONE_MODELS];
    }

    if (allFlag === "1" || allFlag === null) {
      return [...DEFAULT_IPHONE_MODELS];
    }

    const parsed = normalizeIphoneModels(JSON.parse(raw), {
      expandLegacyGens: version < 4,
      allowEmpty: true,
    });
    if (version < IPHONE_MODELS_VERSION) {
      saveIphoneModels(parsed);
    }
    return parsed.length ? parsed : [];
  } catch {
    return [...DEFAULT_IPHONE_MODELS];
  }
}

export function saveIphoneModels(models: string[]): void {
  try {
    const normalized = normalizeIphoneModels(models, { allowEmpty: true });
    localStorage.setItem(IPHONE_MODELS_KEY, JSON.stringify(normalized));
    localStorage.setItem(`${IPHONE_MODELS_KEY}.version`, String(IPHONE_MODELS_VERSION));
    localStorage.setItem(
      `${IPHONE_MODELS_KEY}.all`,
      isDefaultIphoneSelection(normalized) ? "1" : "0",
    );
  } catch {
    /* empty */
  }
}

export function isDefaultIphoneSelection(models: string[]): boolean {
  if (models.length !== DEFAULT_IPHONE_MODELS.length) return false;
  const selected = new Set(models);
  return DEFAULT_IPHONE_MODELS.every((id) => selected.has(id));
}

export function iphoneModelsToPayload(models: string[]): string[] {
  const normalized = normalizeIphoneModels(models, { allowEmpty: true });
  return normalized.length ? normalized : [...DEFAULT_IPHONE_MODELS];
}

export function iphoneModelsSummary(models: string[]): string {
  const normalized = normalizeIphoneModels(models, { allowEmpty: true });
  if (!normalized.length) return "Не выбрано";
  if (isDefaultIphoneSelection(normalized)) return "Все модели";
  if (normalized.length === 1) {
    return IPHONE_MODELS.find((item) => item.id === normalized[0])?.label || normalized[0];
  }
  return `${normalized.length} модели`;
}
