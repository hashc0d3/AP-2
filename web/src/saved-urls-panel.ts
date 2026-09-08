/**
 * Панель сохранённых ссылок в режиме поиска «по ссылке».
 *
 * Состоит из трёх частей: выпадающий список под полем ввода (быстрый выбор),
 * список с правкой в панели фильтров и форма добавления. Модуль владеет
 * своей разметкой и списком закладок; наружу отдаёт только то, что нужно
 * поиску, — текущую ссылку и признак «закладки есть».
 */

import { button, el, input } from "./dom";
import { escapeHtml } from "./format";
import {
  addSavedUrl,
  findSavedUrlByUrl,
  MAX_SAVED_URLS,
  migrateLegacySavedUrl,
  removeSavedUrl,
  type SavedUrl,
  updateSavedUrl,
} from "./saved-urls";

/**
 * Задержка перед закрытием выпадающего списка при уходе фокуса.
 *
 * Без неё клик по пункту не успевает сработать: браузер сначала снимает
 * фокус с поля ввода и список исчезает.
 */
const BLUR_CLOSE_DELAY_MS = 120;

export type SavedUrlsPanel = {
  /** Перерисовать список и выпадающее меню. */
  render: () => void;
  /** Показать или скрыть блоки, относящиеся к режиму «по ссылке». */
  setVisible: (visible: boolean) => void;
  /** Закрыть выпадающий список. */
  closeDropdown: () => void;
  /** Реакция на ввод в поле поиска: подсказки и синхронизация выбора. */
  handleQueryInput: () => void;
  /** Реакция на фокус в поле поиска. */
  handleQueryFocus: () => void;
  /** Подставить в поле ссылку выбранной закладки, если поле пустое. */
  fillQueryIfEmpty: () => void;
  /** Отметить закладку, ссылка которой совпала с полем ввода. */
  syncSelectionFromQuery: () => void;
  /** Есть ли хотя бы одна закладка (влияет на подсветку кнопки фильтров). */
  hasUrls: () => boolean;
  /** Заблокировать правку на время активного поиска. */
  setLocked: (locked: boolean) => void;
  /** Восстановить список из настроек и вернуть id выбранной закладки. */
  restore: (state: { legacyUrl: string; selectedId: string | null }) => string | null;
  /** Id выбранной закладки — сохраняется в настройках поиска. */
  selectedId: () => string | null;
  /** Лежит ли узел внутри блока поиска по ссылке (для клика «мимо»). */
  containsFocus: (target: Node) => boolean;
};

