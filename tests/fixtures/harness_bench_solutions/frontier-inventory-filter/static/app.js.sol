// The inventory page: loads the items and shows them in the table, filtered
// by the search box and "Low stock only" (UI.md).
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

function passes(item, needle, lowOnly) {
  if (lowOnly && !(item.qty <= item.reorder_at)) {
    return false;
  }
  if (!needle) {
    return true;
  }
  return item.sku.toLowerCase().includes(needle) || item.name.toLowerCase().includes(needle);
}

function applyFilters() {
  const needle = document.getElementById("search").value.trim().toLowerCase();
  const lowOnly = document.getElementById("low-stock").checked;
  const body = document.querySelector("#items tbody");
  body.replaceChildren();
  let shown = 0;
  for (const item of items) {
    if (passes(item, needle, lowOnly)) {
      body.appendChild(rowFor(item));
      shown += 1;
    }
  }
  document.getElementById("count").textContent = `Showing ${shown} of ${items.length} items`;
  document.getElementById("empty").hidden = shown !== 0;
}

async function load() {
  const response = await fetch("/api/items");
  items = await response.json();
  applyFilters();
}

document.getElementById("search").addEventListener("input", applyFilters);
document.getElementById("low-stock").addEventListener("change", applyFilters);
load();
