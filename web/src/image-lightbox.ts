import { imgSrcLarge } from "./format";

let root: HTMLElement | null = null;
let imgEl: HTMLImageElement | null = null;
let counterEl: HTMLElement | null = null;
let prevBtn: HTMLButtonElement | null = null;
let nextBtn: HTMLButtonElement | null = null;
let images: string[] = [];
let index = 0;
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
      <button type="button" class="image-lightbox-nav prev" aria-label="Предыдущее">‹</button>
      <img class="image-lightbox-img" alt="" />
      <button type="button" class="image-lightbox-nav next" aria-label="Следующее">›</button>
      <p class="image-lightbox-counter"></p>
    </div>
  `;
  document.body.appendChild(root);

  imgEl = root.querySelector(".image-lightbox-img") as HTMLImageElement;
  counterEl = root.querySelector(".image-lightbox-counter") as HTMLElement;
  prevBtn = root.querySelector(".image-lightbox-nav.prev") as HTMLButtonElement;
  nextBtn = root.querySelector(".image-lightbox-nav.next") as HTMLButtonElement;

  root.querySelector(".image-lightbox-back")?.addEventListener("click", closeImageLightbox);
  root.querySelector(".image-lightbox-close")?.addEventListener("click", closeImageLightbox);
  prevBtn?.addEventListener("click", (ev) => {
    ev.stopPropagation();
    showIndex(index - 1);
  });
  nextBtn?.addEventListener("click", (ev) => {
    ev.stopPropagation();
    showIndex(index + 1);
  });
  root.querySelector(".image-lightbox-panel")?.addEventListener("click", (ev) => {
    ev.stopPropagation();
  });

  return root;
}

function updateView(): void {
  if (!imgEl || !counterEl || !prevBtn || !nextBtn) return;
  const url = images[index];
  if (!url) return;

  imgEl.src = imgSrcLarge(url);
  const multi = images.length > 1;
  counterEl.textContent = multi ? `${index + 1} / ${images.length}` : "";
  counterEl.classList.toggle("hidden", !multi);
  prevBtn.classList.toggle("hidden", !multi);
  nextBtn.classList.toggle("hidden", !multi);
  prevBtn.disabled = index <= 0;
  nextBtn.disabled = index >= images.length - 1;
}

function showIndex(next: number): void {
  if (!images.length) return;
  index = ((next % images.length) + images.length) % images.length;
  updateView();
}

function bindKeys(): void {
  if (keyHandler) return;
  keyHandler = (ev: KeyboardEvent) => {
    if (!root || root.classList.contains("hidden")) return;
    if (ev.key === "Escape") closeImageLightbox();
    else if (ev.key === "ArrowLeft") showIndex(index - 1);
    else if (ev.key === "ArrowRight") showIndex(index + 1);
  };
  document.addEventListener("keydown", keyHandler);
}

export function openImageLightbox(urls: string[], startIndex = 0): void {
  const list = urls.filter(Boolean);
  if (!list.length) return;

  ensureRoot();
  images = list;
  index = Math.min(Math.max(0, startIndex), list.length - 1);
  updateView();
  root?.classList.remove("hidden");
  document.body.classList.add("lightbox-open");
  bindKeys();
}

export function closeImageLightbox(): void {
  if (!root) return;
  root.classList.add("hidden");
  document.body.classList.remove("lightbox-open");
  if (imgEl) imgEl.removeAttribute("src");
  images = [];
  index = 0;
}
