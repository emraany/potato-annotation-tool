/* VLMD Subtask 1 — the two things this study asks of the page that Potato's own
 * template does not do.
 *
 * Inlined into every page by scripts/serve.py, as `custom_footer_html`. It is
 * not loaded as a file: `static/` is not served, and the footer is emitted
 * BEFORE annotation.js, so everything below waits for DOMContentLoaded — by
 * which point annotation.js has been parsed and its globals exist.
 *
 *   1. The guidelines banner starts shut, and remembers whoever opens it.
 *   2. Next becomes two buttons: Save draft and Submit.
 *   3. The navbar arrows say they step, and stop at the two ends.
 *   4. Un-ticking a checkbox re-runs the display_logic gates it feeds.
 */
(function () {
  "use strict";

  // --- 1. the guidelines banner ---------------------------------------------
  // Potato's template hardcodes `open`, and the banner holds the whole of
  // guidelines.md -- several screens above the argument on every item.
  var BANNER_KEY = "vlmd.guidelines.open";

  function banner() {
    var el = document.querySelector("details.annotation-instructions-banner");
    if (!el) { return; }
    var stored = null;
    try { stored = localStorage.getItem(BANNER_KEY); } catch (err) { /* private window */ }
    el.open = stored === "open";
    if (!el.dataset.vlmdBound) {
      el.dataset.vlmdBound = "1";
      el.addEventListener("toggle", function () {
        try { localStorage.setItem(BANNER_KEY, el.open ? "open" : "shut"); } catch (err) {}
      });
    }
  }

  // --- 2. Save draft / Submit ------------------------------------------------
  //
  // Potato has one button. It either lets you past or it refuses, and which one
  // it does is invisible until you press it. Two buttons say what the choice
  // actually is: leave this argument unfinished and come back to it, or declare
  // it done.
  //
  // The schemes reach Potato as optional -- that is what stops it blocking
  // navigation, see scripts/serve.py -- so `required` is not in the page's HTML
  // and Potato's own validateRequiredFields() has nothing to check. This is
  // where the requirement lives on the client instead. window.VLMD_REQUIRED is
  // written by serve.py from the same config the server reads, so the two
  // cannot disagree.

  function requirements() {
    return (window.VLMD_REQUIRED || []);
  }

  function formFor(name) {
    return document.querySelector('.annotation-form[data-schema-name="' + name + '"]');
  }

  /* A question behind a shut display_logic gate cannot be answered, so it is
     not owed. The DOM is the source of truth here rather than a copy of the
     gate's rules: annotation.js is what opens and shuts them, live, as the
     checks are answered. */
  function visible(node) {
    while (node && node !== document.body) {
      if (node.classList && node.classList.contains("display-logic-hidden")) { return false; }
      var style = window.getComputedStyle(node);
      if (style.display === "none" || style.visibility === "hidden") { return false; }
      node = node.parentElement;
    }
    return true;
  }

  function label(form) {
    var legend = form.querySelector("legend");
    var text = (legend ? legend.textContent : form.dataset.schemaName || "").trim();
    text = text.replace(/\s+/g, " ");

    /* The questions are whole sentences and the message names three of them at
       once, so each one is cut back to the part that identifies it. A colon or
       a question mark ends the naming part when there is one near the front --
       "Secondary — Affective Hijacking:", "Why that one?" -- and otherwise the
       em-dash does, which is what separates "1. Manipulation Check" and
       "Primary mechanism" from the question they introduce. Taking the em-dash
       first would cut "Secondary — Affective Hijacking" down to "Secondary",
       which is three questions' worth of ambiguity. */
    var near = text.search(/[:?]/);
    var cut = (near > 8 && near < 70) ? near : text.search(/[—–]/);
    if (cut > 8) { text = text.slice(0, cut).trim(); }
    return text.length > 64 ? text.slice(0, 61) + "…" : text;
  }

  /* Every required question on screen that has not been answered. A text answer
     counts only once it is past its own min_chars: a three-word justification is
     not a finished one, and catching it here is catching it while the annotator
     can still do something about it. */
  function unanswered() {
    var gaps = [];
    requirements().forEach(function (req) {
      var form = formFor(req.name);
      if (!form || !visible(form)) { return; }

      var texts = form.querySelectorAll('textarea, input[type="text"], input[type="number"]');
      var boxes = form.querySelectorAll('input[type="radio"], input[type="checkbox"]');
      var answered;
      if (texts.length) {
        answered = Array.prototype.some.call(texts, function (box) {
          return box.value.trim().length >= Math.max(req.min_chars || 0, 1);
        });
      } else {
        answered = Array.prototype.some.call(boxes, function (box) { return box.checked; });
      }
      if (!answered) { gaps.push({ form: form, label: label(form) }); }
    });
    return gaps;
  }

  function clearMarks() {
    document.querySelectorAll(".vlmd-unfinished").forEach(function (form) {
      form.classList.remove("vlmd-unfinished");
    });
  }

  function mark(gaps) {
    clearMarks();
    gaps.forEach(function (gap) { gap.form.classList.add("vlmd-unfinished"); });
    var names = gaps.map(function (gap) { return gap.label; }).join("; ");
    if (typeof window.showNotification === "function") {
      window.showNotification(
        "Not finished: " + names + ". "
        + (gaps.length === 1 ? "Answer it" : "Answer them")
        + ", or Save draft and come back to this argument.",
        "error");
    }
    gaps[0].form.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function buttons() {
    var submit = document.getElementById("next-btn");
    if (!submit || submit.dataset.vlmdSplit) { return; }
    submit.dataset.vlmdSplit = "1";

    // Submit IS Potato's Next button, relabelled: it carries the save, the
    // navigation, the loading state and the disabled-while-saving handling, and
    // reimplementing those would be reimplementing the page.
    submit.innerHTML = 'Submit <i class="fas fa-check ms-2"></i>';

    var draft = document.createElement("button");
    draft.type = "button";
    draft.id = "draft-btn";
    draft.className = "shadcn-button shadcn-button-outline";
    draft.innerHTML = '<i class="fas fa-clock me-2"></i>Save draft';
    draft.title = "Leave this argument unfinished and go to the next one. "
                + "What you have answered is kept.";
    submit.parentNode.insertBefore(draft, submit);

    draft.addEventListener("click", function () {
      clearMarks();
      if (typeof window.vlmdAdvance === "function") { window.vlmdAdvance(); }
    });
  }

  /* Submit validates; Save draft does not. Both end in the same place.
     `navigateToNext` is overridden rather than the button's onclick replaced,
     because the right-arrow key calls the same global -- so the key and the
     button agree about what Submit means without a second implementation. */
  function wire() {
    if (window.vlmdAdvance || typeof window.navigateToNext !== "function") { return; }
    var advance = window.navigateToNext;
    window.vlmdAdvance = advance;
    window.navigateToNext = function () {
      var gaps = unanswered();
      if (gaps.length) { mark(gaps); return; }
      clearMarks();
      return advance.apply(this, arguments);
    };
  }

  // --- 4. un-ticking a checkbox closes the gate it opened --------------------
  //
  // annotation.js's handleInputChange() ends the UNCHECKED branch with its own
  // `return`, several lines above the `displayLogicManager.evaluateForSchema()`
  // call every other branch reaches. So ticking a secondary mechanism opened
  // its justification box and un-ticking it left the box on screen: the answer
  // was gone from currentAnnotations, but nothing asked the gate about it
  // again. Submit then refused an argument over a question that should not
  // have been there, and the only ways out were to re-tick the mechanism and
  // write a justification nobody wanted, or to Save draft.
  //
  // `change` listeners bound on the input itself run before one bound here on
  // the document, so by this point currentAnnotations has already had the
  // label deleted and the gate is asked about the state it should see. Asking
  // again when nothing changed is free -- updateVisibility() returns early
  // unless the answer differs from what is on screen.

  function regate(event) {
    var input = event.target;
    if (!input || input.type !== "checkbox" || input.checked) { return; }
    var schema = input.getAttribute("schema");
    if (!schema || typeof getDisplayLogicManager !== "function") { return; }
    var manager = getDisplayLogicManager();
    if (manager) { manager.evaluateForSchema(schema); }
  }

  // --- 3. the navbar arrows --------------------------------------------------
  //
  // scripts/serve.py has already changed where they go: they are the previous
  // and the next argument now, not the previous and next UNFINISHED one, so a
  // finished argument is one press away instead of only reachable by typing its
  // number into Go to. Two things are left over on the page.
  //
  // The template calls them "Previous unannotated" and "Next unannotated" in
  // their tooltips and draws them as the double-chevron skip arrows, both of
  // which now describe a button that does something else.
  //
  // And there is no wrap-around any more, so at argument 1 and at the last one
  // the server has nowhere to send them -- it answers with the status Potato
  // reads as "All items have been annotated!", which is a sentence about
  // something else entirely. The button is disabled before it can be pressed.

  var ARROWS = [
    { id: "jump-unannotated-prev-btn", icon: "fa-arrow-left",
      title: "Previous argument" },
    { id: "jump-unannotated-btn", icon: "fa-arrow-right",
      title: "Next argument" }
  ];

  /* Which argument is on screen, 0-based, and how many there are. Read off the
     navbar rather than from a global: `#instance-number` is the number the
     annotator is looking at ("#500"), and the Go to box's own `max` is the
     corpus size, so the arrows and the box cannot disagree about either. */
  function position() {
    var shown = document.getElementById("instance-number");
    var box = document.getElementById("go_to");
    var index = shown ? parseInt(String(shown.textContent).replace(/\D/g, ""), 10) : NaN;
    var total = box ? parseInt(box.max, 10) : NaN;
    if (!isFinite(index) || index < 1) { return null; }
    return { index: index - 1, total: isFinite(total) ? total : 0 };
  }

  function arrows() {
    var at = position();
    ARROWS.forEach(function (spec, i) {
      var button = document.getElementById(spec.id);
      if (!button) { return; }
      button.title = spec.title;
      button.setAttribute("aria-label", spec.title);
      var glyph = button.querySelector("i");
      if (glyph) { glyph.className = "fas " + spec.icon; }
      if (!at) { return; }
      button.disabled = i === 0
        ? at.index <= 0
        : (at.total > 0 && at.index >= at.total - 1);
    });
  }

  // A marked question stops being marked the moment it is answered; leaving the
  // outline up while somebody types reads as "still wrong".
  function watch() {
    document.addEventListener("input", recheck, true);
    document.addEventListener("change", recheck, true);
    // Bubble phase, not capture: regate has to run AFTER annotation.js has
    // deleted the label, and a marked question has to stop being marked in the
    // same gesture that closed its gate -- hence recheck once more behind it.
    document.addEventListener("change", function (event) {
      regate(event);
      recheck();
    });
  }

  function recheck() {
    if (!document.querySelector(".vlmd-unfinished")) { return; }
    var still = unanswered().map(function (gap) { return gap.form; });
    document.querySelectorAll(".vlmd-unfinished").forEach(function (form) {
      if (still.indexOf(form) === -1) { form.classList.remove("vlmd-unfinished"); }
    });
  }

  function start() {
    banner();
    wire();
    buttons();
    arrows();
    watch();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
