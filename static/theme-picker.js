(function () {
  var THEMES = [
    { id: "light", label: "Light" },
    { id: "dark", label: "Dark" },
    { id: "sepia", label: "Sepia" },
    { id: "high-contrast", label: "High contrast" },
  ];

  function apply(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("goodbooks-theme", theme); } catch (e) {}
    document.querySelectorAll("[data-theme-picker]").forEach(function (el) { el.value = theme; });
  }

  function mount(el) {
    var select = document.createElement("select");
    select.setAttribute("data-theme-picker", "");
    select.setAttribute("aria-label", "Color theme");
    select.style.marginLeft = "0.5rem";
    THEMES.forEach(function (t) {
      var opt = document.createElement("option");
      opt.value = t.id;
      opt.textContent = t.label;
      select.appendChild(opt);
    });
    try { select.value = localStorage.getItem("goodbooks-theme") || "light"; } catch (e) { select.value = "light"; }
    select.addEventListener("change", function () { apply(select.value); });
    el.appendChild(select);
  }

  window.GoodBooksTheme = { apply: apply, mount: mount, themes: THEMES };

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-theme-mount]").forEach(mount);
  });
})();
