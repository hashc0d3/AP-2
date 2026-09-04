import { api } from "./api";
import { formatLeft } from "./format";
import { mountLanding } from "./landing";
import { mountMonitor } from "./monitor";
import type { BillingStatus } from "./types";
import "./styles.css";

type View = "plans" | "monitor";

const landing = mountLanding({
  onStatus: (status) => apply(status),
  onActivated: (status) => {
    apply(status);
    if (status.active) openView("monitor");
  },
});
const monitor = mountMonitor();
const tabPlans = document.getElementById("tab-plans") as HTMLButtonElement;
const tabMonitor = document.getElementById("tab-monitor") as HTMLButtonElement;
const toMonitor = document.getElementById("to-monitor") as HTMLButtonElement;
const backPlans = document.getElementById("back-plans") as HTMLButtonElement;
const authBtn = document.getElementById("auth-btn") as HTMLButtonElement;
const shellBadge = document.getElementById("shell-badge") as HTMLElement;
let billing: BillingStatus | null = null;
let view: View = "plans";

function emptyStatus(): BillingStatus {
  return {
    logged_in: false,
    account: null,
    active: false,
    plan: "none",
    trial_used: false,
    trial_available: false,
    expires_at: 0,
    seconds_left: 0,
    phone: "",
    price: 3000,
    currency: "RUB",
    paid_days: 30,
    trial_hours: 24,
  };
}

function setBadge(status: BillingStatus): void {
  if (status.logged_in && status.account) {
    const plan = status.active ? formatLeft(status.seconds_left) : "без подписки";
    shellBadge.textContent = `${status.account.phone_label} · ${plan}`;
  } else {
    shellBadge.textContent = "Нет аккаунта";
  }
  authBtn.textContent = status.logged_in ? "Выйти" : "Войти";
  toMonitor.classList.toggle("hidden", !status.active || view === "monitor");
}

function openView(next: View): void {
  if (next === "monitor" && !billing?.active) {
    view = "plans";
    landing.show();
    monitor.hide();
    document.getElementById("plans")?.scrollIntoView({ behavior: "smooth" });
    shellBadge.textContent = billing?.logged_in ? "Нужна подписка" : "Войдите и оформите тариф";
    tabPlans.classList.add("active");
    tabMonitor.classList.remove("active");
    return;
  }
  view = next;
  const onMonitor = next === "monitor";
  if (onMonitor) {
    landing.hide();
    if (billing) monitor.show(billing);
  } else {
    landing.show();
    monitor.hide();
  }
  tabPlans.classList.toggle("active", !onMonitor);
  tabMonitor.classList.toggle("active", onMonitor);
  toMonitor.classList.toggle("hidden", !billing?.active || onMonitor);
}

function apply(status: BillingStatus): void {
  billing = status;
  landing.render(status);
  setBadge(status);
  if (view === "monitor") openView(status.active ? "monitor" : "plans");
}

tabPlans.addEventListener("click", () => openView("plans"));
tabMonitor.addEventListener("click", () => openView("monitor"));
toMonitor.addEventListener("click", () => openView("monitor"));
backPlans.addEventListener("click", () => openView("plans"));
authBtn.addEventListener("click", () => {
  if (billing?.logged_in) {
    void api.logout().then((status) => {
      apply(status);
      openView("plans");
    });
    return;
  }
  landing.openAuth("login");
});

void api.billing().then((status) => {
  apply(status);
  openView(status.active ? "monitor" : "plans");
}).catch(() => {
  apply(emptyStatus());
  openView("plans");
});
