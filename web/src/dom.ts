/**
 * Доступ к разметке.
 *
 * Вся разметка приложения статична и лежит в index.html, поэтому элементы
 * ищутся по id один раз при старте. Если id не найден — это опечатка в
 * разметке или коде, и молчать про неё нельзя: без элемента интерфейс всё
 * равно не соберётся, а ошибка на старте понятнее, чем «ничего не работает».
 */

/** Элемент по id. Бросает исключение, если его нет в разметке. */
export function el(id: string): HTMLElement {
  const found = document.getElementById(id);
  if (!found) throw new Error(`#${id} не найден в разметке`);
  return found;
}

export function button(id: string): HTMLButtonElement {
  return el(id) as HTMLButtonElement;
}

export function input(id: string): HTMLInputElement {
  return el(id) as HTMLInputElement;
}

export function textarea(id: string): HTMLTextAreaElement {
  return el(id) as HTMLTextAreaElement;
}

/**
 * Открыть или закрыть выпадающий список.
 *
 * Классы дублируются намеренно: `hidden` убирает элемент из потока, а
 * `open` включает анимацию — по отдельности их использует CSS.
 */
export function setDropdownOpen(
  parts: { panel: HTMLElement; trigger: HTMLElement; wrap: HTMLElement },
  open: boolean,
): void {
  parts.panel.classList.toggle("hidden", !open);
  parts.panel.classList.toggle("open", open);
  parts.wrap.classList.toggle("open", open);
  parts.trigger.setAttribute("aria-expanded", open ? "true" : "false");
}

export function isDropdownOpen(panel: HTMLElement): boolean {
  return panel.classList.contains("open");
}

/** Раскрыть или свернуть секцию в панели фильтров. */
export function setFilterSectionOpen(section: HTMLElement, open: boolean): void {
  section.classList.toggle("open", open);
  section.querySelector(".filter-section-body")?.classList.toggle("hidden", !open);
  section
    .querySelector(".filter-section-toggle")
    ?.setAttribute("aria-expanded", open ? "true" : "false");
}

/** Выставить `disabled` всем элементам, попавшим под селектор. */
export function setDisabledAll(root: HTMLElement, selector: string, disabled: boolean): void {
  root.querySelectorAll<HTMLButtonElement | HTMLInputElement>(selector).forEach((node) => {
    node.disabled = disabled;
  });
}
