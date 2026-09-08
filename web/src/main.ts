import { initPushServiceWorker } from "./push-notify";
import { initTheme } from "./theme";
import { api } from "./api";
import { mountMonitor } from "./monitor";
import { mountUserMenu } from "./user-menu";
import "./styles.css";

initTheme();
void initPushServiceWorker();

let monitorRef: ReturnType<typeof mountMonitor> | null = null;

let userMenuRef: ReturnType<typeof mountUserMenu> | null = null;

const monitor = mountMonitor({
  isPushEnabled: () => userMenuRef?.isPushEnabled() ?? false,
  onAvitoStatus: (connected, label) => userMenuRef?.setAvitoStatus(connected, label),
  closeUserMenu: () => userMenuRef?.close(),
});
monitorRef = monitor;

userMenuRef = mountUserMenu({
  onAvitoClick: () => monitorRef?.openAvito(),
  onLogout: () => {
    void api.logout().then(() => {
      authBtn.classList.remove("hidden");
      shellUser.classList.add("hidden");
      shellAppTools.classList.add("hidden");
      app.classList.add("hidden");
      monitor.closeFilters();
      showAuth();
    });
  },
  closeFiltersDrawer: () => monitor.closeFilters(),
});

const app = document.getElementById("app") as HTMLElement;
const authScreen = document.getElementById("auth-screen") as HTMLElement;
const authLogin = document.getElementById("auth-login") as HTMLInputElement;
const authPassword = document.getElementById("auth-password") as HTMLInputElement;
const authSubmit = document.getElementById("auth-submit") as HTMLButtonElement;
const authHint = document.getElementById("auth-hint") as HTMLElement;
const authBtn = document.getElementById("auth-btn") as HTMLButtonElement;
const shellUser = document.getElementById("shell-user") as HTMLElement;
const shellAppTools = document.getElementById("shell-app-tools") as HTMLElement;

function showAuth(): void {
  document.body.classList.add("auth-gate");
  authScreen.classList.remove("hidden");
  app.classList.add("hidden");
  shellUser.classList.add("hidden");
  shellAppTools.classList.add("hidden");
  authBtn.classList.remove("hidden");
  userMenuRef?.close();
  authHint.textContent = "";
  authPassword.value = "";
}

function showApp(username: string): void {
  document.body.classList.remove("auth-gate");
  authScreen.classList.add("hidden");
  app.classList.remove("hidden");
  userMenuRef?.setUsername(username);
  shellUser.classList.remove("hidden");
  shellAppTools.classList.remove("hidden");
  authBtn.classList.add("hidden");
  monitor.show();
}

authSubmit.addEventListener("click", () => {
  authSubmit.disabled = true;
  authHint.textContent = "";
  void api
    .login(authLogin.value, authPassword.value)
    .then((status) => showApp(status.username || "admin"))
    .catch((err) => {
      authHint.textContent = err instanceof Error ? err.message : String(err);
    })
    .finally(() => {
      authSubmit.disabled = false;
    });
});

authBtn.addEventListener("click", () => {
  showAuth();
});

document.getElementById("auth-form")?.addEventListener("submit", (ev) => {
  ev.preventDefault();
  authSubmit.click();
});

void api
  .authStatus()
  .then((status) => {
    if (status.logged_in) {
      showApp(status.username || "admin");
    } else {
      showAuth();
    }
  })
  .catch(() => showAuth());
