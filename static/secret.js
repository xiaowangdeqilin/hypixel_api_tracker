/* ---------------------------------------------------------------------------
   秘密 Bingo 面板：提示按时间依次解锁
   ---------------------------------------------------------------------------
   官方按时间逐步放出秘密任务提示，每个任务共 3 条。页面由 Actions 定时构建，
   构建时刻不一定等于用户打开页面的时刻，所以：
   1. 初始状态以「浏览器当前时间」为准重新校正一遍；
   2. 未解锁的提示显示「还有 xx 解锁」倒计时，每秒刷新；
   3. 到达解锁时刻自动展开提示文本，无需刷新，也无需等下次构建。
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

  var panel = document.querySelector('[data-secret-panel]');
  if (!panel) return;

  var cards = Array.prototype.slice.call(panel.querySelectorAll('[data-secret-goal]'));
  if (!cards.length) return;

  var timer = null;

  function pad(n) {
    return String(n).padStart(2, '0');
  }

  /* 毫秒 -> 「2 天 03:12:45」；不足一天只显示时钟，保证每秒可见变化 */
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

  function unlock(hint) {
    hint.classList.remove('is-locked');
    hint.classList.add('is-unlocked');
    var text = hint.querySelector('[data-hint-text]');
    var lock = hint.querySelector('[data-hint-lock]');
    if (text) text.hidden = false;
    if (lock) lock.hidden = true;
  }

  function lockWith(hint, remainMs) {
    var text = hint.querySelector('[data-hint-text]');
    var lock = hint.querySelector('[data-hint-lock]');
    if (text) text.hidden = true;
    if (lock) {
      lock.hidden = false;
      var label = T('还有 {time} 解锁', { time: humanDuration(remainMs) });
      if (lock.textContent !== label) lock.textContent = label;
    }
  }

  function updateCardCount(card) {
    var badge = card.querySelector('[data-secret-count]');
    if (!badge) return;
    var hints = card.querySelectorAll('[data-secret-hint]');
    var unlocked = 0;
    Array.prototype.forEach.call(hints, function (h) {
      if (h.classList.contains('is-unlocked')) unlocked++;
    });
    var label = unlocked + '/' + hints.length;
    if (badge.textContent !== label) badge.textContent = label;
    var done = unlocked >= hints.length && hints.length > 0;
    if (badge.classList.contains('is-done') !== done) badge.classList.toggle('is-done', done);
  }

  /* 返回仍处于未解锁状态的提示数量 */
  function tick() {
    var now = Date.now();
    var pending = 0;

    cards.forEach(function (card) {
      var hints = card.querySelectorAll('[data-secret-hint]');
      Array.prototype.forEach.call(hints, function (hint) {
        var ms = parseMs(hint.dataset.revealMs);
        if (!ms) return; /* 空槽：没有配置时间，保持占位 */
        if (now >= ms) {
          if (!hint.classList.contains('is-unlocked')) unlock(hint);
          return;
        }
        pending++;
        lockWith(hint, ms - now);
      });
      updateCardCount(card);
    });

    return pending;
  }

  function schedule() {
    var pending = tick();
    if (pending > 0 && !timer) {
      timer = window.setInterval(function () {
        if (tick() === 0) {
          window.clearInterval(timer);
          timer = null;
        }
      }, 1000);
    }
    if (pending === 0 && timer) {
      window.clearInterval(timer);
      timer = null;
    }
  }

  schedule();

  /* 页面在后台时定时器会被节流，回到前台立即校正一次 */
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) schedule();
  });
})();
