// 多语言：译文来自 <head> 里 <script type="application/json" id="i18n-pack">
// （i18n 关闭时为空对象，所有 T() 直接回退中文原文）。
var I18N = (function () {
  try {
    var el = document.getElementById('i18n-pack');
    return el ? (JSON.parse(el.textContent) || {}) : {};
  } catch (e) { return {}; }
})();

function T(text, params) {
  var s = I18N[text] || text;
  if (params) {
    s = s.replace(/\{(\w+)\}/g, function (m, k) {
      return params[k] !== undefined ? params[k] : m;
    });
  }
  return s;
}

// 数据里保留原始英文标识，仅在展示层翻译成中文。
const ENDPOINT_LABELS = {
  collections: '收藏',
  skills: '技能',
  items: '物品',
  bazaar: '集市',
  packs: '资源包',
  bingo: 'Bingo 任务',
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
  return T(ENDPOINT_LABELS[value] || value);
}

function typeLabel(value) {
  return T(TYPE_LABELS[value] || value);
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

function prettyValue(value) {
  if (value == null) return '';
  let text;
  try {
    text = JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    text = String(value);
  }
  return text.length > 4000 ? text.slice(0, 4000) + '…' : text;
}

// 逐行 LCS 比对，返回 ctx / del / add 操作序列；文本过大时返回 null 由调用方降级。
function lcsOps(a, b) {
  const n = a.length;
  const m = b.length;
  if (n > 400 || m > 400 || n * m > 160000) return null;
  const dp = [];
  for (let i = 0; i <= n; i++) dp.push(new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j]
        ? dp[i + 1][j + 1] + 1
        : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const ops = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      ops.push({ kind: 'ctx', text: a[i] }); i++; j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      ops.push({ kind: 'del', text: a[i] }); i++;
    } else {
      ops.push({ kind: 'add', text: b[j] }); j++;
    }
  }
  while (i < n) ops.push({ kind: 'del', text: a[i++] });
  while (j < m) ops.push({ kind: 'add', text: b[j++] });
  return ops;
}

// 只输出发生变化的片段（带 2 行上下文），未变化的大段内容折叠省略。
function renderSegments(ops) {
  const keep = ops.map(() => false);
  ops.forEach((op, idx) => {
    if (op.kind === 'ctx') return;
    const from = Math.max(0, idx - 2);
    const to = Math.min(ops.length - 1, idx + 2);
    for (let k = from; k <= to; k++) keep[k] = true;
  });
  let html = '';
  let inSegment = false;
  ops.forEach((op, idx) => {
    if (!keep[idx]) {
      if (inSegment) html += '<div class="diff-omitted">… ' + T('未变化内容省略') + '</div>';
      inSegment = false;
      return;
    }
    inSegment = true;
    const css = op.kind === 'del' ? 'diff-del' : op.kind === 'add' ? 'diff-add' : 'diff-ctx';
    const sign = op.kind === 'del' ? '-' : op.kind === 'add' ? '+' : ' ';
    html += `<div class="diff-line ${css}"><span class="sign">${sign}</span>` +
      `<pre>${escapeHtml(op.text)}</pre></div>`;
  });
  return html || '<div class="diff-line diff-ctx"><span class="sign"> </span><pre>' + escapeHtml(T('（无可见行变化）')) + '</pre></div>';
}

function renderChangeContent(item, kindClass) {
  const oldText = prettyValue(item.old_value);
  const newText = prettyValue(item.new_value);
  const line = (css, sign, text) =>
    `<div class="diff-line ${css}"><span class="sign">${sign}</span>` +
    `<pre>${escapeHtml(text)}</pre></div>`;
  // 新增：只标绿，前面不出现任何红色行；删除：只标红。
  if (kindClass === 'added') return line('diff-add', '+', newText);
  if (kindClass === 'removed') return line('diff-del', '-', oldText);
  const ops = lcsOps(oldText.split('\n'), newText.split('\n'));
  if (ops) return renderSegments(ops);
  return line('diff-del', '-', oldText) + line('diff-add', '+', newText);
}

async function loadStatus() {
  const response = await fetch('/api/status');
  const result = await response.json();
  const cards = document.getElementById('status-cards');
  const chart = document.getElementById('chart');
  cards.innerHTML = '';
  chart.innerHTML = '';

  if (!result.data || result.data.length === 0) {
    cards.innerHTML = '<div class="card"><div class="label">' + escapeHtml(T('暂无数据')) +
      '</div><div class="value">' + escapeHtml(T('等待首次抓取')) + '</div></div>';
    return;
  }

  const maxCount = Math.max(...result.data.map(item => Number(item.change_count || 0))) || 1;

  result.data.forEach((item) => {
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
      <div class="label">${escapeHtml(endpointLabel(item.endpoint))}</div>
      <div class="value">${escapeHtml(formatTime(item.fetched_at))}</div>
      <div class="meta">${escapeHtml(T('状态：{status} ／ 变更：{count}', { status: item.status_code, count: item.change_count }))}</div>
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
    tbody.innerHTML = '<tr><td colspan="4" class="empty">' + escapeHtml(T('暂无快照')) + '</td></tr>';
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
  // 不限条数：服务端默认返回保留期（30 天）内的全部变更
  const response = await fetch('/api/changes');
  const result = await response.json();
  const tbody = document.getElementById('changes-body');
  tbody.innerHTML = '';

  if (!result.data || result.data.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty">' + escapeHtml(T('暂无变更')) + '</td></tr>';
    return;
  }

  result.data.forEach((item) => {
    const kind = item.change_type;
    const kindClass = ['added', 'removed'].includes(kind) ? kind : 'modified';
    const row = document.createElement('tr');
    row.className = `diff-row diff-row-${kindClass}`;
    row.innerHTML = `
      <td>${escapeHtml(endpointLabel(item.endpoint))}</td>
      <td><span class="type type-${escapeHtml(kindClass)}">${escapeHtml(typeLabel(kind))}</span></td>
      <td class="path"><code>${escapeHtml(item.path)}</code></td>
      <td class="diff-cell">${renderChangeContent(item, kindClass)}</td>
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
