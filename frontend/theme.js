/* Light / dark switch. Loaded in <head> (before the page is drawn) so the saved theme is applied with no flash.
   Light is the default. The choice is saved in this browser (localStorage key "sg-theme"). */
(function () {
  'use strict';
  var KEY = 'sg-theme';
  var root = document.documentElement;

  function saved() {
    try { var v = localStorage.getItem(KEY); return v === 'dark' ? 'dark' : 'light'; } catch (e) { return 'light'; } // storage can be blocked
  }
  function remember(theme) {
    try { localStorage.setItem(KEY, theme); } catch (e) { /* the theme still changes for this visit */ }
  }
  function paintButtons(theme) {
    var dark = theme === 'dark';
    var buttons = document.querySelectorAll('[data-theme-toggle]');
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute('aria-pressed', dark ? 'true' : 'false');
      buttons[i].setAttribute('aria-label', dark ? 'Switch to light mode' : 'Switch to dark mode');
      buttons[i].setAttribute('title', dark ? 'Light mode' : 'Dark mode');
    }
  }
  function apply(theme) {
    root.setAttribute('data-theme', theme);
    var meta = document.querySelector('meta[name="color-scheme"]');
    if (meta) meta.setAttribute('content', theme);
    paintButtons(theme);
  }

  apply(saved());
  document.addEventListener('DOMContentLoaded', function () {
    paintButtons(root.getAttribute('data-theme') || 'light');
    document.addEventListener('click', function (e) {
      var btn = e.target.closest ? e.target.closest('[data-theme-toggle]') : null;
      if (!btn) return;
      var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      apply(next);
      remember(next);
    });
  });
})();
