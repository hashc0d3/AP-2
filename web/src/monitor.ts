import { api } from "./api";
import { openMessenger, itemWebUrl } from "./avito-links";
import { ICON_CLOSE, ICON_EXT, ICON_MSG, ICON_PHONE, ICON_STAR, ICON_STAR_OUTLINE } from "./card-icons";
import { isFavorite, toggleFavorite } from "./favorites";
import { notifyNewAds } from "./push-notify";
import { displayPrice, escapeHtml, formatAddedAt, imgSrc } from "./format";
import { regionTimezone } from "./region-timezones";
import {
  addSellerToBlacklist,
  BLACKLIST_EVENT,
  loadSellerBlacklist,
  sellerMatchesBlacklist,
  syncSellerBlacklist,
} from "./seller-blacklist";
import { showToast, type ToastKind } from "./toast";
import {
  DEFAULT_IPHONE_MODELS,
  IPHONE_MODELS,
  iphoneModelsToGens,
  isDefaultIphoneSelection,
  loadIphoneModels,
  normalizeIphoneModels,
  saveIphoneModels,
} from "./iphone-models";
import type { Ad, Category, Region, SearchMode } from "./types";

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

export function mountMonitor(opts: {
  isPushEnabled?: () => boolean;
  onAvitoStatus?: (connected: boolean, label?: string) => void;
} = {}): {
  show: () => void;
  hide: () => void;
  openAvito: () => void;
} {
  const app = $("app");
  const feed = $("feed");
  const avitoModal = $("avito-modal");
  const avitoStatusLine = $("avito-status-line");
  const avitoHint = $("avito-hint");
  const avitoImport = $("avito-import") as HTMLButtonElement;
  const avitoConnectedBox = $("avito-connected-box");
  const avitoSetupBox = $("avito-setup-box");
  const avitoConnectedLabel = $("avito-connected-label");
  const avitoReset = $("avito-reset") as HTMLButtonElement;
  const avitoCookies = $("avito-cookies") as HTMLTextAreaElement;
  const queryEl = input("query");
  const queryClearBtn = $("query-clear") as HTMLButtonElement;
  const searchFieldWrap = $("search-field-wrap");
  const searchSettingsBtn = $("search-settings-btn") as HTMLButtonElement;
  const searchSettingsPop = $("search-settings-pop");
  const searchSettingsCats = $("search-settings-cats");
  const iphoneModelsEl = $("iphone-models");
  const iphoneModelsAllBtn = $("iphone-models-all") as HTMLButtonElement;
  const iphoneModelsNoneBtn = $("iphone-models-none") as HTMLButtonElement;
  const searchSettingsUrl = $("search-settings-url");
  const saveUrlCheck = input("save-url");
  const hideImagesCheck = input("hide-images");
  const urlSavedFold = $("url-saved-fold");
  const urlSavedFoldBtn = $("url-saved-fold-btn") as HTMLButtonElement;
  const urlSavedPreview = $("url-saved-preview");
  const savedUrlBox = $("saved-url-box");
  const savedUrlLink = $("saved-url-link") as HTMLAnchorElement;
  const searchFiltersUrl = $("search-filters-url") as HTMLAnchorElement;
  const regionBtn = $("region-btn") as HTMLButtonElement;
  const regionLabel = $("region-label");
  const regionPop = $("region-pop");
  const regionQuery = input("region-query");
  const regionList = $("region-list");
  const regionWrap = $("region-wrap");
  const modeQueryBtn = $("mode-query") as HTMLButtonElement;
  const modeUrlBtn = $("mode-url") as HTMLButtonElement;
  const searchPanel = $("search-panel");
  const searchBody = $("search-body");
  const searchFields = $("search-fields");
  const searchActionsBlock = $("search-actions-block");
  const searchActions = $("search-actions");
  const searchFiltersLabel = $("search-filters-label");
  const toggleSearchBtn = $("toggle-search") as HTMLButtonElement;
  const startBtn = $("start") as HTMLButtonElement;
  const stopBtn = $("stop") as HTMLButtonElement;
  const catsEl = $("cats");
  const seen = new Set<string>();
  let region: Region = { slug: "all", name: "Вся Россия" };
  let category: Category = { id: "none", name: "Без категории" };
  let searchMode: SearchMode = "query";
  let savedSearchUrl = "";
  let hideImages = false;
  let categories: Category[] = [
    { id: "none", name: "Без категории" },
    { id: "electronics", name: "Электроника" },
    { id: "phones", name: "Смартфоны" },
  ];
  let monitoring = false;
  let searchBusy = false;
  let started = false;
  let events: EventSource | null = null;
  let avitoConnected = false;
  let selectedIphoneModels = loadIphoneModels();
  const phoneCache = new Map<string, string>();

  const updateSettingsBtnState = () => {
    const urlActive = searchMode === "url" && (saveUrlCheck.checked || Boolean(savedSearchUrl));
    const iphoneFilterActive = !isDefaultIphoneSelection(selectedIphoneModels);
    searchSettingsBtn.classList.toggle("active", category.id !== "none" || urlActive || hideImages || iphoneFilterActive);
  };

  const persistIphoneModels = () => {
    saveIphoneModels(selectedIphoneModels);
    saveCache();
    updateSettingsBtnState();
  };

  const renderIphoneModels = () => {
    const selected = new Set(selectedIphoneModels);
    iphoneModelsEl.innerHTML = `<div class="cat-row iphone-model-row">${IPHONE_MODELS.map((item) => (
      `<button type="button" class="chip iphone-chip${selected.has(item.id) ? " active" : ""}" data-id="${escapeHtml(item.id)}" aria-pressed="${selected.has(item.id) ? "true" : "false"}">
        <span class="mark">${CHECK}</span>${escapeHtml(item.label)}
      </button>`
    )).join("")}</div>`;
    updateSettingsBtnState();
  };

  const setIphoneModels = (models: string[]) => {
    selectedIphoneModels = normalizeIphoneModels(models);
    renderIphoneModels();
    persistIphoneModels();
  };

  const applyHideImages = () => {
    feed.classList.toggle("no-images", hideImages);
    feed.querySelectorAll<HTMLElement>(".card").forEach((card) => {
      card.classList.toggle("card--no-media", hideImages);
      card.querySelector(".card-media")?.classList.toggle("hidden", hideImages);
    });
  };

  const setUrlSavedExpanded = (open: boolean) => {
    savedUrlBox.classList.toggle("hidden", !open);
    urlSavedFoldBtn.classList.toggle("open", open);
    urlSavedFoldBtn.setAttribute("aria-expanded", open ? "true" : "false");
  };

  const syncQueryClear = () => {
    queryClearBtn.classList.toggle("hidden", !queryEl.value.trim());
  };

  const syncSearchControls = () => {
    const locked = monitoring;
    searchFields.classList.toggle("is-locked", locked);
    queryEl.disabled = locked;
    queryClearBtn.disabled = locked;
    modeQueryBtn.disabled = locked;
    modeUrlBtn.disabled = locked;
    regionBtn.disabled = locked;
    regionQuery.disabled = locked;
    searchSettingsBtn.disabled = locked;
    saveUrlCheck.disabled = locked;
    hideImagesCheck.disabled = locked;
    urlSavedFoldBtn.disabled = locked;
    startBtn.disabled = searchBusy || locked;
    stopBtn.disabled = searchBusy || !monitoring;
    if (!searchBusy) {
      startBtn.textContent = "Начать поиск";
    }
    catsEl.querySelectorAll("button.chip").forEach((btn) => {
      (btn as HTMLButtonElement).disabled = locked;
    });
    iphoneModelsEl.querySelectorAll("button.iphone-chip").forEach((btn) => {
      (btn as HTMLButtonElement).disabled = locked;
    });
    iphoneModelsAllBtn.disabled = locked;
    iphoneModelsNoneBtn.disabled = locked;
    regionList.querySelectorAll("button[data-slug]").forEach((btn) => {
      (btn as HTMLButtonElement).disabled = locked;
    });
    if (locked) {
      setSettingsOpen(false);
      regionPop.classList.remove("open");
    }
  };

  const setSettingsOpen = (open: boolean) => {
    searchSettingsPop.classList.toggle("open", open);
    searchSettingsBtn.classList.toggle("open", open);
    searchSettingsBtn.setAttribute("aria-expanded", open ? "true" : "false");
  };

  const setAvitoSession = (connected: boolean, label = "") => {
    avitoConnected = connected;
    opts.onAvitoStatus?.(connected, label);
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

  const dialPhone = (phone: string, targetWindow: Window | null = null) => {
    const tel = phone.trim().replace(/[^\d+]/g, "") || phone.trim();
    const href = `tel:${tel}`;
    if (targetWindow && !targetWindow.closed) {
      try {
        targetWindow.location.href = href;
        return;
      } catch {
        /* fallback below */
      }
    }
    const link = document.createElement("a");
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    document.body.appendChild(link);
    link.click();
    link.remove();
  };

  const requestPhone = async (ad: Ad, btn: HTMLButtonElement) => {
    if (!ad.can_call) {
      window.open(itemWebUrl(ad), "_blank", "noopener");
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
    const label = btn.querySelector("span");
    const prev = label?.textContent || btn.textContent || "Позвонить";
    btn.disabled = true;
    if (label) label.textContent = "…";
    else btn.textContent = "…";
    const callTab = window.open("about:blank", "_blank", "noopener,noreferrer");
    try {
      const result = await api.avitoPhone(id);
      if (result.ok && result.phone) {
        phoneCache.set(id, result.phone);
        if (label) label.textContent = result.phone;
        else btn.textContent = result.phone;
        dialPhone(result.phone, callTab);
        return;
      }
      callTab?.close();
      const message = result.error || "Номер недоступен";
      if (result.code === "no_session" || result.code === "not_logged_in" || result.code === "auth_required") {
        setAvitoSession(false);
        openAvitoModal();
      } else {
        window.alert(message);
      }
      if (label) label.textContent = prev;
      else btn.textContent = prev;
    } catch (err) {
      callTab?.close();
      window.alert(err instanceof Error ? err.message : String(err));
      if (label) label.textContent = prev;
      else btn.textContent = prev;
    } finally {
      btn.disabled = false;
    }
  };

  const loadCache = () => {
    try {
      const data = JSON.parse(localStorage.getItem(CACHE_KEY) || "") as {
        region?: Region;
        category?: Category;
        searchMode?: SearchMode;
        savedSearchUrl?: string;
        saveSearchUrl?: boolean;
        hideImages?: boolean;
        iphoneModels?: string[];
      };
      if (data.searchMode === "query" || data.searchMode === "url") {
        searchMode = data.searchMode;
      }
      savedSearchUrl = String(data.savedSearchUrl || "").trim();
      saveUrlCheck.checked = data.saveSearchUrl !== false;
      hideImages = data.hideImages === true;
      hideImagesCheck.checked = hideImages;
      if (Array.isArray(data.iphoneModels)) {
        selectedIphoneModels = normalizeIphoneModels(data.iphoneModels);
      } else {
        selectedIphoneModels = loadIphoneModels();
      }
      renderIphoneModels();
      if (data.region?.slug && data.region.name) {
        region = { slug: String(data.region.slug), name: String(data.region.name) };
        regionLabel.textContent = region.name;
      }
      if (data.category?.id && data.category.name) {
        category = { id: String(data.category.id), name: String(data.category.name) };
      }
      updateSettingsBtnState();
      applyHideImages();
    } catch {
      /* empty */
    }
  };

  const saveCache = () => {
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify({
        region,
        category,
        searchMode,
        savedSearchUrl: saveUrlCheck.checked ? savedSearchUrl : "",
        saveSearchUrl: saveUrlCheck.checked,
        hideImages,
        iphoneModels: selectedIphoneModels,
      }));
    } catch {
      /* empty */
    }
  };

  const addedTimeText = (ad: Ad): string => {
    if (ad.ts) return formatAddedAt(ad.ts, regionTimezone(region.slug));
    return ad.published || "";
  };

  const refreshCardTimes = () => {
    const tz = regionTimezone(region.slug);
    feed.querySelectorAll<HTMLElement>(".card-meta[data-ts]").forEach((el) => {
      const ts = Number(el.dataset.ts);
      if (!ts) return;
      const address = el.dataset.address || "";
      const added = formatAddedAt(ts, tz);
      el.textContent = [added, address].filter(Boolean).join(" · ");
    });
  };

  const cardHtml = (ad: Ad): string => {
    const id = String(ad.id);
    const web = itemWebUrl(ad);
    const fav = isFavorite(id);
    const photo = ad.images?.[0];
    const gallery = photo
      ? `<img src="${imgSrc(photo)}" alt="" loading="lazy" />`
      : `<div class="ph">нет фото</div>`;
    const addedText = addedTimeText(ad);
    const meta = [addedText, ad.address].filter(Boolean).join(" · ");
    const metaAttrs = [
      ad.ts ? ` data-ts="${ad.ts}"` : "",
      ad.address ? ` data-address="${escapeHtml(ad.address)}"` : "",
    ].join("");
    const sellerHtml = ad.seller
      ? `<div class="card-seller-row">
          <span class="card-seller">Продавец: ${escapeHtml(ad.seller)}</span>
          <button type="button" class="card-seller-block" data-action="block-seller" aria-label="В чёрный список" title="В чёрный список">
            ${ICON_CLOSE}
          </button>
        </div>`
      : "";
    const mediaHtml = hideImages
      ? ""
      : `<a class="card-media" href="${escapeHtml(web)}" target="_blank" rel="noopener">
        ${gallery}
      </a>`;
    return `<article class="card${fav ? " is-fav" : ""}${hideImages ? " card--no-media" : ""}" data-id="${id}"${ad.seller ? ` data-seller="${escapeHtml(ad.seller)}"` : ""}>
      ${mediaHtml}
      <div class="card-body">
        <div class="card-price-block">
          <div class="card-price">${escapeHtml(displayPrice(ad.price || "—"))}</div>
          <button type="button" class="card-fav${fav ? " on" : ""}" data-action="fav" aria-label="${fav ? "Убрать из избранного" : "В избранное"}" title="${fav ? "Убрать из избранного" : "В избранное"}">
            ${fav ? ICON_STAR : ICON_STAR_OUTLINE}
          </button>
        </div>
        <a class="card-title" href="${escapeHtml(web)}" target="_blank" rel="noopener">${escapeHtml(ad.title || "")}</a>
        ${meta ? `<div class="card-meta"${metaAttrs}>${escapeHtml(meta)}</div>` : ""}
        ${sellerHtml}
        <div class="card-actions">
          <button class="card-btn call${ad.can_call ? "" : " muted"}" type="button" data-action="call">
            ${ICON_PHONE}<span>${ad.can_call ? "Позвонить" : "Открыть"}</span>
          </button>
          <button class="card-btn msg${ad.can_message ? "" : " muted"}" type="button" data-action="msg" ${ad.can_message ? "" : "disabled"}>
            ${ICON_MSG}<span>Написать</span>
          </button>
          <a class="card-btn ghost open" href="${escapeHtml(web)}" target="_blank" rel="noopener" title="Открыть на Avito">
            ${ICON_EXT}<span class="sr-only">Открыть</span>
          </a>
        </div>
      </div>
    </article>`;
  };

  const bindCardActions = (card: HTMLElement, ad: Ad) => {
    const favBtn = card.querySelector('button[data-action="fav"]') as HTMLButtonElement | null;
    favBtn?.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const on = toggleFavorite(ad.id);
      card.classList.toggle("is-fav", on);
      favBtn.classList.toggle("on", on);
      favBtn.innerHTML = on ? ICON_STAR : ICON_STAR_OUTLINE;
      favBtn.title = on ? "Убрать из избранного" : "В избранное";
      favBtn.setAttribute("aria-label", favBtn.title);
    });

    const callBtn = card.querySelector('button[data-action="call"]') as HTMLButtonElement | null;
    callBtn?.addEventListener("click", (ev) => {
      ev.preventDefault();
      void requestPhone(ad, callBtn);
    });

    const msgBtn = card.querySelector('button[data-action="msg"]') as HTMLButtonElement | null;
    msgBtn?.addEventListener("click", (ev) => {
      ev.preventDefault();
      if (!ad.can_message) {
        window.open(itemWebUrl(ad), "_blank", "noopener");
        return;
      }
      openMessenger(ad);
    });

    const blockBtn = card.querySelector('button[data-action="block-seller"]') as HTMLButtonElement | null;
    blockBtn?.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (!ad.seller) return;
      if (addSellerToBlacklist(ad.seller)) {
        void syncSellerBlacklist();
        purgeBlacklistedSellers();
        showToast(`«${ad.seller}» в чёрном списке`, "success");
      }
    });
  };

  const setStatus = (text: string, live: boolean, kind: ToastKind = "info") => {
    if (!text || live) return;
    showToast(text, kind);
  };

  const mount = (ad: Ad, fresh: boolean): HTMLElement | null => {
    const id = String(ad.id);
    if (seen.has(id)) return null;
    if (ad.seller && sellerMatchesBlacklist(ad.seller)) {
      seen.add(id);
      return null;
    }
    seen.add(id);
    const wrap = document.createElement("div");
    wrap.innerHTML = cardHtml(ad);
    const el = wrap.firstElementChild as HTMLElement;
    if (fresh) el.classList.add("fresh");
    bindCardActions(el, ad);
    return el;
  };

  const syncFeedEmpty = () => {
    feed.classList.toggle("feed-empty", seen.size === 0);
  };

  const purgeBlacklistedSellers = () => {
    feed.querySelectorAll<HTMLElement>(".card").forEach((card) => {
      const seller = card.dataset.seller || "";
      if (!seller || !sellerMatchesBlacklist(seller)) return;
      const id = card.dataset.id;
      if (id) seen.delete(id);
      card.remove();
    });
    syncFeedEmpty();
  };

  const addBatch = (ads: Ad[], fresh: boolean) => {
    if (!Array.isArray(ads) || !ads.length) {
      if (monitoring && !seen.size) setStatus("жду объявления", true);
      syncFeedEmpty();
      return;
    }
    feed.querySelector(".empty")?.remove();
    const marker = feed.firstChild;
    const freshAds: Ad[] = [];
    ads.forEach((ad) => {
      const el = mount(ad, fresh);
      if (el) {
        feed.insertBefore(el, marker);
        if (fresh) freshAds.push(ad);
      }
    });
    if (fresh && freshAds.length && opts.isPushEnabled?.()) {
      notifyNewAds(freshAds);
    }
    syncFeedEmpty();
    setStatus("онлайн · " + seen.size, true);
  };

  const clearFeed = (message?: string) => {
    seen.clear();
    feed.innerHTML = `<div class="empty">${message || "Жду новые объявления…"}</div>`;
    syncFeedEmpty();
    if (monitoring) setStatus("онлайн · 0", true);
  };

  const setBusy = (busy: boolean) => {
    searchBusy = busy;
    if (busy) {
      startBtn.textContent = "Запускаю…";
    }
    syncSearchControls();
  };

  const shortUrl = (url: string, max = 56): string => {
    if (url.length <= max) return url;
    return `${url.slice(0, max - 1)}…`;
  };

  const applySavedUrl = () => {
    const has = Boolean(savedSearchUrl) && saveUrlCheck.checked;
    urlSavedFold.classList.toggle("hidden", !has);
    if (!has) {
      setUrlSavedExpanded(false);
      updateCollapsedUrlHint();
      return;
    }
    savedUrlLink.href = savedSearchUrl;
    savedUrlLink.textContent = savedSearchUrl;
    savedUrlLink.title = savedSearchUrl;
    urlSavedPreview.textContent = shortUrl(savedSearchUrl, 42);
    updateCollapsedUrlHint();
  };

  const updateCollapsedUrlHint = () => {
    const collapsed = searchFields.classList.contains("hidden");
    const show = collapsed && searchMode === "url" && Boolean(savedSearchUrl);
    searchFiltersUrl.classList.toggle("hidden", !show);
    if (!show) {
      searchFiltersUrl.textContent = "";
      searchFiltersUrl.removeAttribute("href");
      return;
    }
    searchFiltersUrl.href = savedSearchUrl;
    searchFiltersUrl.textContent = shortUrl(savedSearchUrl);
    searchFiltersUrl.title = savedSearchUrl;
  };

  const updateUrlOptions = () => {
    if (searchMode === "url" && savedSearchUrl && saveUrlCheck.checked && !queryEl.value.trim()) {
      queryEl.value = savedSearchUrl;
    }
    syncQueryClear();
    applySavedUrl();
  };

  const persistSavedUrl = (url: string) => {
    if (searchMode !== "url" || !saveUrlCheck.checked) {
      if (!saveUrlCheck.checked) {
        savedSearchUrl = "";
        applySavedUrl();
        saveCache();
      }
      return;
    }
    savedSearchUrl = url;
    applySavedUrl();
    saveCache();
  };

  const setSearchMode = (mode: SearchMode) => {
    searchMode = mode;
    modeQueryBtn.classList.toggle("active", mode === "query");
    modeUrlBtn.classList.toggle("active", mode === "url");
    regionWrap.classList.toggle("hidden", mode === "url");
    searchSettingsCats.classList.toggle("hidden", mode === "url");
    searchSettingsUrl.classList.toggle("hidden", mode !== "url");
    if (mode !== "url") setUrlSavedExpanded(false);
    queryEl.placeholder = mode === "url"
      ? "https://www.avito.ru/..."
      : "Поиск по объявлениям";
    queryEl.type = "search";
    if (mode === "url") {
      queryEl.setAttribute("inputmode", "url");
      queryEl.setAttribute("autocomplete", "url");
    } else {
      queryEl.removeAttribute("inputmode");
      queryEl.setAttribute("autocomplete", "off");
    }
    syncQueryClear();
    updateUrlOptions();
    updateSettingsBtnState();
    saveCache();
  };

  const setMonitoring = (on: boolean) => {
    monitoring = on;
    syncSearchControls();
  };

  const renderCats = (items?: Category[]) => {
    if (items?.length) categories = items;
    catsEl.innerHTML = categories.map((item) => (
      `<button type="button" class="chip${item.id === category.id ? " active" : ""}" data-id="${escapeHtml(item.id)}" data-name="${escapeHtml(item.name)}">
        <span class="mark">${CHECK}</span>${escapeHtml(item.name)}
      </button>`
    )).join("");
    updateSettingsBtnState();
    syncSearchControls();
  };

  const setSearchPanelVisible = (visible: boolean) => {
    searchFields.classList.toggle("hidden", !visible);
    searchActions.classList.toggle("hidden", !visible);
    searchActionsBlock.classList.toggle("is-collapsed", !visible);
    searchFiltersLabel.classList.toggle("hidden", visible);
    searchBody.classList.toggle("is-collapsed", !visible);
    updateCollapsedUrlHint();
    toggleSearchBtn.classList.toggle("collapsed", !visible);
    toggleSearchBtn.setAttribute("aria-label", visible ? "Скрыть фильтры" : "Показать фильтры");
    toggleSearchBtn.title = visible ? "Скрыть фильтры" : "Показать фильтры";
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

  const startSearch = async () => {
    if (monitoring) {
      setStatus("Сначала остановите текущий поиск", false, "error");
      return;
    }
    const value = queryEl.value.trim();
    if (!value) {
      setStatus(searchMode === "url" ? "вставьте ссылку Avito" : "введите поисковый запрос", false, "error");
      queryEl.focus();
      return;
    }
    if (!selectedIphoneModels.length) {
      setStatus("выберите хотя бы одну модель iPhone", false, "error");
      setSettingsOpen(true);
      return;
    }
    setBusy(true);
    setStatus("готовлю поиск…", false);
    try {
      const data = await api.startSearch(
        searchMode === "url"
          ? { mode: "url", url: value, seller_skip: loadSellerBlacklist(), iphone_models: iphoneModelsToGens(selectedIphoneModels) }
          : {
            mode: "query",
            query: value,
            region: region.slug,
            category: category.id,
            seller_skip: loadSellerBlacklist(),
            iphone_models: iphoneModelsToGens(selectedIphoneModels),
          },
      );
      setMonitoring(true);
      if (data.search_mode === "url" || data.search_mode === "query") {
        setSearchMode(data.search_mode);
      }
      if (data.query) queryEl.value = data.query;
      if (data.search_mode === "url") {
        persistSavedUrl(data.query || value);
      }
      if (data.region) {
        region = data.region;
        regionLabel.textContent = region.name;
        refreshCardTimes();
      }
      if (data.category) {
        category = data.category;
        renderCats();
      }
      saveCache();
      clearFeed();
      setSearchPanelVisible(false);
      setStatus("поиск запущен", true);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err), false, "error");
    } finally {
      setBusy(false);
    }
  };

  const stopSearch = async () => {
    stopBtn.disabled = true;
    try {
      await api.stopSearch();
      setMonitoring(false);
      setStatus("остановлен", false, "info");
    } catch (err) {
      syncSearchControls();
      setStatus(err instanceof Error ? err.message : String(err), false, "error");
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
    void syncSellerBlacklist();
    window.addEventListener(BLACKLIST_EVENT, () => purgeBlacklistedSellers());
    loadCache();
    renderIphoneModels();
    setSearchMode(searchMode);
    renderCats();
    $("search-form").addEventListener("submit", (ev) => { ev.preventDefault(); });
    queryEl.addEventListener("input", syncQueryClear);
    queryClearBtn.addEventListener("click", () => {
      if (monitoring) return;
      queryEl.value = "";
      syncQueryClear();
      queryEl.focus();
    });
    startBtn.addEventListener("click", () => { void startSearch(); });
    modeQueryBtn.addEventListener("click", () => {
      if (monitoring) return;
      setSearchMode("query");
    });
    modeUrlBtn.addEventListener("click", () => {
      if (monitoring) return;
      setSearchMode("url");
    });
    stopBtn.addEventListener("click", () => { void stopSearch(); });
    toggleSearchBtn.addEventListener("click", () => {
      setSearchPanelVisible(searchFields.classList.contains("hidden"));
    });
    loadSearchPanelState();
    hideImagesCheck.addEventListener("change", () => {
      if (monitoring) return;
      hideImages = hideImagesCheck.checked;
      applyHideImages();
      updateSettingsBtnState();
      saveCache();
    });
    saveUrlCheck.addEventListener("change", () => {
      if (monitoring) return;
      updateSettingsBtnState();
      if (!saveUrlCheck.checked) {
        savedSearchUrl = "";
        applySavedUrl();
      } else {
        const current = queryEl.value.trim();
        if (current) persistSavedUrl(current);
      }
      saveCache();
    });
    iphoneModelsEl.addEventListener("click", (ev) => {
      if (monitoring) return;
      const btn = (ev.target as HTMLElement).closest("button.iphone-chip[data-id]") as HTMLButtonElement | null;
      if (!btn) return;
      const id = btn.dataset.id || "";
      if (!DEFAULT_IPHONE_MODELS.includes(id)) return;
      if (selectedIphoneModels.includes(id)) {
        selectedIphoneModels = selectedIphoneModels.filter((item) => item !== id);
      } else {
        selectedIphoneModels = [...selectedIphoneModels, id].sort(
          (a, b) => DEFAULT_IPHONE_MODELS.indexOf(a) - DEFAULT_IPHONE_MODELS.indexOf(b),
        );
      }
      renderIphoneModels();
      persistIphoneModels();
    });
    iphoneModelsAllBtn.addEventListener("click", () => {
      if (monitoring) return;
      setIphoneModels([...DEFAULT_IPHONE_MODELS]);
    });
    iphoneModelsNoneBtn.addEventListener("click", () => {
      if (monitoring) return;
      setIphoneModels([]);
    });
    urlSavedFoldBtn.addEventListener("click", () => {
      if (monitoring) return;
      setUrlSavedExpanded(savedUrlBox.classList.contains("hidden"));
    });
    regionBtn.addEventListener("click", (ev) => {
      if (monitoring) return;
      ev.stopPropagation();
      const open = !regionPop.classList.contains("open");
      regionPop.classList.toggle("open", open);
      if (open) {
        void loadRegions(regionQuery.value);
        regionQuery.focus();
      }
    });
    regionQuery.addEventListener("input", () => { void loadRegions(regionQuery.value); });
    catsEl.addEventListener("click", (ev) => {
      if (monitoring) return;
      const btn = (ev.target as HTMLElement).closest("button.chip") as HTMLButtonElement | null;
      if (!btn) return;
      category = { id: btn.dataset.id || "none", name: btn.dataset.name || "" };
      saveCache();
      renderCats();
    });
    searchSettingsBtn.addEventListener("click", (ev) => {
      if (monitoring) return;
      ev.stopPropagation();
      const open = !searchSettingsPop.classList.contains("open");
      setSettingsOpen(open);
      if (open) regionPop.classList.remove("open");
    });
    searchSettingsPop.addEventListener("click", (ev) => {
      ev.stopPropagation();
    });
    regionList.addEventListener("click", (ev) => {
      if (monitoring) return;
      const btn = (ev.target as HTMLElement).closest("button[data-slug]") as HTMLButtonElement | null;
      if (!btn) return;
      region = { slug: btn.dataset.slug || "all", name: btn.textContent || "" };
      regionLabel.textContent = region.name;
      regionPop.classList.remove("open");
      regionQuery.value = "";
      refreshCardTimes();
      saveCache();
    });
    document.addEventListener("click", (ev) => {
      const target = ev.target as Node;
      const path = ev.composedPath();
      if (!regionPop.contains(target) && target !== regionBtn && !regionBtn.contains(target)) {
        regionPop.classList.remove("open");
      }
      const insideSettings = path.some(
        (node) => node === searchSettingsPop || node === searchSettingsBtn,
      );
      if (!insideSettings) {
        setSettingsOpen(false);
      }
    });
    void refreshAvitoSession();
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
    connectEvents();
    setInterval(() => {
      if (monitoring) void api.ads().then((ads) => addBatch(ads, false)).catch(() => undefined);
    }, 4000);
    setInterval(refreshCardTimes, 30_000);
    try {
      const data = await api.status();
      if (data.running) {
        if (data.search_mode === "url" || data.search_mode === "query") {
          setSearchMode(data.search_mode);
        }
        if (data.query) queryEl.value = data.query;
        if (data.search_mode === "url" && data.query) {
          savedSearchUrl = saveUrlCheck.checked ? data.query : savedSearchUrl;
          applySavedUrl();
        }
        if (data.region?.slug) {
          region = data.region;
          regionLabel.textContent = region.name;
          refreshCardTimes();
        }
        if (data.category?.id) {
          category = data.category;
          renderCats();
        }
        saveCache();
        setMonitoring(true);
        setSearchPanelVisible(false);
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
    openAvito: openAvitoModal,
  };
}
