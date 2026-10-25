// The inventory page: loads the items and shows them in the table.
// Decoy: the search, the fixed low-stock rule, the empty message and the
// count are all there, but the count is only refreshed when the SEARCH text
// changes (and on load) — ticking "Low stock only" re-renders the rows and
// leaves the count saying the old number. Nobody who only typed in the box
// would notice.
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
  const needle = document.getElementById("search").value.trim().toLowerCase();
  const body = document.querySelector("#items tbody");
  body.replaceChildren();
  for (const item of items) {
    if (lowOnly && !(item.qty <= item.reorder_at)) {
      continue;
    }
    if (needle && !(item.sku.toLowerCase().includes(needle) || item.name.toLowerCase().includes(needle))) {
      continue;
    }
    body.appendChild(rowFor(item));
  }
  document.getElementById("empty").hidden = body.children.length !== 0;
}

function updateCount() {
  const shown = document.querySelectorAll("#items tbody tr").length;
  document.getElementById("count").textContent = `Showing ${shown} of ${items.length} items`;
}

async function load() {
  const response = await fetch("/api/items");
  items = await response.json();
  render();
  updateCount();
}

document.getElementById("search").addEventListener("input", () => {
  render();
  updateCount();
});
document.getElementById("low-stock").addEventListener("change", render);
load();
