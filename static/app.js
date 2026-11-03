// 数据里保留原始英文标识，仅在展示层翻译成中文。
const ENDPOINT_LABELS = {
  collections: '收藏',
  skills: '技能',
  items: '物品',
  bazaar: '集市',
  packs: '资源包',
};

const TYPE_LABELS = {
  added: '新增',
  removed: '删除',
  modified: '修改',
  value_changed: '修改',
  list_length_changed: '列表长度变化',
  version: '版本更新',
};

function endpointLabel(value) {
  return ENDPOINT_LABELS[value] || value;
}

function typeLabel(value) {
  return TYPE_LABELS[value] || value;
}

function escapeHtml(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// 后端写入已统一为北京时间；这里对历史 UTC 数据做兜底转换。
function formatTime(value) {
  if (!value) return '-';
  const text = String(value).trim();
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(text)) return text;
  const iso = text.endsWith('Z') ? text.slice(0, -1) + '+00:00' : text;
  const dt = new Date(/[+-]\d{2}:\d{2}$|Z$/.test(iso) ? iso : iso + 'Z');
  if (Number.isNaN(dt.getTime())) return text;
  const shifted = new Date(dt.getTime() + 8 * 60 * 60 * 1000);
  const p = (n) => String(n).padStart(2, '0');
  return `${shifted.getUTCFullYear()}-${p(shifted.getUTCMonth() + 1)}-${p(shifted.getUTCDate())} ` +
    `${p(shifted.getUTCHours())}:${p(shifted.getUTCMinutes())}:${p(shifted.getUTCSeconds())}`;
}

function safeValue(value) {
  if (value == null) return '—';
  try {
    return JSON.stringify(JSON.parse(value), null, 2).slice(0, 120);
  } catch {
    return String(value).slice(0, 120);
  }
}

async function loadStatus() {
  const response = await fetch('/api/status');
  const result = await response.json();
  const cards = document.getElementById('status-cards');
  const chart = document.getElementById('chart');
  cards.innerHTML = '';
  chart.innerHTML = '';

  if (!result.data || result.data.length === 0) {
    cards.innerHTML = '<div class="card"><div class="label">暂无数据</div><div class="value">等待首次抓取</div></div>';
    return;
  }

  const maxCount = Math.max(...result.data.map(item => Number(item.change_count || 0))) || 1;

  result.data.forEach((item) => {
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
      <div class="label">${escapeHtml(endpointLabel(item.endpoint))}</div>
      <div class="value">${escapeHtml(formatTime(item.fetched_at))}</div>
      <div class="meta">状态：${escapeHtml(item.status_code)} ／ 变更：${escapeHtml(item.change_count)}</div>
    `;
    cards.appendChild(card);

    const row = document.createElement('div');
    row.className = 'chart-row';
    row.innerHTML = `
      <div class="chart-name">${escapeHtml(endpointLabel(item.endpoint))}</div>
      <div class="chart-bar-bg"><div class="chart-bar" style="width:${((Number(item.change_count) || 0) / maxCount) * 100}%"></div></div>
      <div class="chart-count">${escapeHtml(item.change_count)}</div>
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
    tbody.innerHTML = '<tr><td colspan="4" class="empty">暂无快照</td></tr>';
    return;
  }

  result.data.forEach((item) => {
    const row = document.createElement('tr');
    row.innerHTML = `
      <td><a href="/api/snapshot/${escapeHtml(item.id)}" target="_blank">${escapeHtml(item.id)}</a></td>
      <td>${escapeHtml(endpointLabel(item.endpoint))}</td>
      <td>${escapeHtml(formatTime(item.fetched_at))}</td>
      <td>${escapeHtml(item.status_code)}</td>
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
    tbody.innerHTML = '<tr><td colspan="6" class="empty">暂无变更</td></tr>';
    return;
  }

  result.data.forEach((item) => {
    const kind = item.change_type;
    const kindClass = ['added', 'removed'].includes(kind) ? kind : 'modified';
    const oldVal = safeValue(item.old_value);
    const newVal = safeValue(item.new_value);
    const row = document.createElement('tr');
    row.className = `diff-row diff-row-${kindClass}`;
    row.innerHTML = `
      <td>${escapeHtml(endpointLabel(item.endpoint))}</td>
      <td><span class="type type-${escapeHtml(kindClass)}">${escapeHtml(typeLabel(kind))}</span></td>
      <td class="path"><code>${escapeHtml(item.path)}</code></td>
      <td class="diff-cell diff-del">${item.old_value == null ? '' : `<span class="sign">-</span><pre>${escapeHtml(oldVal)}</pre>`}</td>
      <td class="diff-cell diff-add">${item.new_value == null ? '' : `<span class="sign">+</span><pre>${escapeHtml(newVal)}</pre>`}</td>
      <td class="time">${escapeHtml(formatTime(item.created_at))}</td>
    `;
    tbody.appendChild(row);
  });
}

loadStatus();
loadHistory();
loadChanges();
setInterval(() => {
  loadStatus();
  loadHistory();
  loadChanges();
}, 30000);
