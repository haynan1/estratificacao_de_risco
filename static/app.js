// ---------- CPF mask ----------
function formatCpf(value) {
  const digits = value.replace(/\D/g, "").slice(0, 11);
  if (digits.length <= 3) return digits;
  if (digits.length <= 6) return `${digits.slice(0, 3)}.${digits.slice(3)}`;
  if (digits.length <= 9) return `${digits.slice(0, 3)}.${digits.slice(3, 6)}.${digits.slice(6)}`;
  return `${digits.slice(0, 3)}.${digits.slice(3, 6)}.${digits.slice(6, 9)}-${digits.slice(9)}`;
}

document.querySelectorAll("[data-cpf-mask]").forEach((input) => {
  input.value = formatCpf(input.value);
  input.addEventListener("input", () => {
    input.value = formatCpf(input.value);
  });
});

// ---------- Age from birth date ----------
function calculateAgeFromDate(value) {
  if (!value) return "";
  const birthDate = new Date(`${value}T00:00:00`);
  if (Number.isNaN(birthDate.getTime())) return "";

  const today = new Date();
  let age = today.getFullYear() - birthDate.getFullYear();
  const monthDiff = today.getMonth() - birthDate.getMonth();
  const hasBirthdayPassed = monthDiff > 0 || (monthDiff === 0 && today.getDate() >= birthDate.getDate());
  if (!hasBirthdayPassed) age -= 1;
  return age >= 0 ? age : "";
}

document.querySelectorAll("[data-birth-date]").forEach((birthInput) => {
  const form = birthInput.closest("form");
  const ageInput = form?.querySelector("[data-age-field]");
  if (!ageInput) return;

  const updateAge = () => {
    ageInput.value = calculateAgeFromDate(birthInput.value);
  };

  birthInput.addEventListener("change", updateAge);
  birthInput.addEventListener("input", updateAge);
  updateAge();
});

// ---------- App confirmation modal ----------
const confirmModalEl = document.getElementById("confirmModal");
const confirmTitle = document.getElementById("confirmModalTitle");
const confirmMessage = document.getElementById("confirmModalMessage");
const confirmSubmit = document.querySelector("[data-confirm-submit]");
const confirmModal = confirmModalEl ? new bootstrap.Modal(confirmModalEl) : null;
let pendingConfirmForm = null;

document.querySelectorAll("[data-confirm-form]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (form.dataset.confirmed === "true") return;
    event.preventDefault();
    pendingConfirmForm = form;
    if (confirmTitle) confirmTitle.textContent = form.dataset.confirmTitle || "Confirmar ação";
    if (confirmMessage) confirmMessage.textContent = form.dataset.confirmMessage || "Deseja continuar?";
    confirmModal?.show();
  });
});

confirmSubmit?.addEventListener("click", () => {
  if (!pendingConfirmForm) return;
  pendingConfirmForm.dataset.confirmed = "true";
  confirmModal?.hide();
  pendingConfirmForm.requestSubmit();
});

confirmModalEl?.addEventListener("hidden.bs.modal", () => {
  if (pendingConfirmForm?.dataset.confirmed !== "true") {
    pendingConfirmForm = null;
  }
});

// ---------- Theme ----------
function applyTheme(theme) {
  document.documentElement.setAttribute("data-bs-theme", theme);
  localStorage.setItem("theme", theme);
}

document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
  button.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-bs-theme") || "dark";
    applyTheme(current === "dark" ? "light" : "dark");
  });
});

// ---------- Sidebar (mobile) ----------
const sidebar = document.getElementById("sidebar");
const backdrop = document.querySelector(".sidebar-backdrop");
const appMain = document.querySelector(".app-main");
const isMobileSidebar = () => window.matchMedia("(max-width: 991.98px)").matches;

function openSidebar() {
  sidebar?.classList.add("is-open");
  backdrop?.classList.add("is-open");
}
function closeSidebar() {
  sidebar?.classList.remove("is-open");
  backdrop?.classList.remove("is-open");
}
function applySidebarCollapsed(collapsed) {
  sidebar?.classList.toggle("is-collapsed", collapsed);
  appMain?.classList.toggle("is-sidebar-collapsed", collapsed);
  localStorage.setItem("sidebarCollapsed", collapsed ? "true" : "false");
}
function toggleSidebar() {
  if (isMobileSidebar()) {
    if (sidebar?.classList.contains("is-open")) closeSidebar();
    else openSidebar();
    return;
  }
  applySidebarCollapsed(!sidebar?.classList.contains("is-collapsed"));
}

if (!isMobileSidebar()) {
  applySidebarCollapsed(localStorage.getItem("sidebarCollapsed") === "true");
}

document.querySelectorAll("[data-sidebar-open]").forEach((b) => b.addEventListener("click", openSidebar));
document.querySelectorAll("[data-sidebar-close]").forEach((b) => b.addEventListener("click", closeSidebar));
document.querySelectorAll("[data-sidebar-toggle]").forEach((b) => b.addEventListener("click", toggleSidebar));
window.addEventListener("resize", () => {
  if (isMobileSidebar()) {
    sidebar?.classList.remove("is-collapsed");
    appMain?.classList.remove("is-sidebar-collapsed");
  } else {
    closeSidebar();
    applySidebarCollapsed(localStorage.getItem("sidebarCollapsed") === "true");
  }
});

// ---------- Flash dismiss ----------
document.querySelectorAll("[data-flash-close]").forEach((button) => {
  button.addEventListener("click", () => {
    const toast = button.closest(".toast-flash");
    if (!toast) return;
    toast.style.transition = "opacity .2s ease, transform .2s ease";
    toast.style.opacity = "0";
    toast.style.transform = "translateY(-6px)";
    setTimeout(() => toast.remove(), 200);
  });
});

// ---------- Animate risk bars on entry ----------
function animateBars() {
  document.querySelectorAll(".track-fill[data-width]").forEach((fill) => {
    requestAnimationFrame(() => {
      fill.style.width = `${fill.dataset.width}%`;
    });
  });
}
if (document.readyState !== "loading") animateBars();
else document.addEventListener("DOMContentLoaded", animateBars);

// ---------- Export loading state ----------
document.querySelectorAll("[data-loading-on-click]").forEach((link) => {
  link.addEventListener("click", () => {
    link.setAttribute("data-loading", "");
    // Downloads não disparam navegação; libera o botão após um intervalo curto.
    setTimeout(() => link.removeAttribute("data-loading"), 2500);
  });
});
