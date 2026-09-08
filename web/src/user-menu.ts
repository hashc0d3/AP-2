import { api } from "./api";
import { isLightTheme, setLightTheme } from "./theme";
import {
  canUsePush,
  disableWebPush,
  enableWebPush,
  hasNotificationApi,
  initPushServiceWorker,
  isWebPushSubscribed,
  pushBlockReason,
  pushEnableHint,
  pushPermission,
  pushStatusLine,
  pwaInstallHint,
  showTestNotification,
  subscribeWebPush,
} from "./push-notify";
import {
  addSellerToBlacklist,
  BLACKLIST_EVENT,
  loadSellerBlacklist,
  removeSellerFromBlacklist,
  syncSellerBlacklist,
} from "./seller-blacklist";
import { showToast } from "./toast";
import { escapeHtml } from "./format";

const PUSH_KEY = "parser1.pushNotify";

export function formatUserLabel(username: string): string {
  const name = username.trim();
  if (!name) return "Пользователь";
  const display = name.charAt(0).toUpperCase() + name.slice(1);
  return `Пользователь: ${display}`;
}

function formatUserShort(username: string): string {
  const name = username.trim();
  if (!name) return "Пользователь";
  return name.charAt(0).toUpperCase() + name.slice(1);
}

function themeLabel(light: boolean): string {
  return light ? "светлый" : "тёмный";
}

type UserMenuOptions = {
  onAvitoClick: () => void;
  onLogout: () => void;
  closeFiltersDrawer?: () => void;
};