export function createSavedUrlsPanel(opts: {
  /** Поле ввода поиска: в режиме «по ссылке» в нём лежит ссылка Avito. */
  queryEl: HTMLInputElement;
  /** Идёт ли поиск: во время поиска правка закладок запрещена. */
  isMonitoring: () => boolean;
  /** Синхронизировать кнопку очистки поля. */
  onQueryChange: () => void;
  /** Сохранить настройки поиска (выбранная закладка входит в них). */
  onPersist: () => void;
  /** Показать сообщение пользователю. */
  onNotice: (text: string, kind: "success" | "error") => void;
}): SavedUrlsPanel {
  const combo = el("url-search-combo");
  const dropdown = el("url-search-dropdown");
  const dropdownList = el("url-search-dropdown-list");
  const manageBox = el("saved-urls-manage");
  const list = el("saved-urls-list");
  const limitNote = el("saved-urls-limit");
  const addBtn = button("saved-url-add-btn");
  const addForm = el("saved-url-add-form");
  const addName = input("saved-url-add-name");
  const addUrl = input("saved-url-add-url");
  const addConfirm = button("saved-url-add-confirm");
  const addCancel = button("saved-url-add-cancel");

  let urls: SavedUrl[] = [];
  let selectedId: string | null = null;
  let visible = false;

  const selected = (): SavedUrl | undefined => urls.find((item) => item.id === selectedId);

  const atLimit = (): boolean => urls.length >= MAX_SAVED_URLS;

  /**
   * Текст для фильтра подсказок.
   *
   * Пока пользователь вставляет саму ссылку, фильтровать по ней нечего —
   * закладки ищутся по названию.
   */
  const dropdownFilter = (): string => {
    const value = opts.queryEl.value.trim();
    if (!value || value.startsWith("http://") || value.startsWith("https://")) return "";
    return value.toLowerCase();
  };

  const setDropdownOpen = (open: boolean): void => {
    dropdown.classList.toggle("hidden", !(open && visible && urls.length > 0));
  };

  const renderDropdown = (): void => {
    const filter = dropdownFilter();
    const matches = urls.filter((item) => !filter || item.name.toLowerCase().includes(filter));
    if (!matches.length) {
      dropdownList.innerHTML = '<p class="url-search-dropdown-empty">Нет сохранённых ссылок</p>';
      return;
    }
    dropdownList.innerHTML = matches
      .map(
        (item) =>
          `<button type="button" class="url-search-dropdown-item${
            item.id === selectedId ? " active" : ""
          }" data-id="${escapeHtml(item.id)}">${escapeHtml(item.name)}</button>`,
      )
      .join("");
  };

  const render = (): void => {
    limitNote.classList.toggle("hidden", !atLimit());
    addBtn.disabled = opts.isMonitoring() || atLimit();
    if (atLimit()) setAddFormOpen(false);
    renderDropdown();
    list.innerHTML = urls
      .map(
        (item) => `<article class="saved-url-item" data-id="${escapeHtml(item.id)}">
        <input type="text" class="field saved-url-item-name" value="${escapeHtml(item.name)}" maxlength="64" placeholder="Название" aria-label="Название" />
        <input type="url" class="field saved-url-item-url" value="${escapeHtml(item.url)}" placeholder="https://www.avito.ru/..." aria-label="Ссылка" />
        <button type="button" class="saved-url-item-delete" data-action="delete-saved-url" aria-label="Удалить" title="Удалить">×</button>
      </article>`,
      )
      .join("");
  };

  const setVisible = (value: boolean): void => {
    visible = value;
    manageBox.classList.toggle("hidden", !value);
    if (!value) setDropdownOpen(false);
    render();
  };

  const setAddFormOpen = (open: boolean): void => {
    addForm.classList.toggle("hidden", !open);
    addBtn.classList.toggle("hidden", open);
    if (open) {
      addName.focus();
      return;
    }
    addName.value = "";
    addUrl.value = "";
  };

  const select = (id: string | null, fillQuery = true): void => {
    selectedId = id;
    const item = selected();
    if (fillQuery && item) {
      opts.queryEl.value = item.url;
      opts.onQueryChange();
    }
    render();
    opts.onPersist();
  };

  const syncSelectionFromQuery = (): void => {
    const value = opts.queryEl.value.trim();
    if (!value) {
      if (selectedId) select(null, false);
      return;
    }
    const match = findSavedUrlByUrl(urls, value);
    if (match) {
      if (selectedId !== match.id) select(match.id, false);
    } else if (selectedId) {
      select(null, false);
    }
  };

  const addFromForm = (): void => {
    const result = addSavedUrl(urls, addName.value, addUrl.value);
    if (result.limitReached) {
      opts.onNotice(`Максимум ${MAX_SAVED_URLS} ссылок`, "error");
      return;
    }
    if (!result.item.id) {
      opts.onNotice("Укажите название и ссылку", "error");
      return;
    }
    urls = result.urls;
    setAddFormOpen(false);
    render();
    opts.onPersist();
    const action = result.added ? "добавлена" : "обновлена";
    opts.onNotice(`Ссылка «${result.item.name}» ${action}`, "success");
  };

  // ── Обработчики ─────────────────────────────────────────────────────

  const whenEditable = (handler: () => void) => () => {
    if (!opts.isMonitoring()) handler();
  };

  addBtn.addEventListener(
    "click",
    whenEditable(() => {
      if (!atLimit()) setAddFormOpen(true);
    }),
  );
  addCancel.addEventListener(
    "click",
    whenEditable(() => setAddFormOpen(false)),
  );
  addConfirm.addEventListener("click", whenEditable(addFromForm));
  addForm.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter") return;
    ev.preventDefault();
    if (!opts.isMonitoring()) addFromForm();
  });

  // Фокус ушёл — закрываем список, но не сразу: иначе клик по пункту не
  // успеет сработать.
  opts.queryEl.addEventListener("blur", () => {
    window.setTimeout(() => {
      if (!combo.contains(document.activeElement)) setDropdownOpen(false);
    }, BLUR_CLOSE_DELAY_MS);
  });

  // Клик мышью не должен снимать фокус с поля ввода — иначе список
  // закроется раньше, чем сработает выбор.
  dropdownList.addEventListener("mousedown", (ev) => ev.preventDefault());
  dropdownList.addEventListener("click", (ev) => {
    if (opts.isMonitoring()) return;
    const item = (ev.target as HTMLElement).closest<HTMLButtonElement>(".url-search-dropdown-item");
    if (!item?.dataset.id) return;
    select(item.dataset.id);
    setDropdownOpen(false);
    opts.queryEl.focus();
  });

  list.addEventListener("click", (ev) => {
    if (opts.isMonitoring()) return;
    const deleteBtn = (ev.target as HTMLElement).closest('[data-action="delete-saved-url"]');
    const row = deleteBtn?.closest<HTMLElement>(".saved-url-item");
    if (!row?.dataset.id) return;
    urls = removeSavedUrl(urls, row.dataset.id);
    if (selectedId === row.dataset.id) {
      selectedId = null;
      syncSelectionFromQuery();
    }
    render();
    opts.onPersist();
  });

  list.addEventListener("change", (ev) => {
    if (opts.isMonitoring()) return;
    const field = ev.target as HTMLInputElement;
    const isEditable =
      field.classList.contains("saved-url-item-name") ||
      field.classList.contains("saved-url-item-url");
    if (!isEditable) return;
    const row = field.closest<HTMLElement>(".saved-url-item");
    if (!row?.dataset.id) return;
    const name = row.querySelector<HTMLInputElement>(".saved-url-item-name")?.value ?? "";
    const url = row.querySelector<HTMLInputElement>(".saved-url-item-url")?.value ?? "";
    const next = updateSavedUrl(urls, row.dataset.id, name, url);
    // Тот же массив — правка отклонена (пустое поле или дубликат ссылки).
    if (next === urls) return;
    urls = next;
    if (selectedId === row.dataset.id) {
      opts.queryEl.value = url.trim();
      opts.onQueryChange();
    }
    render();
    opts.onPersist();
  });

  return {
    render,
    setVisible,
    closeDropdown: () => setDropdownOpen(false),

    handleQueryInput() {
      syncSelectionFromQuery();
      renderDropdown();
      // Вставленную ссылку подсказками не дополняем.
      const typingUrl = opts.queryEl.value.trim().startsWith("http");
      setDropdownOpen(!typingUrl && document.activeElement === opts.queryEl);
    },

    handleQueryFocus() {
      if (opts.isMonitoring() || !urls.length) return;
      renderDropdown();
      setDropdownOpen(true);
    },

    fillQueryIfEmpty() {
      const item = selected();
      if (item && !opts.queryEl.value.trim()) {
        opts.queryEl.value = item.url;
      }
    },

    syncSelectionFromQuery,
    hasUrls: () => urls.length > 0,

    setLocked(locked: boolean) {
      addBtn.disabled = locked || atLimit();
      addConfirm.disabled = locked;
      addCancel.disabled = locked;
      addName.disabled = locked;
      addUrl.disabled = locked;
      list
        .querySelectorAll<HTMLButtonElement | HTMLInputElement>(
          ".saved-url-item-name, .saved-url-item-url, .saved-url-item-delete",
        )
        .forEach((node) => {
          node.disabled = locked;
        });
    },

    restore(state) {
      urls = migrateLegacySavedUrl(state.legacyUrl);
      selectedId = urls.some((item) => item.id === state.selectedId) ? state.selectedId : null;
      return selectedId;
    },

    selectedId: () => selectedId,
    containsFocus: (target: Node) => combo.contains(target),
  };
}
