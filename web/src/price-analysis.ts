export const PRICE_PER_ANALYSIS = 0.19;

export type PriceTone = "good" | "bad" | "warn" | "neutral";

export type PriceAnalysisView = {
  status: "loading" | "ok" | "error";
  text: string;
  tone?: PriceTone;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function pickNumber(obj: Record<string, unknown>, keys: string[]): number | null {
  for (const key of keys) {
    const raw = obj[key];
    if (typeof raw === "number" && Number.isFinite(raw)) return raw;
    if (typeof raw === "string" && raw.trim()) {
      const num = Number(raw.replace(/[^\d.-]/g, ""));
      if (Number.isFinite(num)) return num;
    }
  }
  return null;
}

function formatMoney(value: number): string {
  return `${Math.round(value).toLocaleString("ru-RU")} ₽`;
}

function toneFromDiff(diff: number | null): PriceTone {
  if (diff == null) return "neutral";
  if (diff <= -8) return "good";
  if (diff >= 8) return "bad";
  return "warn";
}

export function queryId(value: unknown): string {
  const text = String(value ?? "");
  const match = text.match(/(\d{6,})/);
  return match ? match[1] : text.trim();
}

export function formatPriceResult(item: unknown): PriceAnalysisView {
  const obj = asRecord(item);
  if (!obj) return { status: "error", text: "Нет данных" };
  if (obj.success === false || obj.error) {
    return {
      status: "error",
      text: String(obj.error || obj.message || "Не удалось проанализировать"),
    };
  }

  const nested = asRecord(obj.data) || asRecord(obj.result);
  const source = nested || obj;
  const market = pickNumber(source, [
    "median_price",
    "median",
    "market_price",
    "average_price",
    "avg_price",
    "recommended_price",
    "fair_price",
    "price_median",
    "avg",
    "market",
  ]);
  const diff = pickNumber(source, [
    "diff_percent",
    "diff",
    "percent_diff",
    "price_diff_percent",
    "delta_percent",
  ]);
  const position = pickNumber(source, ["position", "rank", "place"]);
  const verdict = String(source.verdict || source.rating || source.label || source.status_text || "").trim();

  const parts: string[] = [];
  if (market != null) parts.push(`рынок: ${formatMoney(market)}`);
  if (diff != null) {
    const sign = diff > 0 ? "+" : "";
    parts.push(`${sign}${Math.round(diff)}%`);
  }
  if (position != null) parts.push(`поз. ${position}`);
  if (!parts.length && verdict) parts.push(verdict);

  if (!parts.length && nested) return formatPriceResult(nested);

  if (!parts.length) {
    const text = String(source.summary || source.comment || source.message || "").trim();
    if (text) return { status: "ok", text, tone: toneFromDiff(diff) };
    return { status: "error", text: "Пустой ответ" };
  }

  let tone: PriceTone = toneFromDiff(diff);
  const lower = verdict.toLowerCase();
  if (diff == null) {
    if (/выгод|дешев|ниж|good|cheap/i.test(lower)) tone = "good";
    else if (/дорог|выше|плох|bad|high/i.test(lower)) tone = "bad";
  }

  return { status: "ok", text: parts.join(" · "), tone };
}

export function createPriceAnalyzer(opts: {
  region: () => string;
  startBatch: (queries: string[], region: string) => Promise<{ task_id?: string }>;
  pollBatch: (taskId: string) => Promise<{ status?: string; results?: unknown[] }>;
  onUpdate: (adId: string, view: PriceAnalysisView) => void;
  onComplete?: (adIds: string[]) => void;
}) {
  const cache = new Map<string, PriceAnalysisView>();
  const pending = new Set<string>();
  const queue: string[] = [];
  let busy = false;

  const sleep = (ms: number) => new Promise<void>((resolve) => {
    window.setTimeout(resolve, ms);
  });

  const setView = (adId: string, view: PriceAnalysisView) => {
    cache.set(adId, view);
    pending.delete(adId);
    opts.onUpdate(adId, view);
  };

  const request = (adIds: string[]) => {
    for (const adId of adIds) {
      const id = String(adId);
      if (!id || pending.has(id) || queue.includes(id)) continue;
      pending.add(id);
      queue.push(id);
      opts.onUpdate(id, { status: "loading", text: "анализ цены…" });
    }
    void drain();
  };

  const pollUntilDone = async (taskId: string) => {
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const data = await opts.pollBatch(taskId);
      const status = String(data.status || "").toUpperCase();
      if (status === "SUCCESS") return data.results || [];
      if (status === "FAILED" || status === "ERROR" || status === "CANCELLED") {
        throw new Error("Не удалось завершить анализ цен");
      }
      await sleep(1500);
    }
    throw new Error("Таймаут анализа цен");
  };

  const drain = async () => {
    if (busy || !queue.length) return;
    busy = true;
    try {
      while (queue.length) {
        const batch = queue.splice(0, 5);
        try {
          const start = await opts.startBatch(batch, opts.region());
          const taskId = String(start.task_id || "");
          if (!taskId) throw new Error("Сервис не вернул задание");
          const results = await pollUntilDone(taskId);
          const mapped = new Map<string, unknown>();
          results.forEach((item, index) => {
            const obj = asRecord(item);
            const key = queryId(obj?.query || obj?.ad_id || obj?.id || batch[index]);
            if (key) mapped.set(key, item);
          });
          for (const adId of batch) {
            const item = mapped.get(adId) ?? mapped.get(queryId(adId));
            setView(adId, formatPriceResult(item ?? { error: "Нет данных" }));
          }
          opts.onComplete?.(batch);
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          for (const adId of batch) {
            setView(adId, { status: "error", text: message });
          }
        }
        if (queue.length) await sleep(6500);
      }
    } finally {
      busy = false;
    }
  };

  const reset = () => {
    queue.length = 0;
    pending.clear();
    cache.clear();
    busy = false;
  };

  const get = (adId: string) => cache.get(String(adId));
  const isPending = (adId: string) => pending.has(String(adId));

  return { request, reset, get, isPending };
}
