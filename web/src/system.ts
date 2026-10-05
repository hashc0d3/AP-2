import { api } from "./api";
import { escapeHtml } from "./format";
import type { SystemMetrics } from "./types";

const POLL_MS = 5000;

const REQUEST_LABELS: { key: string; label: string }[] = [
  { key: "json", label: "JSON" },
  { key: "429", label: "429" },
  { key: "403", label: "403" },
  { key: "439", label: "439" },
  { key: "timeout", label: "Таймаут" },
  { key: "drop", label: "Обрыв" },
  { key: "antibot", label: "Без JSON" },
  { key: "other", label: "Прочее" },
];

export function mountSystem(): {
  toggle: () => void;
  isOpen: () => boolean;
  stop: () => void;
} {
  const page = document.getElementById("system") as HTMLElement;
  const feed = document.getElementById("feed") as HTMLElement;
  const updated = document.getElementById("system-updated") as HTMLElement;
  const body = document.getElementById("system-body") as HTMLElement;
  const back = document.getElementById("system-back") as HTMLButtonElement;
  let open = false;
  let timer = 0;
  let loading = false;

  const setOpen = (next: boolean) => {
    open = next;
    page.classList.toggle("hidden", !open);
    feed.classList.toggle("hidden", open);
    const systemBtn = document.getElementById("system-open-btn");
    systemBtn?.classList.toggle("active", open);
    systemBtn?.setAttribute("aria-pressed", open ? "true" : "false");
    if (open) {
      void refresh();
      timer = window.setInterval(() => void refresh(), POLL_MS);
      return;
    }
    window.clearInterval(timer);
    timer = 0;
  };

  const refresh = async () => {
    if (!open || loading) return;
    loading = true;
    try {
      render(await api.metrics());
      updated.textContent = `Обновлено ${formatClock(Date.now() / 1000)}`;
      updated.classList.remove("system-error");
    } catch (err) {
      updated.textContent = err instanceof Error ? err.message : "Не удалось загрузить замеры";
      updated.classList.add("system-error");
    } finally {
      loading = false;
    }
  };

  const render = (data: SystemMetrics) => {
    const age =
      data.json_age_min_sec == null
        ? "—"
        : `${data.json_age_min_sec}–${data.json_age_max_sec ?? data.json_age_min_sec} с`;
    body.innerHTML = `
      <section class="system-grid">
        ${card("Поиск", data.search.running ? "идёт" : "остановлен", searchLine(data))}
        ${card("Циклы", String(data.cycles), `успешных ${data.cycles_ok}, пустых ${data.cycles_failed}`)}
        ${card("Темп", data.pace_sec ? `${data.pace_sec} с` : "—", cycleLine(data))}
        ${card("Новых в ленте", String(data.new_ads), `наборов в кольце ${data.cookie_sets}`)}
        ${card("Свежесть JSON", age, "возраст объявлений в последнем ответе")}
        ${card("Cookies", String(data.cookies.usable), cookieLine(data))}
      </section>
      <section class="system-panel">
        <h2>Ответы с запуска</h2>
        <div class="system-counts">${REQUEST_LABELS.map((item) => countPill(item.label, data.requests[item.key] || 0, item.key)).join("")}</div>
      </section>
      <section class="system-panel">
        <h2>Прокси</h2>
        ${proxyTable(data)}
      </section>
      <section class="system-panel">
        <h2>Последние ошибки</h2>
        ${eventList(data)}
      </section>
    `;
  };

  back.addEventListener("click", () => setOpen(false));

  return {
    toggle: () => setOpen(!open),
    isOpen: () => open,
    stop: () => setOpen(false),
  };
}

function card(title: string, value: string, hint: string): string {
  return `<article class="system-card">
    <p class="system-card-title">${escapeHtml(title)}</p>
    <p class="system-card-value">${escapeHtml(value)}</p>
    <p class="system-card-hint">${escapeHtml(hint)}</p>
  </article>`;
}

function countPill(label: string, value: number, kind: string): string {
  return `<span class="system-count kind-${escapeHtml(kind)}"><span>${escapeHtml(label)}</span><strong>${value}</strong></span>`;
}

function searchLine(data: SystemMetrics): string {
  if (data.search.error) return data.search.error;
  const parts = [data.search.region, data.search.category, data.search.query].filter(Boolean);
  return parts.join(" · ") || "поиск ещё не запускали";
}

function cycleLine(data: SystemMetrics): string {
  const last = data.last_cycle_sec == null ? "—" : `${data.last_cycle_sec} с`;
  const median = data.cycle_median_sec == null ? "—" : `${data.cycle_median_sec} с`;
  return `последний ${last}, медиана ${median}`;
}

function cookieLine(data: SystemMetrics): string {
  const counts = data.cookies.counts;
  const age =
    data.cookies.oldest_hours == null ? "" : `, старшему ${formatHours(data.cookies.oldest_hours)}`;
  return `готовы ${counts.ready}, заняты ${counts.in_use}, блок ${counts.blocked}, мертвы ${counts.dead}${age}`;
}

function proxyTable(data: SystemMetrics): string {
  if (!data.proxies.length) return `<p class="hint">Прокси не настроены</p>`;
  const rows = data.proxies
    .map((proxy) => {
      const requests = proxy.requests || {};
      const where = [proxy.ip, proxy.prefix].filter(Boolean).join(" · ") || "адрес ещё не известен";
      return `<tr>
        <td>${escapeHtml(proxy.label)}<span class="system-sub">${escapeHtml(where)}</span></td>
        <td>${escapeHtml(proxyState(proxy))}</td>
        <td>${requests.json || 0}</td>
        <td>${requests["429"] || 0}</td>
        <td>${(requests["403"] || 0) + (requests["439"] || 0)}</td>
        <td>${(requests.timeout || 0) + (requests.drop || 0)}</td>
      </tr>`;
    })
    .join("");
  return `<div class="system-table-wrap"><table class="system-table">
    <thead><tr><th>Канал</th><th>Состояние</th><th>JSON</th><th>429</th><th>403/439</th><th>Сеть</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

function proxyState(proxy: SystemMetrics["proxies"][number]): string {
  if (proxy.changing) return "меняет IP";
  if (!proxy.available) {
    const pause = proxy.cooldown_sec > 0 ? `, пауза ${Math.ceil(proxy.cooldown_sec)} с` : "";
    return `на паузе${pause}`;
  }
  return "в работе";
}

function eventList(data: SystemMetrics): string {
  if (!data.events.length) return `<p class="hint">Ошибок с запуска не было</p>`;
  const items = [...data.events].reverse().map((event) => {
    const proxy = event.proxy ? ` · ${event.proxy}` : "";
    return `<li><time>${escapeHtml(formatClock(event.at))}</time><span>${escapeHtml(event.text)}${escapeHtml(proxy)}</span></li>`;
  });
  return `<ul class="system-events">${items.join("")}</ul>`;
}

function formatClock(unix: number): string {
  return new Date(unix * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatHours(hours: number): string {
  if (hours < 1) return `${Math.round(hours * 60)} мин`;
  return `${hours.toFixed(1)} ч`;
}
