async function loadStatus() {
  const response = await fetch('/api/status');
  const result = await response.json();
  const cards = document.getElementById('status-cards');
  cards.innerHTML = '';

  result.data.forEach((item) => {
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
      <div class="label">${item.endpoint}</div>
      <div class="value">${item.fetched_at}</div>
      <div class="meta">status: ${item.status_code}</div>
    `;
    cards.appendChild(card);
  });
}

async function loadChanges() {
  const response = await fetch('/api/changes?limit=20');
  const result = await response.json();
  const tbody = document.getElementById('changes-body');
  tbody.innerHTML = '';

  result.data.forEach((item) => {
    const row = document.createElement('tr');
    row.innerHTML = `
      <td>${item.endpoint}</td>
      <td>${item.change_type}</td>
      <td>${item.path}</td>
      <td>${safeValue(item.old_value)}</td>
      <td>${safeValue(item.new_value)}</td>
      <td>${item.created_at}</td>
    `;
    tbody.appendChild(row);
  });
}

function safeValue(value) {
  if (value == null) return '-';
  try {
    return JSON.stringify(JSON.parse(value), null, 2).slice(0, 80);
  } catch {
    return String(value).slice(0, 80);
  }
}

loadStatus();
loadChanges();
setInterval(() => {
  loadStatus();
  loadChanges();
}, 30000);
