// Small progressive enhancements. No inline scripts (strict CSP).
(function () {
  "use strict";

  // CSRF header for HTMX requests
  document.addEventListener("htmx:configRequest", function (e) {
    var m = document.querySelector('meta[name="csrf-token"]');
    if (m) e.detail.headers["X-CSRFToken"] = m.content;
  });

  // Auto-submit selects marked data-autosubmit
  document.addEventListener("change", function (e) {
    var el = e.target;
    if (el.matches && el.matches("[data-autosubmit]")) el.form.submit();
  });

  // Confirm dangerous actions: <form data-confirm="Cancel this booking?">
  document.addEventListener("submit", function (e) {
    var msg = e.target.getAttribute && e.target.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  // Hold countdown: <span class="timer" data-expires="ISO">
  function tick() {
    document.querySelectorAll("[data-expires]").forEach(function (el) {
      var left = Math.max(0, Math.floor((new Date(el.dataset.expires) - new Date()) / 1000));
      el.textContent = Math.floor(left / 60) + ":" + String(left % 60).padStart(2, "0");
      if (left === 0 && !el.dataset.done) {
        el.dataset.done = "1";
        var note = document.querySelector("[data-expired-note]");
        if (note) note.hidden = false;
      }
    });
  }
  setInterval(tick, 1000); tick();

  // Copy to clipboard: <button data-copy="text">
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-copy]");
    if (!b) return;
    navigator.clipboard.writeText(b.dataset.copy).then(function () {
      var t = b.textContent; b.textContent = "Copied"; setTimeout(function () { b.textContent = t; }, 1500);
    });
  });

  // Hours editor: add / remove time ranges
  document.addEventListener("click", function (e) {
    var add = e.target.closest && e.target.closest("[data-add-range]");
    if (add) {
      var tpl = document.getElementById("range-template");
      var day = add.dataset.addRange;
      var node = tpl.content.firstElementChild.cloneNode(true);
      node.querySelectorAll("input").forEach(function (i) { i.name = i.name.replace("DAY", day); });
      document.getElementById("ranges-" + day).appendChild(node);
    }
    var rm = e.target.closest && e.target.closest("[data-remove-range]");
    if (rm) rm.closest(".range").remove();
  });

  // Scroll calendar to the current hour
  var cal = document.querySelector("[data-scroll-to]");
  if (cal) { var y = parseInt(cal.dataset.scrollTo, 10); if (y > 0) window.scrollTo({ top: y, behavior: "instant" }); }
})();
