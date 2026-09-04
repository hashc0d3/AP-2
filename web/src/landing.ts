import { api } from "./api";
import type { BillingStatus, PromoQuote } from "./types";

type Intent = "login" | "pay" | "trial";

type Handlers = {
  onStatus: (status: BillingStatus) => void;
  onActivated: (status: BillingStatus) => void;
};

function $(id: string): HTMLElement {
  const el = document.getElementById(id);
  if (!el) throw new Error(`#${id} не найден`);
  return el;
}

function input(id: string): HTMLInputElement {
  return $(id) as HTMLInputElement;
}

export function mountLanding(handlers: Handlers): {
  render: (status: BillingStatus) => void;
  show: () => void;
  hide: () => void;
  openAuth: (intent?: Intent) => void;
} {
  const landing = $("landing");
  const modal = $("pay-modal");
  const trialHint = $("trial-hint");
  const promoHint = $("promo-hint");
  const planPrice = $("plan-price");
  const paySum = $("pay-sum");
  const payHint = $("pay-hint");
  const payTitle = $("pay-title");
  const authStep = $("auth-step");
  const accountStep = $("account-step");
  const accountHint = $("account-hint");
  const payConfirm = $("pay-confirm") as HTMLButtonElement;
  const payTrial = $("pay-trial") as HTMLButtonElement;
  const trialBtns = [$("hero-trial"), $("plan-trial")];
  let quote: PromoQuote = { code: "", price: 3000, discount: 0, note: "" };
  let intent: Intent = "login";
  let status: BillingStatus | null = null;

  const setQuote = (next: PromoQuote) => {
    quote = next;
    const label = next.price.toLocaleString("ru-RU") + " ₽";
    planPrice.innerHTML = `${label} <small>/ 30 дней</small>`;
    ($("plan-pay") as HTMLButtonElement).textContent = `Оформить за ${label}`;
    paySum.textContent = next.code
      ? `К оплате ${label} · ${next.code}${next.note ? " · " + next.note : ""}`
      : `К оплате ${label}`;
  };

  const render = (next: BillingStatus) => {
    status = next;
    trialBtns.forEach((btn) => {
      (btn as HTMLButtonElement).disabled = next.active || (next.logged_in && !next.trial_available);
    });
    if (next.active) {
      trialHint.textContent = "Подписка активна";
    } else if (next.logged_in && !next.trial_available) {
      trialHint.textContent = "Пробный уже использован";
    } else {
      trialHint.textContent = "Нужен подтверждённый телефон";
    }
  };

  const show = () => landing.classList.remove("hidden");
  const hide = () => landing.classList.add("hidden");

  const showAuthStep = () => {
    authStep.classList.remove("hidden");
    accountStep.classList.add("hidden");
    payConfirm.classList.add("hidden");
    payTrial.classList.add("hidden");
    payTitle.textContent = intent === "pay" ? "Оплата" : intent === "trial" ? "Пробный доступ" : "Вход";
    paySum.textContent = "Код 1111 создаёт учётную запись";
    payHint.textContent = "";
    input("pay-code").value = "";
  };

  const showAccountStep = (next: BillingStatus) => {
    authStep.classList.add("hidden");
    accountStep.classList.remove("hidden");
    const label = next.account?.phone_label || next.phone;
    accountHint.textContent = `${next.created ? "Аккаунт создан" : "Вход выполнен"} · ${label}`;
    payTitle.textContent = "Готово";
    payConfirm.classList.toggle("hidden", intent !== "pay");
    payTrial.classList.toggle("hidden", intent !== "trial" || !next.trial_available);
    if (intent === "pay") {
      paySum.textContent = `К оплате ${quote.price.toLocaleString("ru-RU")} ₽`;
    } else if (intent === "trial") {
      paySum.textContent = "Доступ на 24 часа";
    } else {
      paySum.textContent = next.active ? "Лента доступна" : "Осталось выбрать тариф";
    }
  };

  const openAuth = (next: Intent = "login") => {
    intent = next;
    if (status?.logged_in && next === "login") {
      handlers.onStatus(status);
      return;
    }
    if (status?.logged_in && next === "pay") {
      intent = "pay";
      modal.classList.remove("hidden");
      showAccountStep(status);
      payConfirm.classList.remove("hidden");
      paySum.textContent = `К оплате ${quote.price.toLocaleString("ru-RU")} ₽`;
      return;
    }
    if (status?.logged_in && next === "trial") {
      void runTrial();
      return;
    }
    modal.classList.remove("hidden");
    showAuthStep();
  };

  const closePay = () => modal.classList.add("hidden");

  $("hero-pay").addEventListener("click", () => openAuth("pay"));
  $("plan-pay").addEventListener("click", () => openAuth("pay"));
  $("pay-close").addEventListener("click", closePay);
  modal.addEventListener("click", (ev) => {
    if (ev.target === modal) closePay();
  });

  const runTrial = async () => {
    trialHint.textContent = "Активирую…";
    try {
      const next = await api.trial();
      handlers.onActivated(next);
      closePay();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      trialHint.textContent = message;
      accountHint.textContent = message;
    }
  };
  trialBtns.forEach((btn) => btn.addEventListener("click", () => openAuth("trial")));

  $("promo-apply").addEventListener("click", async () => {
    promoHint.textContent = "Проверяю…";
    try {
      const next = await api.quotePromo(input("promo-input").value);
      setQuote(next);
      promoHint.textContent = next.note || (next.code ? `Промокод ${next.code} применён` : "");
    } catch (err) {
      setQuote({ code: "", price: 3000, discount: 0, note: "" });
      promoHint.textContent = err instanceof Error ? err.message : String(err);
    }
  });

  $("pay-sms").addEventListener("click", async () => {
    payHint.textContent = "Отправляю код…";
    try {
      await api.sendSms(input("pay-phone").value);
      payHint.textContent = "Код отправлен. Введите 1111";
    } catch (err) {
      payHint.textContent = err instanceof Error ? err.message : String(err);
    }
  });

  $("pay-verify").addEventListener("click", async () => {
    payHint.textContent = "Проверяю код…";
    try {
      const next = await api.verify(input("pay-phone").value, input("pay-code").value);
      handlers.onStatus(next);
      showAccountStep(next);
      if (intent === "trial" && next.trial_available) {
        await runTrial();
        return;
      }
      if (intent === "login" && next.active) {
        closePay();
        handlers.onActivated(next);
      }
    } catch (err) {
      payHint.textContent = err instanceof Error ? err.message : String(err);
    }
  });

  payConfirm.addEventListener("click", async () => {
    accountHint.textContent = "Оплачиваю…";
    try {
      const next = await api.pay(quote.code || input("promo-input").value);
      closePay();
      handlers.onActivated(next);
    } catch (err) {
      accountHint.textContent = err instanceof Error ? err.message : String(err);
    }
  });

  payTrial.addEventListener("click", () => { void runTrial(); });

  return { render, show, hide, openAuth };
}
