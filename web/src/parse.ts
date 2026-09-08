/**
 * Чтение значений из недоверенных источников (localStorage, JSON).
 *
 * Данные в localStorage переживают обновления приложения, поэтому там может
 * оказаться что угодно из прошлых версий. `String(raw)` для таких значений
 * не годится: объект превратился бы в строку «[object Object]» и прошёл бы
 * дальше как осмысленное значение.
 */

/** Строка без пробелов по краям; для всего остального — пустая строка. */
export function asText(raw: unknown): string {
  if (typeof raw === "string") return raw.trim();
  if (typeof raw === "number" && Number.isFinite(raw)) return String(raw);
  return "";
}
