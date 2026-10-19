/* Deskhand workshop — shared slide runtime.
   Slides are <section class="slide"> children of <main class="deck">.
   Keys: arrows / space / PageUp-PageDown navigate, Home/End jump,
         's' toggles speaker notes, 'f' toggles fullscreen.
   Click: right third = next, left third = previous.
   URL hash deep-links: #5 opens slide 5.
   Dependency-free. */
(function () {
  "use strict";

  var slides = [];
  var current = 0;

  function clamp(n) {
    return Math.max(0, Math.min(slides.length - 1, n));
  }

  function show(n) {
    current = clamp(n);
    for (var i = 0; i < slides.length; i++) {
      slides[i].classList.toggle("current", i === current);
    }
    var counter = document.getElementById("deck-counter");
    if (counter) counter.textContent = (current + 1) + " / " + slides.length;
    var bar = document.getElementById("deck-progress");
    if (bar) bar.style.width = (((current + 1) / slides.length) * 100) + "%";
    if (window.location.hash !== "#" + (current + 1)) {
      history.replaceState(null, "", "#" + (current + 1));
    }
  }

  function next() { show(current + 1); }
  function prev() { show(current - 1); }

  function fromHash() {
    var n = parseInt(window.location.hash.slice(1), 10);
    show(isNaN(n) ? 0 : n - 1);
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else if (document.documentElement.requestFullscreen) {
      document.documentElement.requestFullscreen();
    }
  }

  function onKey(e) {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    switch (e.key) {
      case "ArrowRight":
      case "ArrowDown":
      case "PageDown":
      case " ":
        e.preventDefault(); next(); break;
      case "ArrowLeft":
      case "ArrowUp":
      case "PageUp":
        e.preventDefault(); prev(); break;
      case "Home":
        e.preventDefault(); show(0); break;
      case "End":
        e.preventDefault(); show(slides.length - 1); break;
      case "s":
      case "S":
        document.body.classList.toggle("show-notes"); break;
      case "f":
      case "F":
        toggleFullscreen(); break;
    }
  }

  function onClick(e) {
    if (e.target.closest("a, button, input, textarea, select")) return;
    var x = e.clientX / window.innerWidth;
    if (x > 2 / 3) next();
    else if (x < 1 / 3) prev();
  }

  function init() {
    var deck = document.querySelector("main.deck");
    if (!deck) return;
    slides = Array.prototype.slice.call(deck.querySelectorAll("section.slide"));
    if (!slides.length) return;

    var bar = document.createElement("div");
    bar.id = "deck-progress";
    document.body.appendChild(bar);

    var counter = document.createElement("div");
    counter.id = "deck-counter";
    document.body.appendChild(counter);

    document.addEventListener("keydown", onKey);
    document.addEventListener("click", onClick);
    window.addEventListener("hashchange", fromHash);

    fromHash();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
