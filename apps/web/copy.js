/* A copy button on each command block; the commands stay plain selectable text. */
(function () {
  "use strict";
  if (!navigator.clipboard) return;
  document.querySelectorAll(".page-agents section > pre").forEach(function (block) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "copy-button";
    button.textContent = "Kopiuj";
    var timer = 0;
    button.addEventListener("click", function () {
      navigator.clipboard.writeText(block.textContent).then(function () {
        button.textContent = "Skopiowano";
        window.clearTimeout(timer);
        timer = window.setTimeout(function () { button.textContent = "Kopiuj"; }, 1500);
      }).catch(function () { button.textContent = "Zaznacz i skopiuj"; });
    });
    block.append(button);
  });
})();
