/**
 * Экран поиска: форма, панель фильтров и запуск мониторинга.
 *
 * Модуль связывает части интерфейса и владеет состоянием формы (регион,
 * категория, режим, выбранные модели). Всё, что можно было выделить —
 * карточки, лента, сессия Avito, закладки ссылок, — живёт в отдельных
 * модулях; здесь остаётся то, что действительно про форму поиска.
 *
 * Главное правило экрана: пока идёт поиск, форма заблокирована. Иначе
 * пользователь менял бы условия, не видя, по каким на самом деле идёт
 * опрос. Поэтому обработчики ввода начинаются с проверки `monitoring`.
 */

import { api } from "./api";
import { createAdCardRenderer } from "./ad-card";
import { createAdFeed } from "./ad-feed";
import { createAvitoSession } from "./avito-session";
import {
  button,
  el,
  input,
  isDropdownOpen,
  setDisabledAll,
  setDropdownOpen,
  setFilterSectionOpen,
} from "./dom";
import { escapeHtml } from "./format";
import { ICON_CHECK } from "./card-icons";
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
import { createSavedUrlsPanel } from "./saved-urls-panel";
import { loadSearchSettings, saveSearchSettings } from "./search-settings";
import {
  addSellerToBlacklist,
  BLACKLIST_EVENT,
  loadSellerBlacklist,
  syncSellerBlacklist,
} from "./seller-blacklist";
import { showToast, type ToastKind } from "./toast";
import type { Category, Region, SearchMode, SearchState } from "./types";

/** Фильтр моделей осмыслен только для смартфонов Apple. */
const IPHONE_CATEGORY_ID = "apple_phones";
const ALL_CATEGORY_ID = "all";

const DEFAULT_REGION: Region = { slug: "all", name: "Вся Россия" };
const DEFAULT_CATEGORY: Category = { id: IPHONE_CATEGORY_ID, name: "Смартфоны Apple" };
const ALL_CATEGORY: Category = { id: ALL_CATEGORY_ID, name: "Все категории" };

/**
 * Категории до ответа сервера.
 *
 * Список приходит из `/api/categories`, но форма должна быть собрана сразу:
 * иначе при медленной сети пользователь видит пустой выпадающий список.
 */
const FALLBACK_CATEGORIES: Category[] = [
  ALL_CATEGORY,
  DEFAULT_CATEGORY,
  { id: "game_consoles", name: "Игровые приставки" },
  { id: "laptops_apple", name: "Ноутбуки Apple" },
  { id: "tablets", name: "Планшеты Apple" },
];

/** Идентификаторы категорий, которые остались в настройках старых версий. */
const LEGACY_CATEGORY_IDS = new Set(["none", "electronics", "phones"]);

const FILTER_SECTIONS: [toggleId: string, sectionId: string][] = [
  ["filter-toggle-cats", "search-settings-cats"],
  ["filter-toggle-iphone", "search-settings-iphone"],
  ["filter-toggle-url", "search-settings-url"],
  ["filter-toggle-display", "search-settings-display"],
];

export type Monitor = {
  show: () => void;
  hide: () => void;
  openAvito: () => void;
  closeFilters: () => void;
};

