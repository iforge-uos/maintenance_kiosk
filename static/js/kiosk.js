(function () {
  function openDialog(id) {
    var dialog = document.getElementById("actions-" + id);
    if (dialog && typeof dialog.showModal === "function") {
      dialog.showModal();
    }
  }

  document.addEventListener("click", function (event) {
    var trigger = event.target.closest("[data-action-open]");
    if (trigger) {
      openDialog(trigger.getAttribute("data-action-open"));
    }

    var selectAll = event.target.closest("[data-select-all]");
    if (selectAll) {
      document.querySelectorAll('.check-grid input[type="checkbox"]').forEach(function (checkbox) {
        checkbox.checked = true;
      });
    }
  });

  document.querySelectorAll("[data-technician-select]").forEach(function (select) {
    var otherField = select.closest("section, form").querySelector(".other-technician");
    function toggleOther() {
      if (!otherField) {
        return;
      }
      otherField.hidden = select.value !== "Other";
    }
    select.addEventListener("change", toggleOther);
    toggleOther();
  });

  function updateDashboard() {
    var dashboard = document.querySelector("[data-dashboard]");
    if (!dashboard) {
      return;
    }

    fetch("/api/dashboard" + window.location.search, { cache: "no-store" })
      .then(function (response) {
        return response.json();
      })
      .then(function (payload) {
        var clock = document.querySelector("[data-clock]");
        var sync = document.querySelector("[data-sync-status]");
        var count = document.querySelector("[data-alert-count]");

        if (clock) clock.textContent = payload.generated_at;
        if (sync) sync.textContent = payload.sync_status;
        if (count) count.textContent = payload.unresolved_count;

        payload.printers.forEach(function (printer) {
          var card = document.querySelector('[data-printer-card="' + printer.id + '"]');
          if (!card) {
            return;
          }

          card.className = "printer-card status-" + printer.status.toLowerCase().replace(/\s+/g, "-");
          card.querySelector("[data-fault-text]").textContent = printer.active_problem;
          card.querySelector("[data-status]").textContent = printer.availability;
          card.querySelector("[data-action-needed]").textContent = printer.action_needed;
          card.querySelector("[data-last-weekly]").textContent = printer.last_weekly_at || "Never";
          card.querySelector("[data-last-reactive]").textContent = printer.last_reactive_at || "Never";
          card.querySelector("[data-recent-fault]").textContent = printer.recent_fault;
          card.querySelector("[data-nozzle-life-label]").textContent = printer.nozzle_life_label;

          var nozzleLife = card.querySelector(".nozzle-life");
          if (nozzleLife) {
            nozzleLife.style.setProperty("--nozzle-life-percent", printer.nozzle_life_percent + "%");
          }
        });
      })
      .catch(function () {
        var sync = document.querySelector("[data-sync-status]");
        if (sync) {
          sync.textContent = "Error";
        }
      });
  }

  if (document.querySelector("[data-dashboard]")) {
    setInterval(updateDashboard, 3000);
  }
})();
