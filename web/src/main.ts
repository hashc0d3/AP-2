import { api } from "./api";
import { mountMonitor } from "./monitor";
import "./styles.css";

const monitor = mountMonitor();
const app = document.getElementById("app") as HTMLElement;
const authModal = document.getElementById("auth-modal") as HTMLElement;
const authLogin = document.getElementById("auth-login") as HTMLInputElement;
const authPassword = document.getElementById("auth-password") as HTMLInputElement;
const authSubmit = document.getElementById("auth-submit") as HTMLButtonElement;
const authHint = document.getElementById("auth-hint") as HTMLElement;
const authBtn = document.getElementById("auth-btn") as HTMLButtonElement;
const shellBadge = document.getElementById("shell-badge") as HTMLElement;

function showAuth(): void {
  authModal.classList.remove("hidden");
  app.classList.add("hidden");
  authHint.textContent = "";
  authPassword.value = "";
}

function showApp(username: string): void {
  authModal.classList.add("hidden");
  app.classList.remove("hidden");
  shellBadge.textContent = username;
  shellBadge.classList.remove("hidden");
  authBtn.textContent = "Выйти";
  monitor.show();
}

authSubmit.addEventListener("click", () => {
  authSubmit.disabled = true;
  authHint.textContent = "";
  void api.login(authLogin.value, authPassword.value)
    .then((status) => showApp(status.username || "sotik77"))
    .catch((err) => {
      authHint.textContent = err instanceof Error ? err.message : String(err);
    })
    .finally(() => {
      authSubmit.disabled = false;
    });
});

authBtn.addEventListener("click", () => {
  void api.logout().then(() => {
    authBtn.textContent = "Войти";
    shellBadge.classList.add("hidden");
    app.classList.add("hidden");
    showAuth();
  });
});

document.getElementById("auth-form")?.addEventListener("submit", (ev) => {
  ev.preventDefault();
  authSubmit.click();
});

void api.authStatus().then((status) => {
  if (status.logged_in) {
    showApp(status.username || "sotik77");
  } else {
    showAuth();
  }
}).catch(() => showAuth());
