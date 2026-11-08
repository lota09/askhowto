// DumpStartup이 startup_scripts를 다 실행한 뒤 부른다: 장인 설비 정의(global.artisanMachineDefinitions)와 판매가 표(global.trades) -> JSON
(function () {
  function plain(v) {
    if (v === null || v === undefined) return null;
    if (typeof v === "number" || typeof v === "boolean") return v;
    if (typeof v === "string") return v;
    if (v instanceof Map) { var o = {}; v.forEach(function (val, key) { o[String(key)] = plain(val); }); return o; }
    if (Array.isArray(v)) return v.map(plain);
    if (typeof v === "object") { var r = {}; for (var k in v) { if (typeof v[k] !== "function") r[k] = plain(v[k]); } return r; }
    return String(v);
  }
  var out = { machines: [], trades: {} };
  (global.artisanMachineDefinitions || []).forEach(function (d) {
    out.machines.push({ id: d.id, stageCount: d.stageCount, maxInput: d.maxInput, upgrade: d.upgrade || null, recipes: plain(d.recipes) });
  });
  if (global.trades) global.trades.forEach(function (t, id) { out.trades[String(id)] = { value: Number(t.value), multiplier: String(t.multiplier) }; });
  return JSON.stringify(out);
})();
