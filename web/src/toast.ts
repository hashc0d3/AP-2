export type ToastKind = "info" | "success" | "error";

let root: HTMLElement | null = null;

function getRoot(): HTMLElement {
  if (!root) {
    root = document.createElement("div");
    root.className = "toast-root";
    root.setAttribute("aria-live", "polite");
    document.body.appendChild(root);
  }
  return root;
}

export function showToast(message: string, kind: ToastKind = "info", durationMs = 3500): void {
  const text = message.trim();
  if (!text) return;

  const el = document.createElement("div");
  el.className = `toast toast-${kind}`;
  el.textContent = text;
  getRoot().appendChild(el);

  requestAnimationFrame(() => el.classList.add("show"));

  const dismiss = () => {
    el.classList.remove("show");
    el.classList.add("hide");
    el.addEventListener("transitionend", () => el.remove(), { once: true });
    window.setTimeout(() => el.remove(), 400);
  };

  const timer = window.setTimeout(dismiss, durationMs);
  el.addEventListener("click", () => {
    window.clearTimeout(timer);
    dismiss();
  });
}
