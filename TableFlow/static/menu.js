"use strict";

const menu = document.querySelector("[data-customer-menu]");
if (menu) {
  const tools = menu.querySelector("[data-menu-tools]");
  const search = menu.querySelector("[data-menu-search]");
  const filters = [...menu.querySelectorAll("[data-menu-filter]")];
  const items = [...menu.querySelectorAll(".menu-item")];
  const preview = menu.querySelector("[data-cart-preview]");
  const cartItems = menu.querySelector("[data-cart-items]");
  const cartTotal = menu.querySelector("[data-cart-total]");
  const cartEmpty = menu.querySelector("[data-cart-empty]");
  const sendOrder = menu.querySelector("[data-send-order]");
  const mobileCart = menu.querySelector("[data-mobile-cart]");
  const mobileCount = menu.querySelector("[data-mobile-cart-count]");
  const mobileTotal = menu.querySelector("[data-mobile-cart-total]");
  const cartPanel = menu.querySelector("#menu-cart");
  let cartVisible = false;
  const currency = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" });
  let category = menu.dataset.selected;

  function filterMenu() {
    const value = search.value.trim().toLocaleLowerCase();
    let visible = 0;
    for (const item of items) {
      const matches = (category === "All" || category === item.dataset.category) &&
        item.textContent.toLocaleLowerCase().includes(value);
      item.hidden = !matches;
      visible += Number(matches);
    }
    menu.querySelector("[data-menu-empty]").hidden = visible > 0;
    for (const filter of filters) {
      filter.setAttribute("aria-pressed", String(filter.dataset.menuFilter === category));
    }
  }

  function updateCart() {
    let total = 0;
    let count = 0;
    cartItems.replaceChildren();
    for (const item of items) {
      const input = item.querySelector(".menu-quantity input");
      const quantity = input.valueAsNumber;
      const valid = input.validity.valid && Number.isInteger(quantity);
      item.querySelector('[data-step="-1"]').disabled = !valid || quantity === 0;
      item.querySelector('[data-step="1"]').disabled = valid && quantity === 20;
      if (!valid || quantity < 1) continue;

      const lineTotal = Number(item.dataset.price) * quantity;
      const line = document.createElement("li");
      const title = document.createElement("span");
      const price = document.createElement("strong");
      title.textContent = `${quantity} × ${item.dataset.name}`;
      price.textContent = currency.format(lineTotal / 100);
      line.append(title, price);
      cartItems.append(line);
      count += quantity;
      total += lineTotal;
    }
    cartEmpty.hidden = count > 0;
    sendOrder.disabled = count === 0;
    const formattedTotal = currency.format(total / 100);
    cartTotal.textContent = formattedTotal;
    mobileTotal.textContent = formattedTotal;
    mobileCount.textContent = `${count} ${count === 1 ? "item" : "items"}`;
    mobileCart.hidden = !count || cartVisible;
  }

  tools.hidden = false;
  search.addEventListener("input", filterMenu);
  for (const filter of filters) {
    filter.addEventListener("click", () => {
      category = filter.dataset.menuFilter;
      filterMenu();
    });
  }
  if (preview) {
    preview.hidden = false;
    for (const item of items) {
      const input = item.querySelector(".menu-quantity input");
      input.addEventListener("input", updateCart);
      for (const step of item.querySelectorAll("[data-step]")) {
        step.hidden = false;
        step.addEventListener("click", () => {
          const current = Number.isFinite(input.valueAsNumber) ? Math.trunc(input.valueAsNumber) : 0;
          input.value = Math.max(0, Math.min(20, current + Number(step.dataset.step)));
          updateCart();
        });
      }
    }
    menu.querySelector("form").addEventListener("invalid", (event) => {
      const item = event.target.closest(".menu-item");
      if (item?.hidden) {
        category = "All";
        search.value = "";
        filterMenu();
      }
    }, true);
    if ("IntersectionObserver" in window) {
      const observer = new IntersectionObserver(([entry]) => {
        cartVisible = entry.isIntersecting && entry.intersectionRect.height >= 72;
        updateCart();
      }, { threshold: [0, 0.35] });
      observer.observe(cartPanel);
    }
    updateCart();
  }
  filterMenu();
}
