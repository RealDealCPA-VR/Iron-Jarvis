// The inventory page (alternative structure): every row is built ONCE when
// the items load; the filters only hide or show the rows that are already in
// the table (UI.md allows either). The count and the empty message are kept
// in step by one function.
"use strict";

const state = { items: [], rows: [] };

function el(id) {
  return document.getElementById(id);
}

function makeRow(item) {
  const tr = document.createElement("tr");
  tr.dataset.sku = item.sku;
  [item.sku, item.name, item.qty, item.reorder_at].forEach((value) => {
    const td = document.createElement("td");
    td.textContent = String(value);
    tr.appendChild(td);
  });
  tr.classList.toggle("low", item.qty <= item.reorder_at);
  return tr;
}

function matchesText(item, text) {
  if (text === "") return true;
  return [item.sku, item.name].some((field) => field.toLowerCase().indexOf(text) !== -1);
}

function isLow(item) {
  return item.qty <= item.reorder_at;
}

function refresh() {
  const text = el("search").value.trim().toLowerCase();
  const lowOnly = el("low-stock").checked;
  let visible = 0;
  state.items.forEach((item, i) => {
    const show = matchesText(item, text) && (!lowOnly || isLow(item));
    state.rows[i].hidden = !show;
    if (show) visible++;
  });
  el("count").textContent = "Showing " + visible + " of " + state.items.length + " items";
  el("empty").style.display = visible === 0 ? "" : "none";
}

function start(items) {
  state.items = items;
  state.rows = items.map(makeRow);
  const tbody = el("items").tBodies[0];
  state.rows.forEach((tr) => tbody.appendChild(tr));
  refresh();
}

fetch("/api/items")
  .then((r) => r.json())
  .then(start);

el("search").addEventListener("input", refresh);
el("low-stock").addEventListener("change", refresh);
