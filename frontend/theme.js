// Theme bootstrap: runs synchronously in <head> so the first paint already uses the chosen theme.
// Dark is the default; the topbar toggle stores "light" or "dark" in this browser only.
(function () {
  var KEY = "cot-theme";
  function stored() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function apply(theme) {
    document.documentElement.setAttribute("data-theme", theme === "light" ? "light" : "dark");
  }
  apply(stored() || "dark");
  document.addEventListener("DOMContentLoaded", function () {
    var bar = document.getElementById("runtime-banner");
    if (!bar) return;
    var button = document.createElement("button");
    button.type = "button";
    button.className = "theme-toggle";
    function label() {
      var dark = document.documentElement.getAttribute("data-theme") === "dark";
      button.textContent = dark ? "☀ Light" : "☾ Dark";
      button.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
    }
    label();
    button.addEventListener("click", function () {
      var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      try { localStorage.setItem(KEY, next); } catch (e) { /* private mode: this page only */ }
      apply(next);
      label();
      // Canvas charts read the theme colours when they mount; re-mount the current view.
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    bar.appendChild(button);
  });
})();
