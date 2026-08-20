// Theme toggle shared by the chat, dashboard and admin pages. The initial theme
// is applied pre-paint by a tiny inline <head> script (saved preference, else OS
// setting); this file wires every .theme-toggle button on the page (there can be
// more than one — e.g. the dashboard and the chat topbar both have one) and keeps
// following the OS until the user makes an explicit choice.
(function () {
  var KEY = "rag-chat-theme";
  var root = document.documentElement;

  function current() {
    return root.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }

  function apply(theme, persist) {
    root.setAttribute("data-theme", theme);
    if (persist) localStorage.setItem(KEY, theme);
  }

  var btns = document.querySelectorAll(".theme-toggle");
  btns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      apply(current() === "dark" ? "light" : "dark", true);
    });
  });

  // Follow the OS theme only while the user hasn't picked one explicitly.
  var mq = window.matchMedia("(prefers-color-scheme: dark)");
  var onChange = function (e) {
    if (!localStorage.getItem(KEY)) apply(e.matches ? "dark" : "light", false);
  };
  if (mq.addEventListener) mq.addEventListener("change", onChange);
  else if (mq.addListener) mq.addListener(onChange);
})();
