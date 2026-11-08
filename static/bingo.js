/* ---------------------------------------------------------------------------
   Bingo 任务面板交互
   ---------------------------------------------------------------------------
   1. 倒计时：完全由浏览器本地时间计算，每秒刷新。Actions 每次抓取有延迟，
      页面上的「剩余时间」不能写死成构建时刻的快照。
   2. 5×5 方格：点击任意格子打开详情弹层（小屏下格子里放不下的完整文案在这里看）。
--------------------------------------------------------------------------- */
(function () {
  'use strict';

  /* 多语言助手：译文来自 #i18n-pack（i18n 关闭时回退中文原文） */
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

  var panel = document.querySelector('[data-bingo-panel]');
  if (!panel) return;

  var stateEl = panel.querySelector('[data-bingo-state]');
  var countdownEl = panel.querySelector('[data-bingo-countdown]');
  var labelEl = panel.querySelector('.bingo-state-label');

  var STATE_LABELS = { upcoming: '未开始', active: '进行中', ended: '已结束', unknown: '未知' };
  /* 只有「未开始」和「进行中」需要倒计时；已结束只保留状态徽章 */
  var COUNTDOWN_PREFIX = { upcoming: '距开始 {time}', active: '剩余 {time}' };

  function pad(n) {
    return String(n).padStart(2, '0');
  }

  /* 毫秒 -> 「2 天 03:12:45」；不足一天时不显示天数，保证每秒变化可见 */
  function humanDuration(ms) {
    var total = Math.max(0, Math.floor(ms / 1000));
    var days = Math.floor(total / 86400);
    var hours = Math.floor((total % 86400) / 3600);
    var minutes = Math.floor((total % 3600) / 60);
    var seconds = total % 60;
    var clock = pad(hours) + ':' + pad(minutes) + ':' + pad(seconds);
    return days > 0 ? T('{days} 天 {time}', { days: days, time: clock }) : clock;
  }

  function parseMs(value) {
    var num = Number(value);
    return Number.isFinite(num) && num > 0 ? num : 0;
  }

  var startMs = stateEl ? parseMs(stateEl.dataset.startMs) : 0;
  var endMs = stateEl ? parseMs(stateEl.dataset.endMs) : 0;
  var timer = null;
  var finished = false;

  function applyState(state, text) {
    /* 组件同步变化：徽章配色 + 状态文案 + 倒计时（已结束时整个隐藏） */
    if (stateEl.dataset.state !== state) {
      stateEl.dataset.state = state;
      stateEl.classList.remove('bingo-state-upcoming', 'bingo-state-active', 'bingo-state-ended');
      stateEl.classList.add('bingo-state-' + state);
      if (labelEl) labelEl.textContent = T(STATE_LABELS[state] || state);
    }
    if (countdownEl) {
      var showCountdown = text !== null;
      if (countdownEl.hidden !== !showCountdown) countdownEl.hidden = !showCountdown;
      if (showCountdown && countdownEl.textContent !== text) {
        countdownEl.textContent = text;
      }
    }
  }

  function tick() {
    if (!stateEl || !startMs || !endMs) return false;

    var now = Date.now();
    var state;
    var text;

    if (now < startMs) {
      state = 'upcoming';
      text = T(COUNTDOWN_PREFIX.upcoming, { time: humanDuration(startMs - now) });
    } else if (now > endMs) {
      state = 'ended';
      text = null; /* 已结束不再计时 */
    } else {
      state = 'active';
      text = T(COUNTDOWN_PREFIX.active, { time: humanDuration(endMs - now) });
    }

    /* 页面可能是几小时前由 Actions 构建的，状态以浏览器当前时间为准：
       到点自动从未开始切进行中、从进行中切已结束，无需等下次抓取 */
    applyState(state, text);

    if (state === 'ended') {
      finished = true;
      if (timer) { window.clearInterval(timer); timer = null; }
      return false;
    }
    return true;
  }

  if (tick()) {
    timer = window.setInterval(tick, 1000);
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden && !finished) tick();
    });
  }

  /* ---------- 详情弹层 ---------- */
  var dialog = panel.querySelector('[data-bingo-dialog]');
  if (!dialog || typeof dialog.showModal !== 'function') return;

  var dlgIndex = dialog.querySelector('[data-dialog-index]');
  var dlgName = dialog.querySelector('[data-dialog-name]');
  var dlgLore = dialog.querySelector('[data-dialog-lore]');
  var dlgReq = dialog.querySelector('[data-dialog-requirement]');
  var dlgProgress = dialog.querySelector('[data-dialog-progress]');
  var dlgPercent = dialog.querySelector('[data-dialog-percent]');
  var dlgId = dialog.querySelector('[data-dialog-id]');
  var dlgKind = dialog.querySelector('[data-dialog-kind]');

  function openDialog(cell) {
    if (dlgIndex) dlgIndex.textContent = cell.dataset.index || '';
    if (dlgName) dlgName.textContent = (cell.querySelector('.bingo-cell-name') || {}).textContent || '';
    if (dlgReq) dlgReq.textContent = cell.dataset.requirement || '—';
    if (dlgProgress) dlgProgress.textContent = cell.dataset.progress || T('暂无全服进度');
    if (dlgPercent) dlgPercent.textContent = cell.dataset.percent ? cell.dataset.percent + '%' : '—';
    if (dlgId) dlgId.textContent = cell.dataset.goalId || '';
    if (dlgKind) dlgKind.textContent = cell.dataset.kindLabel || '—';

    if (dlgLore) {
      var lore = cell.querySelector('.bingo-cell-lore');
      dlgLore.replaceChildren();
      if (lore) {
        Array.prototype.forEach.call(lore.childNodes, function (node) {
          dlgLore.appendChild(node.cloneNode(true));
        });
      }
    }

    dialog.showModal();
  }

  panel.addEventListener('click', function (event) {
    var cell = event.target.closest('[data-bingo-cell]');
    if (cell) openDialog(cell);
  });

  var closeBtn = dialog.querySelector('[data-dialog-close]');
  if (closeBtn) closeBtn.addEventListener('click', function () { dialog.close(); });

  /* 点击弹层外的遮罩区域关闭 */
  dialog.addEventListener('click', function (event) {
    if (event.target === dialog) dialog.close();
  });
})();
