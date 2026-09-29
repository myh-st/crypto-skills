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
      // Inline SVG (same family as modules/components/icons.js); the text label stays visible.
      var sun = '<svg class="icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
      var moon = '<svg class="icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/></svg>';
      button.innerHTML = (dark ? sun : moon) + "<span>" + (dark ? "Light" : "Dark") + "</span>";
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
