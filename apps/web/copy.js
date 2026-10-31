/* Copy buttons on the command blocks, which stay plain selectable text, and tool links. */
(function () {
  "use strict";
  // A link to a tool lands on its folded row; open it rather than ask for a second click.
  function openTarget() {
    var target = document.getElementById(location.hash.slice(1));
    if (target instanceof HTMLDetailsElement) target.open = true;
  }
  window.addEventListener("hashchange", openTarget);
  openTarget();
  if (!navigator.clipboard) return;
  document.querySelectorAll(".page-agents section > pre").forEach(function (block) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "copy-button";
    button.textContent = "Kopiuj";
    var text = block.textContent;
    var timer = 0;
    button.addEventListener("click", function () {
      navigator.clipboard.writeText(text).then(function () {
        button.textContent = "Skopiowano";
        window.clearTimeout(timer);
        timer = window.setTimeout(function () { button.textContent = "Kopiuj"; }, 1500);
      }).catch(function () { button.textContent = "Zaznacz"; });
    });
    block.append(button);
  });
})();
