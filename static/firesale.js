/* ---------------------------------------------------------------------------
   限时特卖（Firesales）面板交互
   ---------------------------------------------------------------------------
   「显示额外数据」按钮：展开/收起全部期次（含已结束）与原始 JSON。
   特卖的开始/结束时间只做展示，不做倒计时，因此没有定时器。
--------------------------------------------------------------------------- */
(function () {
  'use strict';

  var I18N = (function () {
    try {
      var el = document.getElementById('i18n-pack');
      return el ? (JSON.parse(el.textContent) || {}) : {};
    } catch (e) { return {}; }
  })();

  function T(text) {
    return I18N[text] || text;
  }

  var panel = document.querySelector('[data-firesale-panel]');
  if (!panel) return;

  var toggle = panel.querySelector('[data-firesale-toggle]');
  var extra = panel.querySelector('[data-firesale-extra]');
  if (!toggle || !extra) return;

  toggle.addEventListener('click', function () {
    var willShow = extra.hidden;
    extra.hidden = !willShow;
    toggle.setAttribute('aria-expanded', willShow ? 'true' : 'false');
    toggle.textContent = willShow ? T('收起额外数据') : T('显示额外数据');
  });
})();
