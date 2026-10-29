async function loadStatus() {
  const response = await fetch('/api/status');
  const result = await response.json();
  const cards = document.getElementById('status-cards');
  const chart = document.getElementById('chart');
  cards.innerHTML = '';
  chart.innerHTML = '';

  if (!result.data || result.data.length === 0) {
    cards.innerHTML = '<div class="card"><div class="label">No data yet</div><div class="value">Waiting for first fetch</div></div>';
    return;
  }

  const maxCount = Math.max(...result.data.map(item => Number(item.change_count || 0))) || 1;

  result.data.forEach((item) => {
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
      <div class="label">${item.endpoint}</div>
      <div class="value">${item.fetched_at}</div>
      <div class="meta">status: ${item.status_code} / changes: ${item.change_count}</div>
    `;
    cards.appendChild(card);

    const row = document.createElement('div');
    row.className = 'chart-row';
    row.innerHTML = `
      <div class="chart-name">${item.endpoint}</div>
      <div class="chart-bar-bg"><div class="chart-bar" style="width:${((Number(item.change_count) || 0) / maxCount) * 100}%"></div></div>
      <div class="chart-count">${item.change_count}</div>
    `;
    chart.appendChild(row);
  });
}

async function loadHistory() {
  const response = await fetch('/api/history?limit=8');
  const result = await response.json();
  const tbody = document.getElementById('history-body');
  tbody.innerHTML = '';

  if (!result.data || result.data.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4">No snapshots yet</td></tr>';
    return;
  }

  result.data.forEach((item) => {
    const row = document.createElement('tr');
    row.innerHTML = `
      <td><a href="/api/snapshot/${item.id}" target="_blank">${item.id}</a></td>
      <td>${item.endpoint}</td>
      <td>${item.fetched_at}</td>
      <td>${item.status_code}</td>
    `;
    tbody.appendChild(row);
  });
}

async function loadChanges() {
  const response = await fetch('/api/changes?limit=20');
  const result = await response.json();
  const tbody = document.getElementById('changes-body');
  tbody.innerHTML = '';

  if (!result.data || result.data.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6">No changes yet</td></tr>';
    return;
  }

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
loadHistory();
loadChanges();
setInterval(() => {
  loadStatus();
  loadHistory();
  loadChanges();
}, 30000);
