// Crewed — small client-side helpers (no framework)

// Auto-dismiss flash messages
document.querySelectorAll(".flash").forEach(function (el) {
  setTimeout(function () {
    el.style.transition = "opacity .4s";
    el.style.opacity = "0";
    setTimeout(function () { el.remove(); }, 450);
  }, 4500);
});

// Live bill-rate preview: any input[data-bill-source] updates [data-bill-target]
// using the markup % in data-markup.
function wireBillPreview(source) {
  var targetSel = source.getAttribute("data-bill-target");
  var markup = parseFloat(source.getAttribute("data-markup") || "0");
  var target = document.querySelector(targetSel);
  if (!target) return;
  function update() {
    var pay = parseFloat(source.value);
    if (isNaN(pay) || pay <= 0) { target.textContent = "—"; return; }
    var bill = pay * (1 + markup / 100);
    target.textContent = "$" + bill.toFixed(2) + "/hr";
  }
  source.addEventListener("input", update);
  update();
}
document.querySelectorAll("input[data-bill-source]").forEach(wireBillPreview);

// Shift form: location selection updates the minimum-wage hint + input floor;
// position selection pre-fills the pay rate from the client's default.
(function () {
  var locSelect = document.querySelector("#shift-location");
  var posSelect = document.querySelector("#shift-position");
  var payInput = document.querySelector("#shift-pay");
  var wageHint = document.querySelector("#wage-hint");
  if (!payInput) return;

  function currentWage() {
    if (!locSelect) return 0;
    var opt = locSelect.options[locSelect.selectedIndex];
    return opt ? parseFloat(opt.getAttribute("data-minwage") || "0") : 0;
  }
  function refreshWage() {
    var wage = currentWage();
    if (wageHint && wage > 0) {
      wageHint.innerHTML =
        "Minimum wage at this location: <strong>$" + wage.toFixed(2) + "/hr</strong>";
    }
    if (wage > 0) payInput.min = wage.toFixed(2);
  }
  function fillRate() {
    if (!posSelect) return;
    var opt = posSelect.options[posSelect.selectedIndex];
    var rate = opt ? opt.getAttribute("data-rate") : null;
    if (rate) {
      payInput.value = parseFloat(rate).toFixed(2);
      payInput.dispatchEvent(new Event("input"));
    }
  }
  if (locSelect) locSelect.addEventListener("change", refreshWage);
  if (posSelect) posSelect.addEventListener("change", fillRate);
  refreshWage();
})();
