// 静态站（GitHub Pages）的多语言切换。
//
// 原理：构建时 `src/site_i18n.rs` 会把每个中文界面文案所在的元素标上
// `data-k="<中文原文>"`（带可变部分时另有 `data-p='{...}'`），这里按词条包
//（`#i18n-packs`，103 种语言）替换其 innerHTML，实现零后端切换语言。
//
// 与 tz.js 协作：换语言后重新设置「（北京时间）」的本地化写法并调用 TZ.reset()，
// 让时间与后缀都按当前语言 + 当前时区重新渲染。
(function () {
  'use strict';

  var STORE = 'hypixel-site-lang';
  var ZH = 'zh-CN';
  var RTL = ['ar', 'he', 'fa', 'ur', 'sd', 'ps'];

  var bundle = {};
  try {
    var el = document.getElementById('i18n-packs');
    if (el) bundle = JSON.parse(el.textContent || '{}') || {};
  } catch (e) { bundle = {}; }

  var keys = bundle.keys || [];
  var packs = bundle.packs || {};
  var index = Object.create(null);
  for (var i = 0; i < keys.length; i++) index[keys[i]] = i;

  var currentLang = ZH;

  function available(code) {
    return code === ZH || !!packs[code];
  }

  // 占位符回填：参数值本身若是词条（如状态「已结束」），一并翻译成当前语言
  function fill(template, paramsJson, pack) {
    if (!paramsJson) return template;
    var params;
    try { params = JSON.parse(paramsJson); } catch (e) { return template; }
    return template.replace(/\{(\w+)\}/g, function (m, name) {
      var v = params[name];
      if (v === undefined) return m;
      v = String(v);
      if (pack) {
        var i = index[v];
        if (i !== undefined && pack[i]) v = pack[i];
      }
      return v;
    });
  }

  function label(codes) {
    for (var n = 0; n < codes.length; n++) {
      var i = index[codes[n]];
      var pack = packs[currentLang];
      if (i !== undefined && pack && pack[i]) return pack[i];
    }
    return null;
  }

  function apply(lang) {
    if (!available(lang)) lang = ZH;
    currentLang = lang;
    var pack = packs[lang];
    var nodes = document.querySelectorAll('[data-k]');
    for (var n = 0; n < nodes.length; n++) {
      var node = nodes[n];
      if (node.__zh === undefined) node.__zh = node.innerHTML;
      var key = node.getAttribute('data-k');
      var tpl = null;
      if (lang !== ZH) {
        var idx = index[key];
        if (idx !== undefined && pack && pack[idx]) tpl = pack[idx];
      }
      if (tpl === null || tpl === undefined) {
        // 中文或缺失译文：恢复构建时的原文
        if (node.innerHTML !== node.__zh) node.innerHTML = node.__zh;
        continue;
      }
      var out = fill(tpl, node.getAttribute('data-p'), pack);
      if (node.innerHTML === out) continue;
      if (out.indexOf('<') === -1 && !node.firstElementChild) {
        // 纯文本词条：直接改文本节点，避免几万次 HTML 重解析
        var first = node.firstChild;
        if (first && first.nodeType === 3 && !first.nextSibling) {
          if (first.nodeValue !== out) first.nodeValue = out;
          continue;
        }
        node.textContent = out;
        continue;
      }
      node.innerHTML = out;
    }
    document.documentElement.lang = lang === ZH ? 'zh-CN' : lang;
    document.documentElement.dir = RTL.indexOf(lang) >= 0 ? 'rtl' : 'ltr';

    // 让 bingo.js / firesale.js 这类前端脚本也能拿到当前语言
    var live = {};
    if (lang !== ZH && pack) {
      for (var k = 0; k < keys.length; k++) {
        if (pack[k]) live[keys[k]] = pack[k];
      }
    }
    window.I18N = live;
    if (window.BINGO_REFRESH) window.BINGO_REFRESH();
    if (window.THEME_REFRESH) window.THEME_REFRESH();

    if (window.TZ) {
      var beijing = label(['（北京时间）']);
      if (lang !== ZH && beijing) window.TZ.setBeijingLabel(beijing);
      else window.TZ.setBeijingLabel('（北京时间）');
      window.TZ.reset();
    }
  }

  function save(lang) {
    try { localStorage.setItem(STORE, lang); } catch (e) {}
  }

  function resolve() {
    var q = null;
    try { q = new URLSearchParams(location.search).get('lang'); } catch (e) { q = null; }
    if (q === 'zh-CN' || q === 'zh') {
      save(ZH);
      return ZH;
    }
    if (q && available(q)) {
      save(q);
      return q;
    }
    try {
      var saved = localStorage.getItem(STORE);
      if (saved && available(saved)) return saved;
    } catch (e) {}
    return ZH;
  }

  function init() {
    var lang = resolve();
    var select = document.querySelector('[data-site-lang]');
    if (select) {
      select.value = lang;
      select.addEventListener('change', function () {
        save(select.value);
        apply(select.value);
      });
    }
    apply(lang);
  }

  window.SiteI18N = {
    apply: apply,
    current: function () { return currentLang; },
    languages: function () { return Object.keys(packs); }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