export function mountMonitor(
  opts: {
    isPushEnabled?: () => boolean;
    onAvitoStatus?: (connected: boolean, label?: string) => void;
    closeUserMenu?: () => void;
  } = {},
): Monitor {
  // ── Разметка ────────────────────────────────────────────────────────
  const app = el("app");
  const feedEl = el("feed");
  const queryEl = input("query");
  const queryClearBtn = button("query-clear");
  const searchFieldWrap = el("search-field-wrap");
  const searchFields = el("search-fields");
  const startBtn = button("start");
  const stopBtn = button("stop");
  const modeQueryBtn = button("mode-query");
  const modeUrlBtn = button("mode-url");
  const searchStatusPill = el("search-status-pill");
  const searchStatusLabel = el("search-status-label");

  const filtersSections = el("filters-sections");
  const filtersDrawer = el("filters-drawer");
  const filtersDrawerBack = el("filters-drawer-back");
  const filtersOpenBtn = button("filters-open-btn");
  const filtersCloseBtn = button("filters-close");
  const searchSettingsCats = el("search-settings-cats");
  const searchSettingsIphone = el("search-settings-iphone");
  const searchSettingsUrl = el("search-settings-url");
  const hideImagesCheck = input("hide-images");

  const regionWrap = el("region-wrap");
  const regionBtn = button("region-btn");
  const regionLabel = el("region-label");
  const regionPop = el("region-pop");
  const regionQuery = input("region-query");
  const regionList = el("region-list");

  const categoryWrap = el("category-wrap");
  const categoryTrigger = button("category-trigger");
  const categoryPanel = el("category-panel");
  const categoryLabel = el("category-label");
  const catsEl = el("cats");

  const iphoneWrap = el("iphone-models-wrap");
  const iphoneTrigger = button("iphone-models-trigger");
  const iphonePanel = el("iphone-models-panel");
  const iphoneLabel = el("iphone-models-label");
  const iphoneListEl = el("iphone-models");
  const iphoneAllBtn = button("iphone-models-all");
  const iphoneNoneBtn = button("iphone-models-none");

  const categoryDropdown = {
    panel: categoryPanel,
    trigger: categoryTrigger,
    wrap: categoryWrap,
  };
  const iphoneDropdown = { panel: iphonePanel, trigger: iphoneTrigger, wrap: iphoneWrap };

  // ── Состояние формы ─────────────────────────────────────────────────
  let region: Region = DEFAULT_REGION;
  let category: Category = DEFAULT_CATEGORY;
  let categories: Category[] = FALLBACK_CATEGORIES;
  let searchMode: SearchMode = "query";
  let iphoneModels: string[] = loadIphoneModels();
  let hideImages = false;
  let monitoring = false;
  let starting = false;
  let booted = false;

  const isMonitoring = (): boolean => monitoring;
  const notice = (text: string, kind: ToastKind = "info"): void => showToast(text, kind);

  // ── Части интерфейса ────────────────────────────────────────────────
  const avito = createAvitoSession({ onStatusChange: opts.onAvitoStatus });

  const cards = createAdCardRenderer({
    feed: feedEl,
    regionSlug: () => region.slug,
    hideImages: () => hideImages,
    onCall: (ad, btn) => void avito.requestPhone(ad, btn),
    onBlockSeller: (seller) => {
      if (!addSellerToBlacklist(seller)) return;
      void syncSellerBlacklist();
      feed.purgeBlacklisted();
      notice(`«${seller}» в чёрном списке`, "success");
    },
  });

  const feed = createAdFeed({
    feed: feedEl,
    cards,
    isMonitoring,
    isPushEnabled: () => opts.isPushEnabled?.() ?? false,
    onReconnecting: () => notice("Переподключение…"),
  });

  const savedUrls = createSavedUrlsPanel({
    queryEl,
    isMonitoring,
    onQueryChange: () => syncQueryClear(),
    onPersist: () => persistSettings(),
    onNotice: (text, kind) => notice(text, kind),
  });

  // ── Признаки состояния ──────────────────────────────────────────────

  const iphoneFilterApplies = (): boolean =>
    searchMode === "query" && category.id === IPHONE_CATEGORY_ID;

  const isAllCategories = (): boolean => searchMode === "query" && category.id === ALL_CATEGORY_ID;

  /** Подсветить кнопку фильтров, если условия отличаются от обычных. */
  const syncFiltersButton = (): void => {
    const custom =
      category.id !== IPHONE_CATEGORY_ID ||
      hideImages ||
      (searchMode === "url" && savedUrls.hasUrls()) ||
      (iphoneFilterApplies() && !isDefaultIphoneSelection(iphoneModels));
    filtersOpenBtn.classList.toggle("active", custom);
  };

  const persistSettings = (): void => {
    saveSearchSettings({
      region,
      category,
      searchMode,
      selectedSavedUrlId: savedUrls.selectedId(),
      hideImages,
      query: isAllCategories() ? queryEl.value.trim() : "",
    });
  };

  // ── Панель фильтров ─────────────────────────────────────────────────

  const closeAllDropdowns = (): void => {
    regionPop.classList.remove("open");
    setDropdownOpen(categoryDropdown, false);
    setDropdownOpen(iphoneDropdown, false);
    savedUrls.closeDropdown();
  };

  const setFiltersOpen = (open: boolean): void => {
    filtersDrawerBack.classList.toggle("hidden", !open);
    // Класс анимации ставим следующим кадром, иначе переход не запустится.
    requestAnimationFrame(() => filtersDrawerBack.classList.toggle("open", open));
    filtersDrawerBack.setAttribute("aria-hidden", open ? "false" : "true");
    filtersOpenBtn.setAttribute("aria-expanded", open ? "true" : "false");
    document.body.classList.toggle("filters-drawer-open", open);
    if (open) opts.closeUserMenu?.();
    else closeAllDropdowns();
  };

  const isFiltersOpen = (): boolean => filtersDrawerBack.classList.contains("open");

  const syncIphoneSection = (): void => {
    const visible = iphoneFilterApplies();
    searchSettingsIphone.classList.toggle("hidden", !visible);
    if (!visible) setDropdownOpen(iphoneDropdown, false);
    syncFiltersButton();
  };

  // ── Блокировка формы во время поиска ────────────────────────────────

  const syncSearchControls = (): void => {
    const locked = monitoring;
    searchFields.classList.toggle("is-locked", locked);
    for (const control of [
      queryEl,
      queryClearBtn,
      modeQueryBtn,
      modeUrlBtn,
      regionBtn,
      regionQuery,
      hideImagesCheck,
      categoryTrigger,
      iphoneTrigger,
      iphoneAllBtn,
      iphoneNoneBtn,
    ]) {
      control.disabled = locked;
    }
    startBtn.disabled = starting || locked;
    stopBtn.disabled = starting || !monitoring;
    if (!starting) startBtn.textContent = "Начать поиск";

    savedUrls.setLocked(locked);
    setDisabledAll(iphoneListEl, ".styled-check input", locked);
    setDisabledAll(filtersSections, ".filter-section-toggle", locked);
    setDisabledAll(regionList, "button[data-slug]", locked);
    if (locked) closeAllDropdowns();
  };

  const syncSearchStatus = (): void => {
    searchStatusPill.classList.toggle("is-active", monitoring);
    searchStatusPill.classList.toggle("is-idle", !monitoring);
    searchStatusLabel.textContent = monitoring ? "Поиск активен" : "Поиск остановлен";
  };

  const setMonitoring = (on: boolean): void => {
    monitoring = on;
    syncSearchControls();
    syncSearchStatus();
  };

  const setStarting = (value: boolean): void => {
    starting = value;
    if (value) startBtn.textContent = "Запускаю…";
    syncSearchControls();
  };

  // ── Поле ввода и режим поиска ───────────────────────────────────────

  const syncQueryClear = (): void => {
    queryClearBtn.classList.toggle("hidden", !queryEl.value.trim());
  };

  const syncQueryField = (): void => {
    const byUrl = searchMode === "url";
    const showQuery = byUrl || isAllCategories();
    searchFieldWrap.classList.toggle("hidden", !showQuery);
    queryEl.type = "search";
    queryEl.placeholder = byUrl ? "Вставьте ссылку или выберите из списка" : "Что искать";
    if (byUrl) {
      queryEl.setAttribute("inputmode", "url");
      queryEl.setAttribute("autocomplete", "url");
    } else {
      queryEl.removeAttribute("inputmode");
      queryEl.setAttribute("autocomplete", "off");
    }
    syncQueryClear();
  };

  const setSearchMode = (mode: SearchMode): void => {
    const previous = searchMode;
    searchMode = mode;
    modeQueryBtn.classList.toggle("active", mode === "query");
    modeUrlBtn.classList.toggle("active", mode === "url");

    const byUrl = mode === "url";
    regionWrap.classList.toggle("hidden", byUrl);
    searchSettingsCats.classList.toggle("hidden", byUrl);
    searchSettingsUrl.classList.toggle("hidden", !byUrl);
    // Поле нужно в режиме ссылки и в поиске по всем категориям.
    if (!byUrl && (previous === "url" || !isAllCategories())) queryEl.value = "";
    syncQueryField();

    savedUrls.setVisible(byUrl);
    if (byUrl) {
      savedUrls.fillQueryIfEmpty();
      setFilterSectionOpen(searchSettingsUrl, true);
    }
    syncIphoneSection();
    syncFiltersButton();
    syncSearchControls();
    persistSettings();
  };

  // ── Категории ───────────────────────────────────────────────────────

  const resolveCategory = (id: string): Category => {
    const wanted = LEGACY_CATEGORY_IDS.has(id) ? IPHONE_CATEGORY_ID : id;
    return categories.find((item) => item.id === wanted) ?? categories[0] ?? DEFAULT_CATEGORY;
  };

  const renderCategories = (items?: Category[]): void => {
    if (items?.length) categories = items;
    category = resolveCategory(category.id);
    catsEl.innerHTML = categories
      .map((item) => {
        const active = item.id === category.id;
        return `<button type="button" class="multi-select-choice${active ? " active" : ""}" data-id="${escapeHtml(item.id)}" data-name="${escapeHtml(item.name)}" role="option" aria-selected="${active}">
          <span class="multi-select-choice-label">${escapeHtml(item.name)}</span>
          <span class="multi-select-choice-mark">${ICON_CHECK}</span>
        </button>`;
      })
      .join("");
    categoryLabel.textContent = category.name;
    syncQueryField();
    syncIphoneSection();
    syncFiltersButton();
    syncSearchControls();
  };

  // ── Модели iPhone ───────────────────────────────────────────────────

  const renderIphoneModels = (): void => {
    const selected = new Set(iphoneModels);
    iphoneListEl.innerHTML = IPHONE_MODELS.map(
      (item) => `<label class="multi-select-option">
        <span class="styled-check">
          <input type="checkbox" value="${escapeHtml(item.id)}"${selected.has(item.id) ? " checked" : ""} />
          <span class="styled-check-box" aria-hidden="true">${ICON_CHECK}</span>
        </span>
        <span class="multi-select-option-label">${escapeHtml(item.label)}</span>
      </label>`,
    ).join("");
    iphoneLabel.textContent = iphoneModelsSummary(iphoneModels);
    syncFiltersButton();
  };

  const setIphoneModels = (models: string[], rerender = true): void => {
    iphoneModels = normalizeIphoneModels(models, { allowEmpty: true });
    saveIphoneModels(iphoneModels);
    if (rerender) renderIphoneModels();
    else iphoneLabel.textContent = iphoneModelsSummary(iphoneModels);
    syncFiltersButton();
  };

  // ── Регионы ─────────────────────────────────────────────────────────

  const renderRegions = async (needle: string): Promise<void> => {
    const found = await api.regions(needle).catch(() => []);
    if (!found.length) {
      regionList.innerHTML = '<div class="region-empty">Ничего не найдено</div>';
      return;
    }
    regionList.innerHTML = found
      .map(
        (item) =>
          `<button type="button" data-slug="${escapeHtml(item.slug)}" class="${
            item.slug === region.slug ? "active" : ""
          }">${escapeHtml(item.name)}</button>`,
      )
      .join("");
  };

  const setRegion = (next: Region): void => {
    region = next;
    regionLabel.textContent = next.name;
    // Время публикации показывается по часовому поясу региона.
    cards.refreshTimes();
  };

  // ── Запуск и остановка ──────────────────────────────────────────────

  /** Привести форму к тому, что реально запущено на сервере. */
  const applyServerState = (state: SearchState): void => {
    if (state.search_mode) setSearchMode(state.search_mode);
    if (state.query && (state.search_mode === "url" || state.category?.id === ALL_CATEGORY_ID)) {
      queryEl.value = state.query;
      if (state.search_mode === "url") savedUrls.syncSelectionFromQuery();
    }
    if (state.region?.slug) setRegion(state.region);
    if (state.category?.id) {
      category = state.category;
      renderCategories();
    }
    persistSettings();
  };

  const startSearch = async (): Promise<void> => {
    if (monitoring) {
      notice("Сначала остановите текущий поиск", "error");
      return;
    }
    const value = queryEl.value.trim();
    if (searchMode === "url" && !value) {
      notice("Вставьте ссылку Avito", "error");
      queryEl.focus();
      return;
    }
    if (isAllCategories() && !value) {
      notice("Введите поисковый запрос", "error");
      queryEl.focus();
      return;
    }
    if (iphoneFilterApplies() && !iphoneModels.length) {
      notice("Выберите хотя бы одну модель iPhone", "error");
      setFilterSectionOpen(searchSettingsIphone, true);
      return;
    }

    setStarting(true);
    try {
      const models = iphoneFilterApplies() ? iphoneModelsToPayload(iphoneModels) : null;
      const sellerSkip = loadSellerBlacklist();
      const state = await api.startSearch(
        searchMode === "url"
          ? { mode: "url", url: value, seller_skip: sellerSkip, iphone_models: models }
          : {
              mode: "query",
              query: isAllCategories() ? value : "",
              region: region.slug,
              category: category.id,
              seller_skip: sellerSkip,
              iphone_models: models,
            },
      );
      setMonitoring(true);
      applyServerState(state);
      feed.clear();
      setFiltersOpen(false);
    } catch (err) {
      notice(err instanceof Error ? err.message : String(err), "error");
    } finally {
      setStarting(false);
    }
  };

  const stopSearch = async (): Promise<void> => {
    stopBtn.disabled = true;
    try {
      await api.stopSearch();
      setMonitoring(false);
      notice("Поиск остановлен");
    } catch (err) {
      syncSearchControls();
      notice(err instanceof Error ? err.message : String(err), "error");
    }
  };

  // ── Восстановление и запуск ─────────────────────────────────────────

  const restoreSettings = (): void => {
    const saved = loadSearchSettings();
    if (saved.searchMode) searchMode = saved.searchMode;
    if (saved.region) {
      region = saved.region;
      regionLabel.textContent = region.name;
    }
    if (saved.category) category = resolveCategory(saved.category.id);
    if (saved.query && category.id === ALL_CATEGORY_ID) queryEl.value = saved.query;
    hideImages = saved.hideImages;
    hideImagesCheck.checked = hideImages;
    savedUrls.restore({
      legacyUrl: saved.legacySavedUrl,
      selectedId: saved.selectedSavedUrlId,
    });
    cards.applyHideImages();
  };

  /**
   * Догнать сервер: мониторинг мог остаться запущенным с прошлой сессии
   * или идти в другой вкладке.
   */
  const adoptRunningSearch = async (): Promise<void> => {
    const state = await api.status().catch(() => null);
    if (!state?.running) return;
    setMonitoring(true);
    applyServerState(state);
    setFiltersOpen(false);
    const ads = await api.ads().catch(() => []);
    feed.add(ads, false);
  };

  const bindEvents = (): void => {
    // Форма отправляется по Enter — перезагрузка страницы здесь не нужна.
    el("search-form").addEventListener("submit", (ev) => ev.preventDefault());

    // ── Поле ввода ────────────────────────────────────────────────────
    queryEl.addEventListener("input", () => {
      syncQueryClear();
      if (searchMode === "url") savedUrls.handleQueryInput();
      else persistSettings();
    });
    queryEl.addEventListener("focus", () => {
      if (searchMode === "url") savedUrls.handleQueryFocus();
    });
    queryClearBtn.addEventListener("click", () => {
      if (monitoring) return;
      queryEl.value = "";
      syncQueryClear();
      queryEl.focus();
    });

    // ── Режим и запуск ────────────────────────────────────────────────
    modeQueryBtn.addEventListener("click", () => {
      if (!monitoring) setSearchMode("query");
    });
    modeUrlBtn.addEventListener("click", () => {
      if (!monitoring) setSearchMode("url");
    });
    startBtn.addEventListener("click", () => void startSearch());
    stopBtn.addEventListener("click", () => void stopSearch());

    // ── Панель фильтров ───────────────────────────────────────────────
    filtersOpenBtn.addEventListener("click", () => setFiltersOpen(!isFiltersOpen()));
    filtersCloseBtn.addEventListener("click", () => setFiltersOpen(false));
    filtersDrawerBack.addEventListener("click", (ev) => {
      if (ev.target === filtersDrawerBack) setFiltersOpen(false);
    });
    // Клик внутри панели не должен закрывать её через затемнение.
    filtersDrawer.addEventListener("click", (ev) => ev.stopPropagation());
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && isFiltersOpen()) setFiltersOpen(false);
    });
    for (const [toggleId, sectionId] of FILTER_SECTIONS) {
      const section = el(sectionId);
      button(toggleId).addEventListener("click", () => {
        if (monitoring) return;
        setFilterSectionOpen(section, !section.classList.contains("open"));
      });
    }

    hideImagesCheck.addEventListener("change", () => {
      if (monitoring) return;
      hideImages = hideImagesCheck.checked;
      cards.applyHideImages();
      syncFiltersButton();
      persistSettings();
    });

    // ── Категории ─────────────────────────────────────────────────────
    categoryTrigger.addEventListener("click", (ev) => {
      if (monitoring) return;
      ev.stopPropagation();
      const open = !isDropdownOpen(categoryPanel);
      // Два выпадающих списка рядом — открытым должен быть один.
      if (open) setDropdownOpen(iphoneDropdown, false);
      setDropdownOpen(categoryDropdown, open);
    });
    catsEl.addEventListener("click", (ev) => {
      if (monitoring) return;
      const choice = (ev.target as HTMLElement).closest<HTMLButtonElement>(
        "button.multi-select-choice",
      );
      if (!choice) return;
      category = {
        id: choice.dataset.id || IPHONE_CATEGORY_ID,
        name: choice.dataset.name || "",
      };
      if (category.id !== ALL_CATEGORY_ID) queryEl.value = "";
      syncQueryClear();
      renderCategories();
      setDropdownOpen(categoryDropdown, false);
      persistSettings();
    });

    // ── Модели iPhone ─────────────────────────────────────────────────
    iphoneTrigger.addEventListener("click", (ev) => {
      if (monitoring) return;
      ev.stopPropagation();
      const open = !isDropdownOpen(iphonePanel);
      if (open) setDropdownOpen(categoryDropdown, false);
      setDropdownOpen(iphoneDropdown, open);
    });
    iphoneListEl.addEventListener("change", (ev) => {
      if (monitoring) return;
      const box = ev.target as HTMLInputElement;
      if (box.type !== "checkbox" || !DEFAULT_IPHONE_MODELS.includes(box.value)) return;
      const next = box.checked
        ? [...iphoneModels, box.value]
        : iphoneModels.filter((id) => id !== box.value);
      // Список не перерисовываем: иначе флажок потеряет фокус под курсором.
      setIphoneModels(next, false);
    });
    iphoneAllBtn.addEventListener("click", (ev) => {
      ev.preventDefault();
      if (!monitoring) setIphoneModels([...DEFAULT_IPHONE_MODELS]);
    });
    iphoneNoneBtn.addEventListener("click", (ev) => {
      ev.preventDefault();
      if (!monitoring) setIphoneModels([]);
    });

    // ── Регионы ───────────────────────────────────────────────────────
    regionBtn.addEventListener("click", (ev) => {
      if (monitoring) return;
      ev.stopPropagation();
      const open = !regionPop.classList.contains("open");
      regionPop.classList.toggle("open", open);
      if (!open) return;
      void renderRegions(regionQuery.value);
      regionQuery.focus();
    });
    regionQuery.addEventListener("input", () => void renderRegions(regionQuery.value));
    regionList.addEventListener("click", (ev) => {
      if (monitoring) return;
      const choice = (ev.target as HTMLElement).closest<HTMLButtonElement>("button[data-slug]");
      if (!choice) return;
      setRegion({ slug: choice.dataset.slug || "all", name: choice.textContent || "" });
      regionPop.classList.remove("open");
      regionQuery.value = "";
      persistSettings();
    });

    // Клик вне выпадающего списка закрывает его.
    document.addEventListener("click", (ev) => {
      const target = ev.target as Node;
      if (!regionPop.contains(target) && !regionBtn.contains(target)) {
        regionPop.classList.remove("open");
      }
      if (!categoryWrap.contains(target)) setDropdownOpen(categoryDropdown, false);
      if (!iphoneWrap.contains(target)) setDropdownOpen(iphoneDropdown, false);
      if (!savedUrls.containsFocus(target)) savedUrls.closeDropdown();
    });

    // Чёрный список продавцов могли изменить в меню пользователя.
    window.addEventListener(BLACKLIST_EVENT, () => feed.purgeBlacklisted());
  };

  /** Собрать экран. Вызывается один раз, после успешного входа. */
  const boot = async (): Promise<void> => {
    if (booted) return;
    booted = true;

    void syncSellerBlacklist();
    restoreSettings();
    renderIphoneModels();
    renderCategories();
    setSearchMode(searchMode);
    setFiltersOpen(false);
    syncSearchStatus();
    bindEvents();

    void avito.refresh();
    void api
      .categories()
      .then(renderCategories)
      .catch(() => undefined);
    feed.start();
    await adoptRunningSearch();
  };

  return {
    show() {
      app.classList.remove("hidden");
      void boot();
    },
    hide() {
      app.classList.add("hidden");
    },
    openAvito: avito.open,
    closeFilters: () => setFiltersOpen(false),
  };
}
