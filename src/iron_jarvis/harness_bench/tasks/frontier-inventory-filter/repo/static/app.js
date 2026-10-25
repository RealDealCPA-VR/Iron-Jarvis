// The inventory page: loads the items and shows them in the table.
"use strict";

let items = [];

function rowFor(item) {
  const tr = document.createElement("tr");
  tr.dataset.sku = item.sku;
  for (const value of [item.sku, item.name, item.qty, item.reorder_at]) {
    const td = document.createElement("td");
    td.textContent = String(value);
    tr.appendChild(td);
  }
  if (item.qty <= item.reorder_at) {
    tr.classList.add("low");
  }
  return tr;
}

function render() {
  const lowOnly = document.getElementById("low-stock").checked;
  const body = document.querySelector("#items tbody");
  body.replaceChildren();
  for (const item of items) {
    if (lowOnly && !(item.qty < 10)) {
      continue;
    }
    body.appendChild(rowFor(item));
  }
}

async function load() {
  const response = await fetch("/api/items");
  items = await response.json();
  render();
}

document.getElementById("low-stock").addEventListener("change", render);
load();
