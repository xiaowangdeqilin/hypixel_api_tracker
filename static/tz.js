// 时区适配（服务端页与 GitHub Pages 静态页共用）。
//
// 设计：
// - 数据侧写入的时间一律是北京时间串 `YYYY-MM-DD HH:MM:SS`，本脚本先按 UTC+8
//   还原成时间戳，再按目标时区格式化，不依赖后端改造；
// - 顶部控件显示当前使用的时区，可切换到 60+ 常见时区（记忆在 localStorage），
//   也支持 `?tz=Area/City` 钉住、`?tz=auto` 恢复「跟随浏览器」；
// - 默认时区由 `#tz-config` 的 `follow_browser` 决定：
//   服务端页跟随浏览器，静态页默认北京时间（UTC+8）；
// - 与 `site-i18n.js` 协作：切语言后调用 `TZ.setBeijingLabel()` + `TZ.reset()`
//   重新换算，保证「（北京时间）」后缀与语言一致。
(function () {
  'use strict';

  var STORE_KEY = 'hypixel-tz';
  var BEIJING = 'Asia/Shanghai';
  var AUTO = 'auto';

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
  var followByDefault = cfg.follow_browser !== false;
  // 「（北京时间）」在各语言里的写法不同，由模板 / 语言模块注入
  var beijingLabel = cfg.beijing_label || '（北京时间）';

  var state = { zone: BEIJING, follow: followByDefault, offset: 'UTC+08:00' };
  var records = []; // 参与换算的文本节点（保存原文，切换时区时重算）
  var scheduled = false;

  function pad(n, w) {
    var s = String(n);
    while (s.length < (w || 2)) s = '0' + s;
    return s;
  }

  function browserZone() {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
    } catch (e) { return 'UTC'; }
  }

  function zoneOk(zone) {
    if (!zone || typeof zone !== 'string') return false;
    try {
      new Intl.DateTimeFormat('en-US', { timeZone: zone }).format(new Date());
      return true;
    } catch (e) { return false; }
  }

  // 某时区在给定时刻相对 UTC 的分钟偏移（自动处理夏令时）
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

  // 任意时间值（北京时间串 / epoch 毫秒 / ISO）→ 当前时区串
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

  function loadPreference() {
    var q = null;
    try { q = new URLSearchParams(location.search).get('tz'); } catch (e) { q = null; }
    if (q === AUTO) {
      try { localStorage.setItem(STORE_KEY, AUTO); } catch (e) {}
      return AUTO;
    }
    if (q && zoneOk(q)) {
      try { localStorage.setItem(STORE_KEY, q); } catch (e) {}
      return q;
    }
    try {
      var saved = localStorage.getItem(STORE_KEY);
      if (saved === AUTO) return AUTO;
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
        // 仅时区后缀
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
    var auto = document.createElement('option');
    auto.value = AUTO;
    auto.textContent = '🌐 ' + browser + ' · ' + offsetLabel(browser);
    frag.appendChild(auto);
    var seen = {};
    seen[AUTO] = 1;
    list.forEach(function (zone) {
      // 不再跳过浏览器所在时区：跟随模式另有 auto 项，这里保留同名的具体时区，
      // 否则「静态页默认北京 + 浏览器也在北京」时下拉会没有选中项（显示空白）
      if (seen[zone]) return;
      seen[zone] = 1;
      var opt = document.createElement('option');
      opt.value = zone;
      opt.textContent = zone + ' · ' + offsetLabel(zone);
      frag.appendChild(opt);
    });
    select.innerHTML = '';
    select.appendChild(frag);
    select.value = state.follow ? AUTO : state.zone;
    select.title = 'Timezone · pick a zone to convert every timestamp';
    var wrap = select.closest('[data-tz-switch]');
    if (wrap) wrap.setAttribute('data-tz-pinned', state.follow ? '0' : '1');
  }

  function applyAll() {
    state.offset = offsetLabel(state.zone);
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
    state.follow = false;
    applyAll();
  }

  function setFollow(on) {
    state.follow = !!on;
    state.zone = state.follow ? browserZone() : BEIJING;
    applyAll();
  }

  function init() {
    var pref = loadPreference();
    if (pref === AUTO) {
      state.follow = true;
      state.zone = browserZone();
    } else if (pref) {
      state.follow = false;
      state.zone = pref;
    } else if (followByDefault) {
      state.follow = true;
      state.zone = browserZone();
    } else {
      state.follow = false;
      state.zone = BEIJING;
    }
    if (!zoneOk(state.zone)) state.zone = BEIJING;

    collect(document.body);
    applyAll();

    var select = document.querySelector('[data-tz-select]');
    if (select) {
      select.addEventListener('change', function () {
        var value = select.value;
        if (value === AUTO) {
          try { localStorage.setItem(STORE_KEY, AUTO); } catch (e) {}
          setFollow(true);
          return;
        }
        try { localStorage.setItem(STORE_KEY, value); } catch (e) {}
        setZone(value);
      });
    }

    // 动态内容同样要换算
    if (window.MutationObserver && document.body) {
      var observer = new MutationObserver(function (muts) {
        for (var i = 0; i < muts.length; i++) {
          if (muts[i].addedNodes && muts[i].addedNodes.length) { refresh(); return; }
        }
      });
      observer.observe(document.body, { childList: true, subtree: true });
    }
  }

  // 对外接口
  window.TZ = {
    format: formatValue,
    zone: function () { return state.zone; },
    offset: function () { return state.offset; },
    label: function () { return state.zone + ' ' + state.offset; },
    setZone: function (zone) { setZone(zone); },
    setFollow: function (on) { setFollow(on); },
    // 换语言后同步「（北京时间）」的本地化写法并重算
    setBeijingLabel: function (label) {
      beijingLabel = label || '（北京时间）';
    },
    reset: function () {
      // 语言切换后 DOM 文本已换掉：清掉缓存的原始文本，否则会用旧中文覆盖译文
      records.forEach(function (node) { node.__tzRaw = undefined; });
      records = [];
      collect(document.body);
      applyAll();
    },
    apply: function () { collect(document.body); applyAll(); }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
