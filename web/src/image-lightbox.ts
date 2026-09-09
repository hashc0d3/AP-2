import { imgSrcLarge } from "./format";

let root: HTMLElement | null = null;
let imgEl: HTMLImageElement | null = null;
let keyHandler: ((ev: KeyboardEvent) => void) | null = null;

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
  imgEl?.addEventListener("click", closeImageLightbox);

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

/** Открыть то же фото, что на карточке, крупнее. */
export function openImageLightbox(url: string): void {
  if (!url) return;

  ensureRoot();
  if (imgEl) imgEl.src = imgSrcLarge(url);
  root?.classList.remove("hidden");
  document.body.classList.add("lightbox-open");
  bindKeys();
}

export function closeImageLightbox(): void {
  if (!root) return;
  root.classList.add("hidden");
  document.body.classList.remove("lightbox-open");
  if (imgEl) imgEl.removeAttribute("src");
}
