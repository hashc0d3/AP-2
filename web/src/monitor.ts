import { api } from "./api";
import { escapeHtml, imgSrc } from "./format";
import type { Ad, Category, Region } from "./types";

const CACHE_KEY = "parser1.search";
const PANEL_KEY = "parser1.searchPanel";
const CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M5 12.5 10 17.5 19 7"></path></svg>';

function $(id: string): HTMLElement {
  const el = document.getElementById(id);
  if (!el) throw new Error(`#${id} не найден`);
  return el;
}

function input(id: string): HTMLInputElement {
  return $(id) as HTMLInputElement;
}

export function mountMonitor(): {
  show: () => void;
  hide: () => void;
} {
  const app = $("app");
  const feed = $("feed");
  const statusEl = $("status");
  const avitoSessionEl = $("avito-session");
  const avitoModal = $("avito-modal");
  const avitoStatusLine = $("avito-status-line");
  const avitoHint = $("avito-hint");
  const avitoImport = $("avito-import") as HTMLButtonElement;
  const avitoConnectedBox = $("avito-connected-box");
  const avitoSetupBox = $("avito-setup-box");
  const avitoConnectedLabel = $("avito-connected-label");
  const avitoReset = $("avito-reset") as HTMLButtonElement;
  const avitoCookies = $("avito-cookies") as HTMLTextAreaElement;
  const titleEl = $("title");
  const queryEl = input("query");
  const regionBtn = $("region-btn");
  const regionLabel = $("region-label");
  const regionPop = $("region-pop");
  const regionQuery = input("region-query");
  const regionList = $("region-list");
  const searchPanel = $("search-panel");
  const toggleSearchBtn = $("toggle-search") as HTMLButtonElement;
  const startBtn = $("start") as HTMLButtonElement;
  const stopBtn = $("stop") as HTMLButtonElement;
  const findBtn = $("find") as HTMLButtonElement;
  const catsEl = $("cats");
  const seen = new Set<string>();
  let region: Region = { slug: "all", name: "Вся Россия" };
  let category: Category = { id: "none", name: "Без категории" };
  let categories: Category[] = [
    { id: "none", name: "Без категории" },
    { id: "electronics", name: "Электроника" },
    { id: "phones", name: "Смартфоны" },
  ];
  let monitoring = false;
  let started = false;
  let events: EventSource | null = null;
  let avitoConnected = false;
  const phoneCache = new Map<string, string>();

  const setAvitoSession = (connected: boolean, label = "") => {
    avitoConnected = connected;
    avitoSessionEl.classList.toggle("on", connected);
    avitoSessionEl.classList.toggle("off", !connected);
    avitoSessionEl.textContent = connected ? `Avito: ${label || "вход"}` : "Avito: подключить";
    avitoSessionEl.title = connected
      ? "Аккаунт подключён"
      : "Подключить Avito через Kiwi Browser";
  };

  const updateAvitoModal = (data: { connected?: boolean; label?: string; error?: string }) => {
    const connected = Boolean(data.connected);
    const label = data.label || "";
    avitoStatusLine.textContent = connected ? `Подключён${label ? ` (${label})` : ""}` : "Не подключён";
    setAvitoSession(connected, label);
    avitoConnectedBox.classList.toggle("hidden", !connected);
    avitoSetupBox.classList.toggle("hidden", connected);
    avitoConnectedLabel.textContent = label || "вход";
    if (data.error) avitoHint.textContent = data.error;
  };

  const resetAvitoConnection = async (hint = "Подключение сброшено") => {
    await api.clearAvitoSession().catch(() => undefined);
    avitoCookies.value = "";
    updateAvitoModal({ connected: false });
    avitoHint.textContent = hint;
  };

  const openAvitoModal = () => {
    avitoModal.classList.remove("hidden");
    avitoHint.textContent = "";
    void refreshAvitoSession();
  };

  const closeAvitoModal = () => {
    avitoModal.classList.add("hidden");
  };

  const refreshAvitoSession = async () => {
    try {
      const data = await api.avitoSession();
      updateAvitoModal({ connected: data.connected, label: data.label });
    } catch {
      setAvitoSession(false);
    }
  };

  const dialPhone = (phone: string) => {
    const link = document.createElement("a");
    link.href = `tel:${phone}`;
    link.rel = "noopener";
    link.click();
  };

  const requestPhone = async (ad: Ad, btn: HTMLButtonElement) => {
    if (!ad.can_call) {
      window.open(ad.url, "_blank", "noopener");
      return;
    }
    const id = String(ad.id);
    const cached = phoneCache.get(id);
    if (cached) {
      dialPhone(cached);
      return;
    }
    if (!avitoConnected) {
      openAvitoModal();
      return;
    }
    const prev = btn.textContent;
    btn.disabled = true;
    btn.textContent = "…";
    try {
      const result = await api.avitoPhone(id);
      if (result.ok && result.phone) {
        phoneCache.set(id, result.phone);
        btn.textContent = result.phone;
        dialPhone(result.phone);
        return;
      }
      const message = result.error || "Номер недоступен";
      if (result.code === "no_session" || result.code === "not_logged_in" || result.code === "auth_required") {
        setAvitoSession(false);
        openAvitoModal();
      } else {
        window.alert(message);
      }
      btn.textContent = prev || "Позвонить";
    } catch (err) {
      window.alert(err instanceof Error ? err.message : String(err));
      btn.textContent = prev || "Позвонить";
    } finally {
      btn.disabled = false;
    }
  };

  const loadCache = () => {
    try {
      const data = JSON.parse(localStorage.getItem(CACHE_KEY) || "") as {
        region?: Region;
        category?: Category;
      };
      if (data.region?.slug && data.region.name) {
        region = { slug: String(data.region.slug), name: String(data.region.name) };
        regionLabel.textContent = region.name;
      }
      if (data.category?.id && data.category.name) {
        category = { id: String(data.category.id), name: String(data.category.name) };
      }
    } catch {
      /* empty */
    }
  };

  const saveCache = () => {
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify({ region, category }));
    } catch {
      /* empty */
    }
  };

  const cardHtml = (ad: Ad): string => {
    const images = ad.images || [];
    const gallery = images.length
      ? `<img src="${imgSrc(images[0])}" alt="" data-i="0" />
         ${images.length > 1 ? `<button class="nav prev" type="button">‹</button><button class="nav next" type="button">›</button>` : ""}`
      : `<div class="ph">нет фото</div>`;
    const imagesAttr = encodeURIComponent(JSON.stringify(images));
    const sellerHtml = ad.seller ? `<div class="seller">${escapeHtml(ad.seller)}</div>` : "";
    return `<article class="card${ad.can_call ? " callable" : ""}" data-id="${ad.id}">
      <div class="gallery" data-images="${imagesAttr}">${gallery}</div>
      <div class="body">
        <div class="price">${escapeHtml(ad.price || "")}</div>
        <div class="title">${escapeHtml(ad.title || "")}</div>
        <div class="addr">${escapeHtml(ad.published ? ad.published + " · " : "")}${escapeHtml(ad.address || "")}</div>
        ${sellerHtml}
        <div class="tags">
          <span class="tag ${ad.can_call ? "on" : "off"}">${ad.can_call ? "Звонок" : "Без звонка"}</span>
          <span class="tag ${ad.can_message ? "on" : "off"}">${ad.can_message ? "Сообщение" : "Без сообщений"}</span>
        </div>
        <div class="actions">
          <button class="call${ad.can_call ? "" : " muted"}" type="button" data-action="call">${ad.can_call ? "Позвонить" : "Открыть"}</button>
          <a class="msg${ad.can_message ? "" : " muted"}" href="${ad.url}" target="_blank" rel="noopener">Написать</a>
          <a class="open" href="${ad.url}" target="_blank" rel="noopener">Открыть</a>
        </div>
      </div>
    </article>`;
  };

  const bindCardActions = (card: HTMLElement, ad: Ad) => {
    const callBtn = card.querySelector('button[data-action="call"]') as HTMLButtonElement | null;
    if (!callBtn) return;
    callBtn.addEventListener("click", (ev) => {
      ev.preventDefault();
      void requestPhone(ad, callBtn);
    });
  };

  const bindGallery = (card: HTMLElement) => {
    const box = card.querySelector(".gallery") as HTMLElement | null;
    if (!box) return;
    let images: string[] = [];
    try {
      images = JSON.parse(decodeURIComponent(box.dataset.images || "%5B%5D")) as string[];
    } catch {
      images = [];
    }
    if (images.length < 2) return;
    const img = box.querySelector("img");
    if (!img) return;
    let i = 0;
    const show = () => {
      img.src = imgSrc(images[i]);
      img.dataset.i = String(i);
    };
    box.querySelector(".prev")?.addEventListener("click", () => {
      i = (i - 1 + images.length) % images.length;
      show();
    });
    box.querySelector(".next")?.addEventListener("click", () => {
      i = (i + 1) % images.length;
      show();
    });
  };

  const setStatus = (text: string, live: boolean) => {
    statusEl.textContent = text;
    statusEl.classList.toggle("live", live);
  };

  const mount = (ad: Ad, fresh: boolean): HTMLElement | null => {
    const id = String(ad.id);
    if (seen.has(id)) return null;
    seen.add(id);
    const wrap = document.createElement("div");
    wrap.innerHTML = cardHtml(ad);
    const el = wrap.firstElementChild as HTMLElement;
    if (fresh) el.classList.add("fresh");
    bindGallery(el);
    bindCardActions(el, ad);
    return el;
  };

  const addBatch = (ads: Ad[], fresh: boolean) => {
    if (!Array.isArray(ads) || !ads.length) {
      if (monitoring && !seen.size) setStatus("жду объявления", true);
      return;
    }
    feed.querySelector(".empty")?.remove();
    const marker = feed.firstChild;
    ads.forEach((ad) => {
      const el = mount(ad, fresh);
      if (el) feed.insertBefore(el, marker);
    });
    setStatus("онлайн · " + seen.size, true);
  };

  const clearFeed = (message?: string) => {
    seen.clear();
    feed.innerHTML = `<div class="empty">${message || "Жду новые объявления…"}</div>`;
    if (monitoring) setStatus("онлайн · 0", true);
  };

  const setBusy = (busy: boolean) => {
    startBtn.disabled = busy;
    findBtn.disabled = busy;
    stopBtn.disabled = busy || !monitoring;
    startBtn.textContent = busy ? "Запускаю…" : "Начать поиск";
  };

  const setMonitoring = (on: boolean) => {
    monitoring = on;
    stopBtn.disabled = !on;
  };

  const updateTitle = () => {
    const q = queryEl.value.trim();
    const cat = category.id === "none" ? "" : " · " + category.name;
    titleEl.textContent = q
      ? `${q} · ${region.name}${cat}`
      : `Новые частные · ${region.name}${cat}`;
  };

  const renderCats = (items?: Category[]) => {
    if (items?.length) categories = items;
    catsEl.innerHTML = categories.map((item) => (
      `<button type="button" class="chip${item.id === category.id ? " active" : ""}" data-id="${escapeHtml(item.id)}" data-name="${escapeHtml(item.name)}">
        <span class="mark">${CHECK}</span>${escapeHtml(item.name)}
      </button>`
    )).join("");
  };

  const setSearchPanelVisible = (visible: boolean) => {
    searchPanel.classList.toggle("hidden", !visible);
    toggleSearchBtn.textContent = visible ? "Скрыть" : "Показать";
    try {
      localStorage.setItem(PANEL_KEY, visible ? "open" : "closed");
    } catch {
      /* empty */
    }
  };

  const loadSearchPanelState = () => {
    try {
      setSearchPanelVisible(localStorage.getItem(PANEL_KEY) !== "closed");
    } catch {
      setSearchPanelVisible(true);
    }
  };

  const refreshPreview = () => {
    updateTitle();
  };

  const loadRegions = async (needle: string) => {
    const data = await api.regions(needle);
    if (!data.length) {
      regionList.innerHTML = '<div class="region-empty">Ничего не найдено</div>';
      return;
    }
    regionList.innerHTML = data.map((item) => (
      `<button type="button" data-slug="${escapeHtml(item.slug)}" class="${item.slug === region.slug ? "active" : ""}">${escapeHtml(item.name)}</button>`
    )).join("");
  };

  const startSearch = async (ev?: Event) => {
    ev?.preventDefault();
    setBusy(true);
    setStatus("готовлю поиск…", false);
    try {
      const data = await api.startSearch(queryEl.value, region.slug, category.id);
      setMonitoring(true);
      if (data.region) {
        region = data.region;
        regionLabel.textContent = region.name;
      }
      if (data.category) {
        category = data.category;
        renderCats();
      }
      saveCache();
      updateTitle();
      clearFeed();
      setStatus("поиск запущен", true);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err), false);
    } finally {
      setBusy(false);
    }
  };

  const stopSearch = async () => {
    stopBtn.disabled = true;
    try {
      await api.stopSearch();
      setMonitoring(false);
      setStatus("остановлен", false);
    } catch (err) {
      stopBtn.disabled = !monitoring;
      setStatus(err instanceof Error ? err.message : String(err), false);
    }
  };

  const connectEvents = () => {
    if (events) return;
    events = new EventSource("/events");
    events.onmessage = (ev) => {
      try {
        addBatch(JSON.parse(ev.data) as Ad[], true);
      } catch {
        /* empty */
      }
    };
    events.addEventListener("reset", () => clearFeed());
    events.onerror = () => setStatus("переподключение…", false);
    events.onopen = () => {
      if (monitoring) setStatus("онлайн · " + seen.size, true);
    };
  };

  const boot = async () => {
    if (started) return;
    started = true;
    loadCache();
    renderCats();
    $("search-form").addEventListener("submit", (ev) => { void startSearch(ev); });
    startBtn.addEventListener("click", () => { void startSearch(); });
    stopBtn.addEventListener("click", () => { void stopSearch(); });
    toggleSearchBtn.addEventListener("click", () => {
      setSearchPanelVisible(searchPanel.classList.contains("hidden"));
    });
    loadSearchPanelState();
    queryEl.addEventListener("input", refreshPreview);
    regionBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const open = regionPop.classList.toggle("open");
      if (open) {
        void loadRegions(regionQuery.value);
        regionQuery.focus();
      }
    });
    regionQuery.addEventListener("input", () => { void loadRegions(regionQuery.value); });
    catsEl.addEventListener("click", (ev) => {
      const btn = (ev.target as HTMLElement).closest("button.chip") as HTMLButtonElement | null;
      if (!btn) return;
      category = { id: btn.dataset.id || "none", name: btn.dataset.name || "" };
      saveCache();
      renderCats();
      refreshPreview();
    });
    regionList.addEventListener("click", (ev) => {
      const btn = (ev.target as HTMLElement).closest("button[data-slug]") as HTMLButtonElement | null;
      if (!btn) return;
      region = { slug: btn.dataset.slug || "all", name: btn.textContent || "" };
      regionLabel.textContent = region.name;
      regionPop.classList.remove("open");
      regionQuery.value = "";
      saveCache();
      refreshPreview();
    });
    document.addEventListener("click", (ev) => {
      const target = ev.target as Node;
      if (!regionPop.contains(target) && target !== regionBtn && !regionBtn.contains(target)) {
        regionPop.classList.remove("open");
      }
    });
    $("reset").addEventListener("click", async () => {
      try {
        await api.reset();
      } catch {
        /* empty */
      }
      clearFeed();
    });
    void refreshAvitoSession();
    avitoSessionEl.addEventListener("click", () => openAvitoModal());
    $("avito-close").addEventListener("click", () => closeAvitoModal());
    avitoModal.addEventListener("click", (ev) => {
      if (ev.target === avitoModal) closeAvitoModal();
    });
    $("avito-open-login").addEventListener("click", () => {
      window.open("https://m.avito.ru", "_blank", "noopener");
      avitoHint.textContent = "После входа: Cookie-Editor → Export → вставьте JSON ниже";
    });
    const importCookies = (raw: string) => {
      if (!raw.trim()) {
        avitoHint.textContent = "Вставьте JSON cookies";
        return;
      }
      void api.importAvitoSession(raw).then((data) => {
        avitoHint.textContent = "Сохранено";
        avitoCookies.value = "";
        updateAvitoModal({ connected: data.connected, label: data.label });
        closeAvitoModal();
      }).catch((err) => {
        avitoHint.textContent = err instanceof Error ? err.message : String(err);
      });
    };
    avitoReset.addEventListener("click", () => {
      avitoReset.disabled = true;
      void resetAvitoConnection().finally(() => {
        avitoReset.disabled = false;
      });
    });
    avitoImport.addEventListener("click", () => {
      avitoImport.disabled = true;
      importCookies(avitoCookies.value);
      avitoImport.disabled = false;
    });
    void api.categories().then(renderCats).catch(() => undefined);
    refreshPreview();
    connectEvents();
    setInterval(() => {
      if (monitoring) void api.ads().then((ads) => addBatch(ads, false)).catch(() => undefined);
    }, 4000);
    try {
      const data = await api.status();
      if (data.running) {
        if (data.query) queryEl.value = data.query;
        if (data.region?.slug) {
          region = data.region;
          regionLabel.textContent = region.name;
        }
        if (data.category?.id) {
          category = data.category;
          renderCats();
        }
        saveCache();
        updateTitle();
        setMonitoring(true);
        setStatus("поиск запущен", true);
        void api.ads().then((ads) => addBatch(ads, false));
      }
    } catch {
      /* empty */
    }
  };

  return {
    show() {
      app.classList.remove("hidden");
      void boot();
    },
    hide() {
      app.classList.add("hidden");
    },
  };
}
