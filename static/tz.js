// 时区适配（只在本地服务页加载）。
//
// 设计：
// - 服务端页：按浏览器时区展示所有时间，顶部实时显示「当前使用哪个时区」，
//   并允许在常见时区里手动切换（写 localStorage，`?tz=Area/City` 可直接钉住）；
// - GitHub Pages 静态页不加载本脚本，时间固定为北京时间（UTC+8），不可修改。
//
// 数据侧写入的时间一律是北京时间串 `YYYY-MM-DD HH:MM:SS`，所以本脚本先按
// UTC+8 还原成时间戳，再按目标时区格式化，避免依赖后端改造。
(function () {
  'use strict';

  var STORE_KEY = 'hypixel-tz';
  var BEIJING = 'Asia/Shanghai';

  // 常见时区（切换器用；浏览器所在时区若不在列表里会自动补到最前）
  var ZONES = [
    'Asia/Shanghai', 'Asia/Hong_Kong', 'Asia/Taipei', 'Asia/Tokyo', 'Asia/Seoul',
    'Asia/Singapore', 'Asia/Kuala_Lumpur', 'Asia/Bangkok', 'Asia/Jakarta', 'Asia/Manila',
    'Asia/Ho_Chi_Minh', 'Asia/Kolkata', 'Asia/Karachi', 'Asia/Dhaka', 'Asia/Kathmandu',
    'Asia/Dubai', 'Asia/Riyadh', 'Asia/Tehran', 'Asia/Jerusalem', 'Asia/Istanbul',
    'Europe/Moscow', 'Europe/Kyiv', 'Europe/Warsaw', 'Europe/Berlin', 'Europe/Paris',
    'Europe/Madrid', 'Europe/Rome', 'Europe/Amsterdam', 'Europe/London', 'Europe/Dublin',
    'Europe/Lisbon', 'Europe/Stockholm', 'Europe/Oslo', 'Europe/Helsinki', 'Europe/Athens',
    'Europe/Bucharest', 'Europe/Prague', 'Europe/Vienna', 'Europe/Zurich', 'Europe/Budapest',
    'Africa/Cairo', 'Africa/Lagos', 'Africa/Nairobi', 'Africa/Johannesburg', 'Africa/Casablanca',
    'America/New_York', 'America/Chicago', 'America/Denver', 'America/Phoenix',
    'America/Los_Angeles', 'America/Vancouver', 'America/Toronto', 'America/Mexico_City',
    'America/Bogota', 'America/Lima', 'America/Santiago', 'America/Sao_Paulo',
    'America/Argentina/Buenos_Aires', 'Pacific/Honolulu', 'Pacific/Auckland',
    'Australia/Sydney', 'Australia/Melbourne', 'Australia/Brisbane', 'Australia/Perth',
    'Australia/Adelaide', 'UTC'
  ];

  // 后端口径：数据里所有 `YYYY-MM-DD HH:MM:SS` 都是北京时间
  var TIME_SRC = '\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}';
  var TIME_RE = new RegExp(TIME_SRC, 'g');
  var TIME_TEST = new RegExp(TIME_SRC);
  // 这些标签内的内容是用户数据原文，不做时区换算
  var SKIP_TAGS = { CODE: 1, PRE: 1, SCRIPT: 1, STYLE: 1, TEXTAREA: 1, KBD: 1, SAMP: 1 };

  var cfg = {};
  try {
    var el = document.getElementById('tz-config');
    if (el) cfg = JSON.parse(el.textContent || '{}') || {};
  } catch (e) { cfg = {}; }
  // 「（北京时间）」在各语言里的写法不同，由模板注入，便于把后缀同步成当前时区
  var beijingLabel = cfg.beijing_label || '（北京时间）';

  var state = { zone: BEIJING, pinned: false, offset: 'UTC+08:00' };
  var records = []; // 参与换算的文本节点（保存原文，切换时区时重算）
  var scheduled = false;

  function pad(n, w) {
    var s = String(n);
    while (s.length < (w || 2)) s = '0' + s;
    return s;
  }

  function browserZone() {
    try {
      var z = Intl.DateTimeFormat().resolvedOptions().timeZone;
      return z || 'UTC';
    } catch (e) { return 'UTC'; }
  }

  function zoneOk(zone) {
    if (!zone || typeof zone !== 'string') return false;
    try {
      new Intl.DateTimeFormat('en-US', { timeZone: zone }).format(new Date());
      return true;
    } catch (e) { return false; }
  }

  // 取某时区在给定时刻相对 UTC 的分钟偏移（自动处理夏令时）
  function offsetMinutes(zone, date) {
    var d = date || new Date();
    try {
      var parts = new Intl.DateTimeFormat('en-US', {
        timeZone: zone, hour12: false,
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit'
      }).formatToParts(d);
      var m = {};
      parts.forEach(function (p) { m[p.type] = p.value; });
      var wall = Date.UTC(+m.year, +m.month - 1, +m.day, +m.hour % 24, +m.minute, +m.second);
      return Math.round((wall - d.getTime()) / 60000);
    } catch (e) { return 0; }
  }

  function offsetLabel(zone, date) {
    var mins = offsetMinutes(zone, date);
    var sign = mins < 0 ? '-' : '+';
    var abs = Math.abs(mins);
    return 'UTC' + sign + pad(Math.floor(abs / 60)) + ':' + pad(abs % 60);
  }

  // 「YYYY-MM-DD HH:MM:SS」按北京时间解析成时间戳
  function parseBeijing(text) {
    var m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/.exec(text);
    if (!m) return null;
    return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4] - 8, +m[5], +m[6]));
  }

  function formatInZone(date, zone) {
    if (!date || isNaN(date.getTime())) return null;
    try {
      var parts = new Intl.DateTimeFormat('en-US', {
        timeZone: zone, hour12: false,
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit'
      }).formatToParts(date);
      var m = {};
      parts.forEach(function (p) { m[p.type] = p.value; });
      return m.year + '-' + m.month + '-' + m.day + ' ' +
        pad(+m.hour % 24) + ':' + m.minute + ':' + m.second;
    } catch (e) { return null; }
  }

  // 供页面脚本复用：任意时间值（北京时间串 / epoch 毫秒 / ISO）→ 当前时区串
  function formatValue(value) {
    if (value === null || value === undefined || value === '') return '-';
    var text = String(value).trim();
    if (!text) return '-';
    if (/^\d{10,}$/.test(text)) {
      return formatInZone(new Date(+text), state.zone) || text;
    }
    var bj = parseBeijing(text);
    if (bj) return formatInZone(bj, state.zone) || text;
    var iso = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(text) ? text + 'Z' : text;
    var dt = new Date(iso);
    if (isNaN(dt.getTime())) return text;
    return formatInZone(dt, state.zone) || text;
  }

  function loadPinned() {
    var q = null;
    try { q = new URLSearchParams(location.search).get('tz'); } catch (e) { q = null; }
    if (q === 'auto') {
      try { localStorage.removeItem(STORE_KEY); } catch (e) {}
      return null;
    }
    if (q && zoneOk(q)) {
      try { localStorage.setItem(STORE_KEY, q); } catch (e) {}
      return q;
    }
    try {
      var saved = localStorage.getItem(STORE_KEY);
      if (saved && zoneOk(saved)) return saved;
    } catch (e) {}
    return null;
  }

  function eligible(node) {
    var parent = node.parentNode;
    if (!parent || parent.nodeType !== 1) return false;
    if (SKIP_TAGS[parent.nodeName]) return false;
    if (parent.closest && parent.closest('[data-no-tz]')) return false;
    return true;
  }

  function rawOf(node) {
    if (node.__tzRaw === undefined) node.__tzRaw = node.nodeValue;
    return node.__tzRaw;
  }

  // 「（北京时间）」→「（UTC+09:00）」：跟随原文的括号风格与前置空格
  function labelReplacement() {
    var lead = beijingLabel.charAt(0) === ' ' ? ' ' : '';
    if (beijingLabel.indexOf('（') !== -1 || beijingLabel.indexOf('）') !== -1) {
      return lead + '（' + state.offset + '）';
    }
    if (beijingLabel.indexOf('(') !== -1) {
      return lead + '(' + state.offset + ')';
    }
    return lead + state.offset;
  }

  function convert(raw) {
    var out = raw;
    if (beijingLabel && out.indexOf(beijingLabel) !== -1) {
      out = out.split(beijingLabel).join(labelReplacement());
    }
    if (TIME_TEST.test(out)) {
      out = out.replace(TIME_RE, function (m) { return formatValue(m); });
    }
    return out;
  }

  function collect(root) {
    if (!root || !document.createTreeWalker) return;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var node;
    while ((node = walker.nextNode())) {
      if (!eligible(node)) continue;
      var raw = rawOf(node);
      if (beijingLabel && raw.indexOf(beijingLabel) !== -1) {
        // 只可能是时区后缀
      } else if (!TIME_TEST.test(raw)) {
        continue;
      }
      if (records.indexOf(node) === -1) records.push(node);
      var out = convert(raw);
      if (out !== node.nodeValue) node.nodeValue = out;
    }
  }

  function renderSelect() {
    var select = document.querySelector('[data-tz-select]');
    if (!select) return;
    var browser = browserZone();
    var list = ZONES.slice();
    [browser, state.zone].forEach(function (z) {
      if (z && list.indexOf(z) === -1) list.unshift(z);
    });
    var frag = document.createDocumentFragment();
    // 第一项：跟随浏览器（🌐 标记）
    var auto = document.createElement('option');
    auto.value = 'auto';
    auto.textContent = '🌐 ' + browser + ' · ' + offsetLabel(browser);
    frag.appendChild(auto);
    list.forEach(function (zone) {
      if (zone === browser) return;
      var opt = document.createElement('option');
      opt.value = zone;
      opt.textContent = zone + ' · ' + offsetLabel(zone);
      frag.appendChild(opt);
    });
    select.innerHTML = '';
    select.appendChild(frag);
    select.value = state.pinned ? state.zone : 'auto';
    select.title = 'Timezone · auto-detected from browser; add ?tz=Area/City to pin';
    var wrap = select.closest('[data-tz-switch]');
    if (wrap) wrap.setAttribute('data-tz-pinned', state.pinned ? '1' : '0');
  }

  function applyAll() {
    state.offset = offsetLabel(state.zone);
    // 清理已脱离文档的节点（app.js 每 30 秒整体重渲染表格）
    records = records.filter(function (node) { return node.isConnected; });
    records.forEach(function (node) {
      if (!node.parentNode) return;
      var out = convert(rawOf(node));
      if (out !== node.nodeValue) node.nodeValue = out;
    });
    renderSelect();
  }

  function refresh() {
    if (scheduled) return;
    scheduled = true;
    var run = function () {
      scheduled = false;
      collect(document.body);
    };
    if (window.requestAnimationFrame) requestAnimationFrame(run); else setTimeout(run, 16);
  }

  function setZone(zone) {
    if (!zoneOk(zone)) return;
    state.zone = zone;
    state.pinned = zone !== browserZone();
    applyAll();
  }

  function init() {
    var pinned = loadPinned();
    state.pinned = !!pinned;
    state.zone = pinned || browserZone();
    if (!zoneOk(state.zone)) state.zone = BEIJING;
    state.offset = offsetLabel(state.zone);
    collect(document.body);
    applyAll();

    var select = document.querySelector('[data-tz-select]');
    if (select) {
      select.addEventListener('change', function () {
        var value = select.value;
        if (value === 'auto') {
          try { localStorage.removeItem(STORE_KEY); } catch (e) {}
          state.pinned = false;
          state.zone = browserZone();
          applyAll();
          return;
        }
        try { localStorage.setItem(STORE_KEY, value); } catch (e) {}
        setZone(value);
      });
    }

    // 动态内容（app.js 每 30 秒刷新的表格、bingo 倒计时等）同样要换算
    if (window.MutationObserver && document.body) {
      var observer = new MutationObserver(function (muts) {
        for (var i = 0; i < muts.length; i++) {
          if (muts[i].addedNodes && muts[i].addedNodes.length) { refresh(); return; }
        }
      });
      observer.observe(document.body, { childList: true, subtree: true });
    }

    window.addEventListener('storage', function (e) {
      if (e.key !== STORE_KEY) return;
      state.pinned = !!e.newValue && zoneOk(e.newValue);
      state.zone = state.pinned ? e.newValue : browserZone();
      applyAll();
    });
  }

  // 对外接口（app.js / 自定义脚本可复用）
  window.TZ = {
    format: formatValue,
    zone: function () { return state.zone; },
    offset: function () { return state.offset; },
    label: function () { return state.zone + ' ' + state.offset; },
    setZone: function (zone) { setZone(zone); },
    apply: function () { collect(document.body); applyAll(); }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
