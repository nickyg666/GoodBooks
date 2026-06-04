(function () {
  try {
    var t = localStorage.getItem("goodbooks-theme") || "light";
    if (["light", "dark", "sepia", "high-contrast"].indexOf(t) === -1) t = "light";
    document.documentElement.setAttribute("data-theme", t);
  } catch (e) {
    document.documentElement.setAttribute("data-theme", "light");
  }
})();
