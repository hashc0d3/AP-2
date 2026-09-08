import { api } from "./api";
import { itemWebUrl } from "./avito-links";
import { ICON_CLOSE, ICON_EXT, ICON_PHONE, ICON_STAR, ICON_STAR_OUTLINE } from "./card-icons";
import { isFavorite, toggleFavorite } from "./favorites";
import { notifyNewAds, isIosDevice } from "./push-notify";
import { collapseDescText, displayPrice, escapeHtml, formatAddedAt, imgSrc } from "./format";
import { openImageLightbox } from "./image-lightbox";
import { regionTimezone } from "./region-timezones";
import {
  addSavedUrl,
  findSavedUrlByUrl,
  MAX_SAVED_URLS,
  migrateLegacySavedUrl,
  removeSavedUrl,
  type SavedUrl,
  updateSavedUrl,
} from "./saved-urls";
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
  iphoneModelsSummary,
  iphoneModelsToPayload,
  isDefaultIphoneSelection,
  loadIphoneModels,
  normalizeIphoneModels,
  saveIphoneModels,
} from "./iphone-models";
import type { Ad, Category, Region, SearchMode } from "./types";

const CACHE_KEY = "parser1.search";

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
  closeUserMenu?: () => void;
} = {}): {
  show: () => void;
  hide: () => void;
  openAvito: () => void;
  closeFilters: () => void;
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
  const filtersSections = $("filters-sections");
  const iphoneModelsEl = $("iphone-models");
  const iphoneModelsWrap = $("iphone-models-wrap");
  const iphoneModelsTrigger = $("iphone-models-trigger") as HTMLButtonElement;
  const iphoneModelsPanel = $("iphone-models-panel");
  const iphoneModelsLabel = $("iphone-models-label");
  const iphoneModelsAllBtn = $("iphone-models-all") as HTMLButtonElement;
  const iphoneModelsNoneBtn = $("iphone-models-none") as HTMLButtonElement;
  const searchSettingsCats = $("search-settings-cats");
  const searchSettingsIphone = $("search-settings-iphone");
  const searchSettingsUrl = $("search-settings-url");
  const hideImagesCheck = input("hide-images");
  const urlSearchCombo = $("url-search-combo");
  const urlSearchDropdown = $("url-search-dropdown");
  const urlSearchDropdownList = $("url-search-dropdown-list");
  const savedUrlsManage = $("saved-urls-manage");
  const savedUrlsList = $("saved-urls-list");
  const savedUrlAddBtn = $("saved-url-add-btn") as HTMLButtonElement;
  const savedUrlAddForm = $("saved-url-add-form");
  const savedUrlAddNameInput = input("saved-url-add-name");
  const savedUrlAddUrlInput = input("saved-url-add-url");
  const savedUrlAddConfirmBtn = $("saved-url-add-confirm") as HTMLButtonElement;
  const savedUrlAddCancelBtn = $("saved-url-add-cancel") as HTMLButtonElement;
  const savedUrlsLimit = $("saved-urls-limit");
  const filtersDrawerBack = $("filters-drawer-back");
  const filtersDrawer = $("filters-drawer");
  const filtersOpenBtn = $("filters-open-btn") as HTMLButtonElement;
  const filtersCloseBtn = $("filters-close") as HTMLButtonElement;
  const searchStatusPill = $("search-status-pill");
  const searchStatusLabel = $("search-status-label");
  const regionBtn = $("region-btn") as HTMLButtonElement;
  const regionLabel = $("region-label");
  const regionPop = $("region-pop");
  const regionQuery = input("region-query");
  const regionList = $("region-list");
  const regionWrap = $("region-wrap");
  const modeQueryBtn = $("mode-query") as HTMLButtonElement;
  const modeUrlBtn = $("mode-url") as HTMLButtonElement;
  const searchBody = $("search-body");
  const searchFields = $("search-fields");
  const searchActionsBlock = $("search-actions-block");
  const searchActions = $("search-actions");
  const startBtn = $("start") as HTMLButtonElement;
  const stopBtn = $("stop") as HTMLButtonElement;
  const categoryWrap = $("category-wrap");
  const categoryTrigger = $("category-trigger") as HTMLButtonElement;
  const categoryPanel = $("category-panel");
  const categoryLabel = $("category-label");
  const catsEl = $("cats");
  const seen = new Set<string>();
  let region: Region = { slug: "all", name: "Вся Россия" };
  const DEFAULT_CATEGORY_ID = "apple_phones";
  let category: Category = { id: DEFAULT_CATEGORY_ID, name: "Смартфоны Apple" };
  let searchMode: SearchMode = "query";
  let savedUrls: SavedUrl[] = [];
  let selectedSavedUrlId: string | null = null;
  let hideImages = false;
  let categories: Category[] = [
    { id: DEFAULT_CATEGORY_ID, name: "Смартфоны Apple" },
    { id: "game_consoles", name: "Игровые приставки" },
    { id: "laptops_apple", name: "Ноутбуки Apple" },
    { id: "tablets", name: "Планшеты Apple" },
  ];
  let monitoring = false;
  let searchBusy = false;
  let started = false;
  let events: EventSource | null = null;
  let avitoConnected = false;
  let selectedIphoneModels = loadIphoneModels();
  const phoneCache = new Map<string, string>();

  const isIphoneCategorySelected = () => searchMode === "query" && category.id === DEFAULT_CATEGORY_ID;

  const setCategoryPanelOpen = (open: boolean) => {
    categoryPanel.classList.toggle("hidden", !open);
    categoryPanel.classList.toggle("open", open);
    categoryTrigger.setAttribute("aria-expanded", open ? "true" : "false");
    categoryWrap.classList.toggle("open", open);
  };

  const setIphoneModelsPanelOpen = (open: boolean) => {
    iphoneModelsPanel.classList.toggle("hidden", !open);
    iphoneModelsPanel.classList.toggle("open", open);
    iphoneModelsTrigger.setAttribute("aria-expanded", open ? "true" : "false");
    iphoneModelsWrap.classList.toggle("open", open);
  };

  const syncIphoneSectionVisibility = () => {
    const visible = isIphoneCategorySelected();
    searchSettingsIphone.classList.toggle("hidden", !visible);
    if (!visible) {
      setIphoneModelsPanelOpen(false);
    }
    updateSettingsBtnState();
  };

  const updateSettingsBtnState = () => {
    const urlActive = searchMode === "url" && savedUrls.length > 0;
    const iphoneFilterActive = isIphoneCategorySelected() && !isDefaultIphoneSelection(selectedIphoneModels);
    const active = category.id !== DEFAULT_CATEGORY_ID || urlActive || hideImages || iphoneFilterActive;
    filtersOpenBtn.classList.toggle("active", active);
  };

  const setUrlSearchDropdownOpen = (open: boolean) => {
    if (searchMode !== "url" || !savedUrls.length) {
      urlSearchDropdown.classList.add("hidden");
      return;
    }
    urlSearchDropdown.classList.toggle("hidden", !open);
  };

  const setFilterSectionOpen = (section: HTMLElement, open: boolean) => {
    const toggle = section.querySelector(".filter-section-toggle") as HTMLButtonElement | null;
    const body = section.querySelector(".filter-section-body") as HTMLElement | null;
    section.classList.toggle("open", open);
    body?.classList.toggle("hidden", !open);
    toggle?.setAttribute("aria-expanded", open ? "true" : "false");
  };

  const bindFilterSectionToggle = (toggleId: string, sectionId: string) => {
    const toggle = $(toggleId) as HTMLButtonElement;
    const section = $(sectionId);
    toggle.addEventListener("click", () => {
      if (monitoring) return;
      setFilterSectionOpen(section, !section.classList.contains("open"));
    });
  };

  const syncSearchStatus = () => {
    searchStatusPill.classList.toggle("is-active", monitoring);
    searchStatusPill.classList.toggle("is-idle", !monitoring);
    searchStatusLabel.textContent = monitoring ? "Поиск активен" : "Поиск остановлен";
  };

  const setFiltersDrawerOpen = (open: boolean) => {
    filtersDrawerBack.classList.toggle("hidden", !open);
    requestAnimationFrame(() => filtersDrawerBack.classList.toggle("open", open));
    filtersDrawerBack.setAttribute("aria-hidden", open ? "false" : "true");
    filtersOpenBtn.setAttribute("aria-expanded", open ? "true" : "false");
    document.body.classList.toggle("filters-drawer-open", open);
    if (open) {
      opts.closeUserMenu?.();
    }
    if (!open) {
      regionPop.classList.remove("open");
      setCategoryPanelOpen(false);
      setIphoneModelsPanelOpen(false);
      setUrlSearchDropdownOpen(false);
    }
  };

  const persistIphoneModels = () => {
    saveIphoneModels(selectedIphoneModels);
    updateSettingsBtnState();
  };

  const renderIphoneModels = () => {
    const selected = new Set(selectedIphoneModels);
    iphoneModelsEl.innerHTML = IPHONE_MODELS.map((item) => (
      `<label class="multi-select-option">
        <span class="styled-check">
          <input type="checkbox" value="${escapeHtml(item.id)}"${selected.has(item.id) ? " checked" : ""} />
          <span class="styled-check-box" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
              <path d="M5 12.5 10 17.5 19 7"></path>
            </svg>
          </span>
        </span>
        <span class="multi-select-option-label">${escapeHtml(item.label)}</span>
      </label>`
    )).join("");
    iphoneModelsLabel.textContent = iphoneModelsSummary(selectedIphoneModels);
    updateSettingsBtnState();
  };

  const setIphoneModels = (models: string[]) => {
    selectedIphoneModels = normalizeIphoneModels(models, { allowEmpty: true });
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

  const syncQueryClear = () => {
    queryClearBtn.classList.toggle("hidden", !queryEl.value.trim());
  };

  const syncQueryFieldVisibility = () => {
    const show = searchMode === "url";
    searchFieldWrap.classList.toggle("hidden", !show);
    if (!show) {
      queryEl.value = "";
      syncQueryClear();
    }
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
    hideImagesCheck.disabled = locked;
    savedUrlAddBtn.disabled = locked || savedUrls.length >= MAX_SAVED_URLS;
    savedUrlAddConfirmBtn.disabled = locked;
    savedUrlAddCancelBtn.disabled = locked;
    savedUrlAddNameInput.disabled = locked;
    savedUrlAddUrlInput.disabled = locked;
    startBtn.disabled = searchBusy || locked;
    stopBtn.disabled = searchBusy || !monitoring;
    if (!searchBusy) {
      startBtn.textContent = "Начать поиск";
    }
    categoryTrigger.disabled = locked;
    iphoneModelsTrigger.disabled = locked;
    iphoneModelsEl.querySelectorAll(".styled-check input").forEach((input) => {
      (input as HTMLInputElement).disabled = locked;
    });
    iphoneModelsAllBtn.disabled = locked;
    iphoneModelsNoneBtn.disabled = locked;
    savedUrlsList.querySelectorAll(".saved-url-item-name, .saved-url-item-url, .saved-url-item-delete").forEach((el) => {
      (el as HTMLButtonElement | HTMLInputElement).disabled = locked;
    });
    filtersSections.querySelectorAll(".filter-section-toggle").forEach((btn) => {
      (btn as HTMLButtonElement).disabled = locked;
    });
    regionList.querySelectorAll("button[data-slug]").forEach((btn) => {
      (btn as HTMLButtonElement).disabled = locked;
    });
    if (locked) {
      regionPop.classList.remove("open");
      setCategoryPanelOpen(false);
      setIphoneModelsPanelOpen(false);
    }
  };

  const openFilterSection = (sectionId: string) => {
    setFilterSectionOpen($(sectionId), true);
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
    const subtitle = $("avito-modal-subtitle");
    const androidSteps = $("avito-setup-android");
    const iosSteps = $("avito-setup-ios");
    const ios = isIosDevice();
    if (subtitle) {
      subtitle.textContent = ios
        ? "На iPhone cookies Avito нужно экспортировать из Safari — PWA их не видит сам."
        : "Подключение через Kiwi Browser для кнопки «Позвонить».";
    }
    androidSteps?.classList.toggle("hidden", ios);
    iosSteps?.classList.toggle("hidden", !ios);
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
        selectedSavedUrlId?: string;
        hideImages?: boolean;
      };
      if (data.searchMode === "query" || data.searchMode === "url") {
        searchMode = data.searchMode;
      }
      savedUrls = migrateLegacySavedUrl(String(data.savedSearchUrl || "").trim());
      selectedSavedUrlId = data.selectedSavedUrlId || null;
      if (selectedSavedUrlId && !savedUrls.some((item) => item.id === selectedSavedUrlId)) {
        selectedSavedUrlId = null;
      }
      hideImages = data.hideImages === true;
      hideImagesCheck.checked = hideImages;
      selectedIphoneModels = loadIphoneModels();
      renderIphoneModels();
      if (data.region?.slug && data.region.name) {
        region = { slug: String(data.region.slug), name: String(data.region.name) };
        regionLabel.textContent = region.name;
      }
      if (data.category?.id) {
        category = resolveCategory(String(data.category.id));
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
        selectedSavedUrlId,
        hideImages,
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
    const description = (ad.description || "").trim();
    const descriptionHtml = description
      ? `<div class="card-desc" data-action="toggle-desc" role="button" tabindex="0" aria-expanded="false"><span class="card-desc-text">${escapeHtml(collapseDescText(description))}</span></div>`
      : "";
    const mediaHtml = hideImages
      ? ""
      : photo
        ? `<button type="button" class="card-media" data-action="zoom-photo" aria-label="Открыть фото">
        ${gallery}
      </button>`
        : `<div class="card-media">${gallery}</div>`;
    return `<article class="card${fav ? " is-fav" : ""}${hideImages ? " card--no-media" : ""}" data-id="${id}"${ad.seller ? ` data-seller="${escapeHtml(ad.seller)}"` : ""}>
      ${mediaHtml}
      <div class="card-body">
        <div class="card-price-block">
          <div class="card-price">${escapeHtml(displayPrice(ad.price || "—"))}</div>
          <div class="card-toolbar">
            <a class="card-open" href="${escapeHtml(web)}" target="_blank" rel="noopener" title="Открыть на Avito" aria-label="Открыть на Avito">
              ${ICON_EXT}
            </a>
            <button type="button" class="card-fav${fav ? " on" : ""}" data-action="fav" aria-label="${fav ? "Убрать из избранного" : "В избранное"}" title="${fav ? "Убрать из избранного" : "В избранное"}">
              ${fav ? ICON_STAR : ICON_STAR_OUTLINE}
            </button>
          </div>
        </div>
        <a class="card-title" href="${escapeHtml(web)}" target="_blank" rel="noopener">${escapeHtml(ad.title || "")}</a>
        ${meta ? `<div class="card-meta"${metaAttrs}>${escapeHtml(meta)}</div>` : ""}
        ${sellerHtml}
        <div class="card-actions">
          ${ad.can_call
            ? `<button class="card-btn call" type="button" data-action="call">${ICON_PHONE}<span>Позвонить</span></button>`
            : `<button class="card-btn open-ad" type="button" data-action="open-ad">${ICON_EXT}<span>Открыть объявление</span></button>`}
        </div>
        ${descriptionHtml}
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

    const openAdBtn = card.querySelector('button[data-action="open-ad"]') as HTMLButtonElement | null;
    openAdBtn?.addEventListener("click", (ev) => {
      ev.preventDefault();
      window.open(itemWebUrl(ad), "_blank", "noopener");
    });

    const photoBtn = card.querySelector('button[data-action="zoom-photo"]') as HTMLButtonElement | null;
    photoBtn?.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const urls = ad.images?.filter(Boolean) || [];
      if (urls.length) openImageLightbox(urls);
    });

    const descBtn = card.querySelector('[data-action="toggle-desc"]') as HTMLElement | null;
    const descText = descBtn?.querySelector(".card-desc-text") as HTMLElement | null;
    const fullDesc = (ad.description || "").trim();
    if (descBtn && descText && fullDesc) {
      const toggleDesc = () => {
        const open = descBtn.classList.toggle("open");
        descText.textContent = open ? fullDesc : collapseDescText(fullDesc);
        descBtn.setAttribute("aria-expanded", open ? "true" : "false");
      };
      descBtn.addEventListener("click", (ev) => {
        ev.preventDefault();
        toggleDesc();
      });
      descBtn.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          toggleDesc();
        }
      });
    }

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

  const selectedSavedUrl = (): SavedUrl | undefined =>
    savedUrls.find((item) => item.id === selectedSavedUrlId);

  const urlDropdownFilter = (): string => {
    const value = queryEl.value.trim();
    if (!value || value.startsWith("http://") || value.startsWith("https://")) return "";
    return value.toLowerCase();
  };

  const selectSavedUrl = (id: string | null, fillInput = true) => {
    selectedSavedUrlId = id;
    const item = selectedSavedUrl();
    if (fillInput && item) {
      queryEl.value = item.url;
      syncQueryClear();
    }
    renderSavedUrls();
    saveCache();
  };

  const syncSavedUrlFromInput = () => {
    const value = queryEl.value.trim();
    if (!value) {
      if (selectedSavedUrlId) selectSavedUrl(null, false);
      return;
    }
    const match = findSavedUrlByUrl(savedUrls, value);
    if (match) {
      if (selectedSavedUrlId !== match.id) selectSavedUrl(match.id, false);
    } else if (selectedSavedUrlId) {
      selectSavedUrl(null, false);
    }
  };

  const setSavedUrlAddFormOpen = (open: boolean) => {
    savedUrlAddForm.classList.toggle("hidden", !open);
    savedUrlAddBtn.classList.toggle("hidden", open);
    if (!open) {
      savedUrlAddNameInput.value = "";
      savedUrlAddUrlInput.value = "";
    } else {
      savedUrlAddNameInput.focus();
    }
  };

  const renderUrlSearchDropdown = () => {
    const filter = urlDropdownFilter();
    const items = savedUrls.filter((item) => (
      !filter || item.name.toLowerCase().includes(filter)
    ));
    if (!items.length) {
      urlSearchDropdownList.innerHTML = '<p class="url-search-dropdown-empty">Нет сохранённых ссылок</p>';
      return;
    }
    urlSearchDropdownList.innerHTML = items.map((item) => (
      `<button type="button" class="url-search-dropdown-item${item.id === selectedSavedUrlId ? " active" : ""}" data-id="${escapeHtml(item.id)}">
        ${escapeHtml(item.name)}
      </button>`
    )).join("");
  };

  const renderSavedUrls = () => {
    const showUrlUi = searchMode === "url";
    savedUrlsManage.classList.toggle("hidden", !showUrlUi);
    savedUrlsLimit.classList.toggle("hidden", savedUrls.length < MAX_SAVED_URLS);
    savedUrlAddBtn.disabled = monitoring || savedUrls.length >= MAX_SAVED_URLS;
    if (savedUrls.length >= MAX_SAVED_URLS) setSavedUrlAddFormOpen(false);
    renderUrlSearchDropdown();

    savedUrlsList.innerHTML = savedUrls.map((item) => (
      `<article class="saved-url-item" data-id="${escapeHtml(item.id)}">
        <input type="text" class="field saved-url-item-name" value="${escapeHtml(item.name)}" maxlength="64" placeholder="Название" aria-label="Название" />
        <input type="url" class="field saved-url-item-url" value="${escapeHtml(item.url)}" placeholder="https://www.avito.ru/..." aria-label="Ссылка" />
        <button type="button" class="saved-url-item-delete" data-action="delete-saved-url" aria-label="Удалить" title="Удалить">×</button>
      </article>`
    )).join("");
  };

  const addSavedUrlFromForm = (): boolean => {
    const name = savedUrlAddNameInput.value.trim();
    const url = savedUrlAddUrlInput.value.trim();
    if (!name || !url) {
      setStatus("укажите название и ссылку", false, "error");
      return false;
    }
    const result = addSavedUrl(savedUrls, name, url);
    if (result.limitReached) {
      setStatus(`Максимум ${MAX_SAVED_URLS} ссылок`, false, "error");
      return false;
    }
    if (!result.item.id) {
      setStatus("укажите название и ссылку", false, "error");
      return false;
    }
    savedUrls = result.urls;
    setSavedUrlAddFormOpen(false);
    renderSavedUrls();
    saveCache();
    updateSettingsBtnState();
    showToast(result.added ? `Ссылка «${result.item.name}» добавлена` : `Ссылка «${result.item.name}» обновлена`, "success");
    return true;
  };

  const applySavedUrl = () => {
    renderSavedUrls();
  };

  const updateUrlOptions = () => {
    if (searchMode === "url" && selectedSavedUrlId) {
      const item = selectedSavedUrl();
      if (item && !queryEl.value.trim()) {
        queryEl.value = item.url;
      }
    }
    syncQueryClear();
    applySavedUrl();
  };

  const setSearchMode = (mode: SearchMode) => {
    searchMode = mode;
    modeQueryBtn.classList.toggle("active", mode === "query");
    modeUrlBtn.classList.toggle("active", mode === "url");
    regionWrap.classList.toggle("hidden", mode === "url");
    searchSettingsCats.classList.toggle("hidden", mode === "url");
    searchSettingsUrl.classList.toggle("hidden", mode !== "url");
    queryEl.placeholder = mode === "url"
      ? "Вставьте ссылку или выберите из списка"
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
    syncQueryFieldVisibility();
    syncIphoneSectionVisibility();
    if (mode === "url") {
      openFilterSection("search-settings-url");
    }
    updateSettingsBtnState();
    syncSearchControls();
    saveCache();
  };

  const setMonitoring = (on: boolean) => {
    monitoring = on;
    syncSearchControls();
    syncSearchStatus();
  };

  const resolveCategory = (id: string): Category => {
    const legacy = id === "none" || id === "electronics" || id === "phones";
    const normalizedId = legacy ? DEFAULT_CATEGORY_ID : id;
    const known = categories.find((item) => item.id === normalizedId);
    if (known) return known;
    return categories[0] ?? { id: DEFAULT_CATEGORY_ID, name: "Смартфоны Apple" };
  };

  const renderCats = (items?: Category[]) => {
    if (items?.length) categories = items;
    category = resolveCategory(category.id);
    catsEl.innerHTML = categories.map((item) => (
      `<button type="button" class="multi-select-choice${item.id === category.id ? " active" : ""}" data-id="${escapeHtml(item.id)}" data-name="${escapeHtml(item.name)}" role="option" aria-selected="${item.id === category.id ? "true" : "false"}">
        <span class="multi-select-choice-label">${escapeHtml(item.name)}</span>
        <svg class="multi-select-choice-mark" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" aria-hidden="true">
          <path d="M5 12.5 10 17.5 19 7"></path>
        </svg>
      </button>`
    )).join("");
    categoryLabel.textContent = category.name;
    syncIphoneSectionVisibility();
    updateSettingsBtnState();
    syncSearchControls();
  };

  const loadSearchPanelState = () => {
    setFiltersDrawerOpen(false);
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
    if (searchMode === "url" && !value) {
      setStatus("вставьте ссылку Avito", false, "error");
      queryEl.focus();
      return;
    }
    if (isIphoneCategorySelected() && !selectedIphoneModels.length) {
      setStatus("выберите хотя бы одну модель iPhone", false, "error");
      openFilterSection("search-settings-iphone");
      return;
    }
    setBusy(true);
    setStatus("готовлю поиск…", false);
    try {
      const iphonePayload = isIphoneCategorySelected() ? iphoneModelsToPayload(selectedIphoneModels) : undefined;
      const data = await api.startSearch(
        searchMode === "url"
          ? { mode: "url", url: value, seller_skip: loadSellerBlacklist(), iphone_models: iphonePayload }
          : {
            mode: "query",
            query: "",
            region: region.slug,
            category: category.id,
            seller_skip: loadSellerBlacklist(),
            iphone_models: iphonePayload,
          },
      );
      setMonitoring(true);
      if (data.search_mode === "url" || data.search_mode === "query") {
        setSearchMode(data.search_mode);
      }
      if (data.search_mode === "url" && data.query) {
        queryEl.value = data.query;
        syncSavedUrlFromInput();
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
      setFiltersDrawerOpen(false);
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
    queryEl.addEventListener("input", () => {
      syncQueryClear();
      if (searchMode === "url") {
        syncSavedUrlFromInput();
        renderUrlSearchDropdown();
        if (queryEl.value.trim().startsWith("http")) {
          setUrlSearchDropdownOpen(false);
        } else if (document.activeElement === queryEl) {
          setUrlSearchDropdownOpen(true);
        }
      }
    });
    queryEl.addEventListener("focus", () => {
      if (monitoring || searchMode !== "url" || !savedUrls.length) return;
      renderUrlSearchDropdown();
      setUrlSearchDropdownOpen(true);
    });
    queryEl.addEventListener("blur", () => {
      window.setTimeout(() => {
        if (!urlSearchCombo.contains(document.activeElement)) {
          setUrlSearchDropdownOpen(false);
        }
      }, 120);
    });
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
    filtersOpenBtn.addEventListener("click", () => {
      setFiltersDrawerOpen(!filtersDrawerBack.classList.contains("open"));
    });
    filtersCloseBtn.addEventListener("click", () => setFiltersDrawerOpen(false));
    filtersDrawerBack.addEventListener("click", (ev) => {
      if (ev.target === filtersDrawerBack) setFiltersDrawerOpen(false);
    });
    filtersDrawer.addEventListener("click", (ev) => ev.stopPropagation());
    loadSearchPanelState();
    syncSearchStatus();
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && filtersDrawerBack.classList.contains("open")) {
        setFiltersDrawerOpen(false);
      }
    });
    hideImagesCheck.addEventListener("change", () => {
      if (monitoring) return;
      hideImages = hideImagesCheck.checked;
      applyHideImages();
      updateSettingsBtnState();
      saveCache();
    });
    savedUrlAddBtn.addEventListener("click", () => {
      if (monitoring || savedUrls.length >= MAX_SAVED_URLS) return;
      setSavedUrlAddFormOpen(true);
    });
    savedUrlAddCancelBtn.addEventListener("click", () => {
      if (monitoring) return;
      setSavedUrlAddFormOpen(false);
    });
    savedUrlAddConfirmBtn.addEventListener("click", () => {
      if (monitoring) return;
      addSavedUrlFromForm();
    });
    savedUrlAddForm.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        if (!monitoring) addSavedUrlFromForm();
      }
    });
    urlSearchDropdownList.addEventListener("mousedown", (ev) => {
      ev.preventDefault();
    });
    urlSearchDropdownList.addEventListener("click", (ev) => {
      if (monitoring) return;
      const btn = (ev.target as HTMLElement).closest(".url-search-dropdown-item") as HTMLButtonElement | null;
      if (!btn?.dataset.id) return;
      selectSavedUrl(btn.dataset.id);
      setUrlSearchDropdownOpen(false);
      queryEl.focus();
    });
    savedUrlsList.addEventListener("click", (ev) => {
      if (monitoring) return;
      const deleteBtn = (ev.target as HTMLElement).closest('[data-action="delete-saved-url"]') as HTMLButtonElement | null;
      if (!deleteBtn) return;
      const item = deleteBtn.closest(".saved-url-item") as HTMLElement | null;
      if (!item?.dataset.id) return;
      savedUrls = removeSavedUrl(savedUrls, item.dataset.id);
      if (selectedSavedUrlId === item.dataset.id) {
        selectedSavedUrlId = null;
        if (findSavedUrlByUrl(savedUrls, queryEl.value.trim())) {
          syncSavedUrlFromInput();
        }
      }
      renderSavedUrls();
      saveCache();
      updateSettingsBtnState();
    });
    savedUrlsList.addEventListener("change", (ev) => {
      if (monitoring) return;
      const input = ev.target as HTMLInputElement;
      if (!input.classList.contains("saved-url-item-name") && !input.classList.contains("saved-url-item-url")) return;
      const item = input.closest(".saved-url-item") as HTMLElement | null;
      if (!item?.dataset.id) return;
      const nameInput = item.querySelector(".saved-url-item-name") as HTMLInputElement;
      const urlInput = item.querySelector(".saved-url-item-url") as HTMLInputElement;
      const next = updateSavedUrl(savedUrls, item.dataset.id, nameInput.value, urlInput.value);
      if (next === savedUrls) return;
      savedUrls = next;
      if (selectedSavedUrlId === item.dataset.id) {
        queryEl.value = urlInput.value.trim();
        syncQueryClear();
      }
      renderSavedUrls();
      saveCache();
    });
    iphoneModelsTrigger.addEventListener("click", (ev) => {
      if (monitoring) return;
      ev.stopPropagation();
      const open = !iphoneModelsPanel.classList.contains("open");
      if (open) setCategoryPanelOpen(false);
      setIphoneModelsPanelOpen(open);
    });
    iphoneModelsEl.addEventListener("change", (ev) => {
      if (monitoring) return;
      const input = ev.target as HTMLInputElement;
      if (input.type !== "checkbox") return;
      const id = input.value;
      if (!DEFAULT_IPHONE_MODELS.includes(id)) return;
      if (input.checked) {
        if (!selectedIphoneModels.includes(id)) {
          selectedIphoneModels = [...selectedIphoneModels, id].sort(
            (a, b) => DEFAULT_IPHONE_MODELS.indexOf(a) - DEFAULT_IPHONE_MODELS.indexOf(b),
          );
        }
      } else {
        selectedIphoneModels = selectedIphoneModels.filter((item) => item !== id);
      }
      iphoneModelsLabel.textContent = iphoneModelsSummary(selectedIphoneModels);
      persistIphoneModels();
      updateSettingsBtnState();
    });
    iphoneModelsAllBtn.addEventListener("click", (ev) => {
      ev.preventDefault();
      if (monitoring) return;
      setIphoneModels([...DEFAULT_IPHONE_MODELS]);
    });
    iphoneModelsNoneBtn.addEventListener("click", (ev) => {
      ev.preventDefault();
      if (monitoring) return;
      setIphoneModels([]);
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
    bindFilterSectionToggle("filter-toggle-cats", "search-settings-cats");
    bindFilterSectionToggle("filter-toggle-iphone", "search-settings-iphone");
    bindFilterSectionToggle("filter-toggle-url", "search-settings-url");
    bindFilterSectionToggle("filter-toggle-display", "search-settings-display");
    categoryTrigger.addEventListener("click", (ev) => {
      if (monitoring) return;
      ev.stopPropagation();
      const open = !categoryPanel.classList.contains("open");
      if (open) setIphoneModelsPanelOpen(false);
      setCategoryPanelOpen(open);
    });
    catsEl.addEventListener("click", (ev) => {
      if (monitoring) return;
      const btn = (ev.target as HTMLElement).closest("button.multi-select-choice") as HTMLButtonElement | null;
      if (!btn) return;
      category = { id: btn.dataset.id || DEFAULT_CATEGORY_ID, name: btn.dataset.name || "" };
      queryEl.value = "";
      syncQueryClear();
      saveCache();
      renderCats();
      setCategoryPanelOpen(false);
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
      if (!regionPop.contains(target) && target !== regionBtn && !regionBtn.contains(target)) {
        regionPop.classList.remove("open");
      }
      if (!categoryWrap.contains(target)) {
        setCategoryPanelOpen(false);
      }
      if (!iphoneModelsWrap.contains(target)) {
        setIphoneModelsPanelOpen(false);
      }
      if (!urlSearchCombo.contains(target)) {
        setUrlSearchDropdownOpen(false);
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
    setInterval(refreshCardTimes, 1000);
    try {
      const data = await api.status();
      if (data.running) {
        if (data.search_mode === "url" || data.search_mode === "query") {
          setSearchMode(data.search_mode);
        }
        if (data.query && data.search_mode === "url") {
          queryEl.value = data.query;
          syncSavedUrlFromInput();
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
        setFiltersDrawerOpen(false);
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
    closeFilters: () => setFiltersDrawerOpen(false),
  };
}
