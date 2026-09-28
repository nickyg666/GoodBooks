(function () {
  try {
    var t = localStorage.getItem("goodbooks-theme") || "light";
    var valid = ["light", "dark", "sepia", "high-contrast", "matrix", "cyberpunk", "synthwave", "typewriter", "solarized-dark"];
    if (valid.indexOf(t) === -1) t = "light";
    document.documentElement.setAttribute("data-theme", t);
  } catch (e) {
    document.documentElement.setAttribute("data-theme", "light");
  }
})();