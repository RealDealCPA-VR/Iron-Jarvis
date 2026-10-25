<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Inventory</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <h1>Inventory</h1>
  <div class="controls">
    <input type="text" id="search" aria-label="Search by SKU or name" placeholder="Search SKU or name">
    <label><input type="checkbox" id="low-stock"> Low stock only</label>
  </div>
  <p id="count">Showing 0 of 0 items</p>
  <table id="items">
    <thead>
      <tr><th>SKU</th><th>Name</th><th>Qty</th><th>Reorder at</th></tr>
    </thead>
    <tbody></tbody>
  </table>
  <p id="empty" style="visibility: hidden">No items match</p>
  <script src="app.js"></script>
</body>
</html>
