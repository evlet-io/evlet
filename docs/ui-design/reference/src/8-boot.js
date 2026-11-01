/* ---------- boot ---------- */
(function boot() {
  const th = store.get('theme', null); if (th) document.documentElement.dataset.theme = th;
  S.collapsed = store.get('collapsed', false); $('#app').classList.toggle('collapsed', S.collapsed);
  S.dealView = store.get('dealView', 'board');
  S.layoutMode = store.get('layoutMode', 'sample');
  const [r, p] = parseHash(); S.route = r; S.param = p;
  render(true);
})();
