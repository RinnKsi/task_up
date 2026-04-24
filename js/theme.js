/* Smart Tracker AI — theme boot
 *
 * Поддерживает: light | dark | a11y | system
 * Источник истины: data-theme на <html>. Серверный шаблон задаёт стартовое значение.
 * Локальный override (если пользователь щёлкнул по быстрой кнопке в топбаре) хранится в localStorage под ключом
 * "st_theme". Серверные настройки имеют приоритет — они выставляют data-server-theme, и мы
 * применяем local override только если сервер сказал "system" или если override старее session.
 */

(function () {
  const ROOT = document.documentElement;
  const KEY = "st_theme";
  const FONT_KEY = "st_font_scale";
  const SUPPORTED = new Set(["light", "dark", "a11y", "system"]);

  const getSystemTheme = () => {
    try {
      return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    } catch {
      return "light";
    }
  };

  const resolve = (value) => {
    if (!SUPPORTED.has(value)) return "light";
    return value === "system" ? getSystemTheme() : value;
  };

  const apply = (value) => {
    const resolved = resolve(value);
    ROOT.setAttribute("data-theme", resolved);
    ROOT.setAttribute("data-theme-pref", value);
  };

  const initial = () => {
    const serverPref = ROOT.getAttribute("data-server-theme") || "light";
    const localPref = localStorage.getItem(KEY);
    // Локальный выбор темы всегда сохраняется между страницами.
    if (localPref && SUPPORTED.has(localPref)) {
      apply(localPref);
      return;
    }
    if (SUPPORTED.has(serverPref)) {
      apply(serverPref);
      return;
    }
    apply("system");
  };

  const applyFontScale = (scaleRaw) => {
    const scale = Math.max(0.85, Math.min(1.35, Number(scaleRaw || 1)));
    ROOT.style.setProperty("--user-font-scale", String(scale));
  };

  const initialFontScale = () => {
    const stored = localStorage.getItem(FONT_KEY);
    applyFontScale(stored || 1);
  };

  initial();
  initialFontScale();

  // Реакция на смену системной темы (когда текущий режим — system)
  try {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", () => {
      if ((ROOT.getAttribute("data-theme-pref") || "system") === "system") {
        apply("system");
      }
    });
  } catch {}

  // API на window — кнопка в топбаре/настройках может им пользоваться
  window.SmartTrackerTheme = {
    set(value) {
      if (!SUPPORTED.has(value)) return;
      localStorage.setItem(KEY, value);
      apply(value);
    },
    cycle() {
      const current = ROOT.getAttribute("data-theme-pref") || "light";
      const order = ["light", "dark", "a11y", "system"];
      const next = order[(order.indexOf(current) + 1) % order.length];
      this.set(next);
      return next;
    },
    current() {
      return {
        pref: ROOT.getAttribute("data-theme-pref") || "light",
        resolved: ROOT.getAttribute("data-theme") || "light",
      };
    },
    setFontScale(scale) {
      applyFontScale(scale);
      localStorage.setItem(FONT_KEY, String(Math.max(0.85, Math.min(1.35, Number(scale || 1)))));
    },
    getFontScale() {
      return Number(localStorage.getItem(FONT_KEY) || 1);
    },
  };

  // Разметка topbar-кнопки
  document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("theme-quick-toggle");
    if (!btn) return;
    const renderLabel = () => {
      const { pref, resolved } = window.SmartTrackerTheme.current();
      const labels = { light: "Светлая", dark: "Тёмная", a11y: "Контраст", system: "Система" };
      const icons = { light: "☀", dark: "☾", a11y: "◉", system: "✦" };
      btn.textContent = `${icons[pref] || "✦"} ${labels[pref] || "Тема"}`;
      btn.setAttribute("title", `Сейчас: ${labels[resolved] || resolved}`);
    };
    renderLabel();
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      window.SmartTrackerTheme.cycle();
      renderLabel();
    });
  });
})();