export function mountUserMenu(opts: UserMenuOptions): {
  setUsername: (username: string) => void;
  setAvitoStatus: (connected: boolean, label?: string) => void;
  isPushEnabled: () => boolean;
  refreshBalance: () => Promise<void>;
  open: () => void;
  close: () => void;
} {
  const btn = document.getElementById("user-menu-btn") as HTMLButtonElement;
  const btnLabel = document.getElementById("user-menu-btn-label") as HTMLElement;
  const btnAvatar = document.getElementById("user-menu-btn-avatar") as HTMLElement;
  const back = document.getElementById("user-drawer-back") as HTMLElement;
  const drawer = document.getElementById("user-drawer") as HTMLElement;
  const title = document.getElementById("user-drawer-title") as HTMLElement;
  const avatar = document.getElementById("user-drawer-avatar") as HTMLElement;
  const closeBtn = document.getElementById("user-drawer-close") as HTMLButtonElement;
  const avitoBtn = document.getElementById("user-menu-avito") as HTMLButtonElement;
  const avitoStatus = document.getElementById("user-menu-avito-status") as HTMLElement;
  const avitoBadge = document.getElementById("user-menu-avito-badge") as HTMLElement;
  const pushCheck = document.getElementById("push-notify") as HTMLInputElement;
  const pushStatus = document.getElementById("push-notify-status") as HTMLElement;
  const pushRequestBtn = document.getElementById("push-notify-request") as HTMLButtonElement;
  const logoutBtn = document.getElementById("user-menu-logout") as HTMLButtonElement;
  const themeBtn = document.getElementById("theme-toggle") as HTMLButtonElement;
  const themeValue = document.getElementById("theme-toggle-value") as HTMLElement;
  const themePill = document.getElementById("theme-toggle-pill") as HTMLElement;
  const themeIconSun = themeBtn.querySelector(".theme-icon-sun") as SVGElement;
  const themeIconMoon = themeBtn.querySelector(".theme-icon-moon") as SVGElement;
  const resourceBalanceHint = document.getElementById(
    "user-menu-resource-balance-hint",
  ) as HTMLElement;
  const resourceBalanceValue = document.getElementById(
    "user-menu-resource-balance-value",
  ) as HTMLElement;
  const tabSettings = document.getElementById("user-tab-settings") as HTMLButtonElement;
  const tabBlacklist = document.getElementById("user-tab-blacklist") as HTMLButtonElement;
  const panelSettings = document.getElementById("user-panel-settings") as HTMLElement;
  const panelBlacklist = document.getElementById("user-panel-blacklist") as HTMLElement;
  const blacklistForm = document.getElementById("blacklist-add-form") as HTMLFormElement;
  const blacklistInput = document.getElementById("blacklist-add-input") as HTMLInputElement;
  const blacklistList = document.getElementById("blacklist-list") as HTMLUListElement;
  const blacklistEmpty = document.getElementById("blacklist-empty") as HTMLElement;

  let pushEnabled = false;
  let balanceLoading = false;
  let activeTab: "settings" | "blacklist" = "settings";

  const refreshBalance = async () => {
    if (balanceLoading) return;
    balanceLoading = true;
    resourceBalanceValue.textContent = "…";
    resourceBalanceHint.textContent = "Загрузка…";
    try {
      const data = await api.resourceBalance();
      resourceBalanceValue.textContent = formatBalance(data.balance);
      resourceBalanceHint.textContent = "Сервисный счёт";
      resourceBalanceValue.classList.remove("error");
    } catch (err) {
      resourceBalanceValue.textContent = "—";
      resourceBalanceHint.textContent = err instanceof Error ? err.message : "Не удалось загрузить";
      resourceBalanceValue.classList.add("error");
    } finally {
      balanceLoading = false;
    }
  };

  const formatBalance = (value: number): string => {
    const rounded = Math.round(value * 100) / 100;
    return `${rounded.toLocaleString("ru-RU", { minimumFractionDigits: 0, maximumFractionDigits: 2 })} ₽`;
  };

  const renderBlacklist = () => {
    const items = loadSellerBlacklist();
    blacklistEmpty.classList.toggle("hidden", items.length > 0);
    blacklistList.innerHTML = items
      .map(
        (seller) =>
          `<li class="blacklist-item">
        <span class="blacklist-item-name">${escapeHtml(seller)}</span>
        <button type="button" class="blacklist-item-remove" data-seller="${escapeHtml(seller)}" aria-label="Удалить из чёрного списка" title="Удалить">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <path d="M6 6l12 12M18 6L6 18"></path>
          </svg>
        </button>
      </li>`,
      )
      .join("");
  };

  const setDrawerTab = (tab: "settings" | "blacklist") => {
    activeTab = tab;
    const settingsActive = tab === "settings";
    tabSettings.classList.toggle("active", settingsActive);
    tabBlacklist.classList.toggle("active", !settingsActive);
    tabSettings.setAttribute("aria-selected", settingsActive ? "true" : "false");
    tabBlacklist.setAttribute("aria-selected", settingsActive ? "false" : "true");
    panelSettings.classList.toggle("hidden", !settingsActive);
    panelBlacklist.classList.toggle("hidden", settingsActive);
    if (!settingsActive) renderBlacklist();
  };

  const enablePush = async (): Promise<boolean> => {
    if (!canUsePush()) {
      showToast(pushEnableHint(pushBlockReason()), "error");
      return false;
    }

    const result = await enableWebPush();
    if (!result.ok) {
      showToast(pushEnableHint(result.reason || pushBlockReason()), "error");
      return false;
    }

    savePush(true);
    pushCheck.checked = true;
    syncPushUi();
    const pwaHint = pwaInstallHint();
    if (!isWebPushSubscribed() && showTestNotification()) {
      showToast(pwaHint || "Уведомления включены (вкладка)", pwaHint ? "info" : "success");
    } else if (pwaHint) {
      showToast(pwaHint, "info");
    }
    return true;
  };

  const syncPushUi = () => {
    if (!hasNotificationApi()) {
      pushCheck.disabled = true;
      pushCheck.checked = false;
      pushEnabled = false;
      pushStatus.textContent = pushStatusLine();
      pushStatus.classList.add("is-error");
      pushRequestBtn.classList.add("hidden");
      return;
    }

    pushCheck.disabled = false;
    let status = pushStatusLine();
    const pwaHint = pwaInstallHint();
    if (pwaHint && pushEnabled && Notification.permission === "granted") {
      status = pwaHint;
    }
    pushStatus.textContent = status;
    pushStatus.classList.toggle("is-error", !canUsePush() || Notification.permission === "denied");

    const canRequest = canUsePush() && Notification.permission === "default";
    pushRequestBtn.classList.toggle("hidden", !canRequest);

    if (!canUsePush()) {
      pushCheck.checked = false;
      if (pushEnabled) {
        pushEnabled = false;
        try {
          localStorage.setItem(PUSH_KEY, "off");
        } catch {
          /* empty */
        }
      }
      return;
    }

    const permission = pushPermission();
    if (permission === "granted") {
      pushCheck.checked = pushEnabled;
      return;
    }

    if (pushEnabled) {
      pushEnabled = false;
      try {
        localStorage.setItem(PUSH_KEY, "off");
      } catch {
        /* empty */
      }
    }
    pushCheck.checked = false;
  };

  const loadPush = () => {
    try {
      pushEnabled = localStorage.getItem(PUSH_KEY) === "on";
    } catch {
      pushEnabled = false;
    }
    syncPushUi();
    if (pushEnabled && Notification.permission === "granted") {
      void subscribeWebPush().catch(() => undefined);
    }
  };

  const savePush = (on: boolean) => {
    pushEnabled = on;
    try {
      localStorage.setItem(PUSH_KEY, on ? "on" : "off");
    } catch {
      /* empty */
    }
  };

  const setOpen = (open: boolean) => {
    back.classList.toggle("hidden", !open);
    requestAnimationFrame(() => back.classList.toggle("open", open));
    back.setAttribute("aria-hidden", open ? "false" : "true");
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    document.body.classList.toggle("user-drawer-open", open);
    if (open) {
      setDrawerTab(activeTab);
      syncPushUi();
      opts.closeFiltersDrawer?.();
      if (activeTab === "settings") void refreshBalance();
    }
  };

  const renderTheme = () => {
    const light = isLightTheme();
    themeValue.textContent = themeLabel(light);
    themePill.textContent = light ? "На тёмную" : "На светлую";
    themeBtn.classList.toggle("is-light", light);
    themeIconSun.classList.toggle("hidden", !light);
    themeIconMoon.classList.toggle("hidden", light);
  };

  const setUsername = (username: string) => {
    const label = formatUserLabel(username);
    const short = username.trim();
    const initial = short ? short.charAt(0).toUpperCase() : "S";
    btnLabel.textContent = formatUserShort(username);
    title.textContent = label;
    avatar.textContent = initial;
    btnAvatar.textContent = initial;
  };

  const setAvitoStatus = (connected: boolean, label = "") => {
    avitoBtn.classList.toggle("on", connected);
    avitoBtn.classList.toggle("off", !connected);
    avitoStatus.textContent = connected ? `Подключён${label ? ` · ${label}` : ""}` : "Не подключён";
    avitoBadge.textContent = connected ? "ON" : "OFF";
    avitoBadge.classList.toggle("on", connected);
    avitoBadge.classList.toggle("off", !connected);
  };

  renderTheme();

  btn.addEventListener("click", () => setOpen(true));
  closeBtn.addEventListener("click", () => setOpen(false));
  back.addEventListener("click", (ev) => {
    if (ev.target === back) setOpen(false);
  });
  drawer.addEventListener("click", (ev) => ev.stopPropagation());

  avitoBtn.addEventListener("click", () => {
    setOpen(false);
    opts.onAvitoClick();
  });

  pushCheck.addEventListener("change", () => {
    if (!pushCheck.checked) {
      savePush(false);
      void disableWebPush().then(syncPushUi);
      return;
    }
    void enablePush().then((ok) => {
      if (!ok) pushCheck.checked = false;
    });
  });

  pushRequestBtn.addEventListener("click", () => {
    void enablePush();
  });

  logoutBtn.addEventListener("click", () => {
    setOpen(false);
    opts.onLogout();
  });

  themeBtn.addEventListener("click", () => {
    setLightTheme(!isLightTheme());
    renderTheme();
  });

  tabSettings.addEventListener("click", () => setDrawerTab("settings"));
  tabBlacklist.addEventListener("click", () => setDrawerTab("blacklist"));

  blacklistForm.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const value = blacklistInput.value.trim();
    if (!value) {
      blacklistInput.focus();
      return;
    }
    if (addSellerToBlacklist(value)) {
      blacklistInput.value = "";
      renderBlacklist();
      void syncSellerBlacklist();
      showToast(`«${value}» в чёрном списке`, "success");
    } else {
      showToast("Продавец уже в списке", "info");
    }
  });

  blacklistList.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>("button[data-seller]");
    if (!btn) return;
    const seller = btn.dataset.seller || "";
    if (!seller) return;
    removeSellerFromBlacklist(seller);
    renderBlacklist();
    void syncSellerBlacklist();
    showToast(`«${seller}» удалён из чёрного списка`, "info");
  });

  window.addEventListener(BLACKLIST_EVENT, () => {
    if (!panelBlacklist.classList.contains("hidden")) renderBlacklist();
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && back.classList.contains("open")) setOpen(false);
  });

  loadPush();
  void initPushServiceWorker();

  return {
    setUsername,
    setAvitoStatus,
    isPushEnabled: () => pushEnabled,
    refreshBalance,
    open: () => setOpen(true),
    close: () => setOpen(false),
  };
}
