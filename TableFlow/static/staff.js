"use strict";

const waiterCount = document.querySelector("[data-waiter-count]");
if (waiterCount) {
  async function refreshWaiterCount() {
    try {
      const response = await fetch("/waiter-count");
      if (response.ok) {
        const count = Number(await response.text());
        waiterCount.textContent = String(count);
        waiterCount.hidden = !count;
      }
    } catch {
      // Keep the last displayed count during temporary network interruptions.
    }
    window.setTimeout(refreshWaiterCount, 5000);
  }
  refreshWaiterCount();
}

const kitchenTimers = [...document.querySelectorAll("[data-kitchen-elapsed]")];
if (kitchenTimers.length) {
  const loadedAt = performance.now();
  window.setInterval(() => {
    const additionalSeconds = Math.floor((performance.now() - loadedAt) / 1000);
    for (const timer of kitchenTimers) {
      const elapsed = Number(timer.dataset.kitchenElapsed) + additionalSeconds;
      timer.textContent = `${Math.floor(elapsed / 60)}m ${String(elapsed % 60).padStart(2, "0")}s`;
    }
  }, 1000);
}
