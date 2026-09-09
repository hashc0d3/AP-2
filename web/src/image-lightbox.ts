let root: HTMLElement | null = null;
let imgEl: HTMLImageElement | null = null;
let keyHandler: ((ev: KeyboardEvent) => void) | null = null;
let unlockTimer = 0;

function ensureRoot(): HTMLElement {
  if (root) return root;

  root = document.createElement("div");
  root.className = "image-lightbox hidden";
  root.setAttribute("role", "dialog");
  root.setAttribute("aria-modal", "true");
  root.setAttribute("aria-label", "Фото объявления");
  root.innerHTML = `
    <button type="button" class="image-lightbox-back" aria-label="Закрыть"></button>
    <div class="image-lightbox-panel">
      <button type="button" class="image-lightbox-close" aria-label="Закрыть">×</button>
      <img class="image-lightbox-img" alt="" />
    </div>
  `;
  document.body.appendChild(root);

  imgEl = root.querySelector(".image-lightbox-img");
  root.querySelector(".image-lightbox-back")?.addEventListener("click", closeImageLightbox);
  root.querySelector(".image-lightbox-close")?.addEventListener("click", closeImageLightbox);

  return root;
}

function bindKeys(): void {
  if (keyHandler) return;
  keyHandler = (ev: KeyboardEvent) => {
    if (!root || root.classList.contains("hidden")) return;
    if (ev.key === "Escape") closeImageLightbox();
  };
  document.addEventListener("keydown", keyHandler);
}

/** Увеличить то же фото, что уже показано на карточке — без галереи. */
export function openImageLightbox(from: HTMLImageElement): void {
  const src = from.currentSrc || from.src;
  if (!src) return;

  ensureRoot();
  if (!root || !imgEl) return;

  imgEl.src = src;
  imgEl.alt = from.alt || "";
  // Иначе тот же клик по карточке сразу закрывает только что открытый слой.
  root.style.pointerEvents = "none";
  root.classList.remove("hidden");
  document.body.classList.add("lightbox-open");
  bindKeys();
  window.clearTimeout(unlockTimer);
  unlockTimer = window.setTimeout(() => {
    if (root) root.style.pointerEvents = "";
  }, 0);
}

export function closeImageLightbox(): void {
  if (!root) return;
  root.classList.add("hidden");
  document.body.classList.remove("lightbox-open");
  root.style.pointerEvents = "";
  if (imgEl) imgEl.removeAttribute("src");
}
