/* ---------------------------------------------------------------------------
   Bingo 任务面板交互
   ---------------------------------------------------------------------------
   1. 倒计时：完全由浏览器本地时间计算，每秒刷新。Actions 每次抓取有延迟，
      页面上的「剩余时间」不能写死成构建时刻的快照。
   2. 5×5 方格：点击任意格子打开详情弹层（小屏下格子里放不下的完整文案在这里看）。
--------------------------------------------------------------------------- */
(function () {
  'use strict';

  var panel = document.querySelector('[data-bingo-panel]');
  if (!panel) return;

  var stateEl = panel.querySelector('[data-bingo-state]');
  var countdownEl = panel.querySelector('[data-bingo-countdown]');
  var labelEl = panel.querySelector('.bingo-state-label');

  var STATE_LABELS = { upcoming: '未开始', active: '进行中', ended: '已结束', unknown: '未知' };

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
    return days > 0 ? days + ' 天 ' + clock : clock;
  }

  function parseMs(value) {
    var num = Number(value);
    return Number.isFinite(num) && num > 0 ? num : 0;
  }

  var startMs = stateEl ? parseMs(stateEl.dataset.startMs) : 0;
  var endMs = stateEl ? parseMs(stateEl.dataset.endMs) : 0;

  function tick() {
    if (!stateEl || !countdownEl || !startMs || !endMs) return;

    var now = Date.now();
    var state;
    var text;

    if (now < startMs) {
      state = 'upcoming';
      text = '距开始 ' + humanDuration(startMs - now);
    } else if (now > endMs) {
      state = 'ended';
      text = '已结束 ' + humanDuration(now - endMs);
    } else {
      state = 'active';
      text = '剩余 ' + humanDuration(endMs - now);
    }

    /* 页面可能是几分钟前由 Actions 构建的，状态以浏览器当前时间为准 */
    if (stateEl.dataset.state !== state) {
      stateEl.dataset.state = state;
      stateEl.classList.remove('bingo-state-upcoming', 'bingo-state-active', 'bingo-state-ended');
      stateEl.classList.add('bingo-state-' + state);
      if (labelEl) labelEl.textContent = STATE_LABELS[state] || state;
    }

    if (countdownEl.textContent !== text) {
      countdownEl.textContent = text;
    }
  }

  tick();
  window.setInterval(tick, 1000);
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) tick();
  });

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

  function openDialog(cell) {
    if (dlgIndex) dlgIndex.textContent = cell.dataset.index || '';
    if (dlgName) dlgName.textContent = (cell.querySelector('.bingo-cell-name') || {}).textContent || '';
    if (dlgReq) dlgReq.textContent = cell.dataset.requirement || '—';
    if (dlgProgress) dlgProgress.textContent = cell.dataset.progress || '暂无全服进度';
    if (dlgPercent) dlgPercent.textContent = cell.dataset.percent ? cell.dataset.percent + '%' : '—';
    if (dlgId) dlgId.textContent = cell.dataset.goalId || '';

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
