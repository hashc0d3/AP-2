/**
 * Сессия Avito и получение номера телефона.
 *
 * Avito отдаёт номер только авторизованному пользователю, а войти за него
 * приложение не может. Поэтому пользователь один раз вставляет cookies
 * своего аккаунта (Cookie-Editor), сервер их хранит и подставляет в запрос
 * номера.
 *
 * Модуль владеет модальным окном подключения и кнопкой «Позвонить»: снаружи
 * нужны только `open()` и `requestPhone()`.
 */

import { api } from "./api";
import { itemWebUrl } from "./avito-links";
import { button, el, textarea } from "./dom";
import { isIosDevice } from "./push-notify";
import type { Ad } from "./types";

/** Коды ответа сервера, означающие «сессия Avito больше не годится». */
const SESSION_LOST_CODES = new Set(["no_session", "not_logged_in", "auth_required"]);

const HINT_AFTER_LOGIN = "После входа: Cookie-Editor → Export → вставьте JSON ниже";

export type AvitoSessionUi = {
  /** Открыть окно подключения. */
  open: () => void;
  /** Спросить у сервера текущее состояние сессии и обновить окно. */
  refresh: () => Promise<void>;
  /** Обработчик кнопки «Позвонить» на карточке. */
  requestPhone: (ad: Ad, btn: HTMLButtonElement) => Promise<void>;
};

export function createAvitoSession(opts: {
  /** Сообщить внешнему интерфейсу (меню пользователя) о смене состояния. */
  onStatusChange?: (connected: boolean, label?: string) => void;
}): AvitoSessionUi {
  const modal = el("avito-modal");
  const statusLine = el("avito-status-line");
  const hint = el("avito-hint");
  const importBtn = button("avito-import");
  const connectedBox = el("avito-connected-box");
  const setupBox = el("avito-setup-box");
  const connectedLabel = el("avito-connected-label");
  const resetBtn = button("avito-reset");
  const cookiesInput = textarea("avito-cookies");

  let connected = false;
  /** Номера живут до перезагрузки страницы: повторный запрос стоит денег. */
  const phones = new Map<string, string>();

  const setConnected = (value: boolean, label = ""): void => {
    connected = value;
    opts.onStatusChange?.(value, label);
  };

  const render = (data: { connected?: boolean; label?: string; error?: string }): void => {
    const isConnected = Boolean(data.connected);
    const label = data.label || "";
    statusLine.textContent = isConnected
      ? `Подключён${label ? ` (${label})` : ""}`
      : "Не подключён";
    setConnected(isConnected, label);
    connectedBox.classList.toggle("hidden", !isConnected);
    setupBox.classList.toggle("hidden", isConnected);
    connectedLabel.textContent = label || "вход";
    if (data.error) hint.textContent = data.error;
  };

  const refresh = async (): Promise<void> => {
    try {
      const data = await api.avitoSession();
      render({ connected: data.connected, label: data.label });
    } catch {
      // Сервер недоступен — считаем, что позвонить не сможем.
      setConnected(false);
    }
  };

  const open = (): void => {
    modal.classList.remove("hidden");
    hint.textContent = "";
    showPlatformSteps();
    void refresh();
  };

  const close = (): void => {
    modal.classList.add("hidden");
  };

  /** Инструкция зависит от платформы: на iOS PWA не видит cookies Safari. */
  const showPlatformSteps = (): void => {
    const ios = isIosDevice();
    el("avito-modal-subtitle").textContent = ios
      ? "На iPhone cookies Avito нужно экспортировать из Safari — PWA их не видит сам."
      : "Подключение через Kiwi Browser для кнопки «Позвонить».";
    el("avito-setup-android").classList.toggle("hidden", ios);
    el("avito-setup-ios").classList.toggle("hidden", !ios);
  };

  const importCookies = (raw: string): void => {
    if (!raw.trim()) {
      hint.textContent = "Вставьте JSON cookies";
      return;
    }
    importBtn.disabled = true;
    void api
      .importAvitoSession(raw)
      .then((data) => {
        hint.textContent = "Сохранено";
        cookiesInput.value = "";
        render({ connected: data.connected, label: data.label });
        close();
      })
      .catch((err: unknown) => {
        hint.textContent = err instanceof Error ? err.message : String(err);
      })
      .finally(() => {
        importBtn.disabled = false;
      });
  };

  const reset = async (): Promise<void> => {
    await api.clearAvitoSession().catch(() => undefined);
    cookiesInput.value = "";
    render({ connected: false });
    hint.textContent = "Подключение сброшено";
  };

  const requestPhone = async (ad: Ad, btn: HTMLButtonElement): Promise<void> => {
    if (!ad.can_call) {
      window.open(itemWebUrl(ad), "_blank", "noopener");
      return;
    }
    const id = String(ad.id);
    const cached = phones.get(id);
    if (cached) {
      dial(cached);
      return;
    }
    if (!connected) {
      open();
      return;
    }

    const label = new ButtonLabel(btn);
    label.set("…");
    btn.disabled = true;
    try {
      const result = await api.avitoPhone(id);
      if (result.ok && result.phone) {
        phones.set(id, result.phone);
        label.set(result.phone);
        dial(result.phone);
        return;
      }
      label.restore();
      if (SESSION_LOST_CODES.has(result.code || "")) {
        setConnected(false);
        open();
      } else {
        window.alert(result.error || "Номер недоступен");
      }
    } catch (err) {
      label.restore();
      window.alert(err instanceof Error ? err.message : String(err));
    } finally {
      btn.disabled = false;
    }
  };

  el("avito-close").addEventListener("click", close);
  modal.addEventListener("click", (ev) => {
    // Клик по затемнению за пределами окна закрывает его.
    if (ev.target === modal) close();
  });
  el("avito-open-login").addEventListener("click", () => {
    window.open("https://m.avito.ru", "_blank", "noopener");
    hint.textContent = HINT_AFTER_LOGIN;
  });
  importBtn.addEventListener("click", () => importCookies(cookiesInput.value));
  resetBtn.addEventListener("click", () => {
    resetBtn.disabled = true;
    void reset().finally(() => {
      resetBtn.disabled = false;
    });
  });

  return { open, refresh, requestPhone };
}

/** Подпись кнопки «Позвонить»: показывает номер, умеет вернуть исходный текст. */
class ButtonLabel {
  private readonly target: HTMLElement;
  private readonly original: string;

  constructor(btn: HTMLButtonElement) {
    this.target = btn.querySelector("span") ?? btn;
    this.original = this.target.textContent || "Позвонить";
  }

  set(text: string): void {
    this.target.textContent = text;
  }

  restore(): void {
    this.target.textContent = this.original;
  }
}

/**
 * Позвонить по номеру в этом же окне.
 *
 * Новую вкладку не открываем: `about:blank` и `tel:` с `target=_blank`
 * оставляют белую страницу, которую потом приходится закрывать руками.
 * Схему `tel:` браузер отдаёт системе и страницу не выгружает.
 */
function dial(phone: string): void {
  const digits = phone.trim().replace(/[^\d+]/g, "") || phone.trim();
  if (!digits) return;
  window.location.assign(`tel:${digits}`);
}
