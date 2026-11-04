// priority: -1000
console.info("[ASKHOWTO] askhowto_prices.js loaded");

// 도우미 봇이 판매가 질문에 답할 수 있게, 배송 상자 판매가 표(global.trades, startup_scripts/globalRegistry.js)를
// 서버가 뜰 때 파일로 남긴다. 표는 서버 설정(getConfiguredValue 배수)까지 반영된 값이다.
//   출력: local/askhowto/prices.json  { "<아이템 ID>": { "value": 기본 판매가, "kind": "crop|gem|wood|meat|..." }, ... }
//   ('<아이템 ID>/<변종>' 꼴의 키는 슬라임 플로트·나비 변종)
// 사이드카(workspace/askhowto)의 build가 이 파일을 읽는다.
// 변수 이름을 다른 스크립트와 겹치지 않게 한다. KubeJS의 Rhino는 스크립트끼리 같은 이름의 const를 쓰면
// 'redeclaration of var out'으로 실패했다(11/04, askhowto_recipes.js의 out과 충돌).

ServerEvents.loaded(() => {
  try {
    if (!global.trades) {
      console.warn("[ASKHOWTO] global.trades 없음 - 판매가 덤프 건너뜀");
      return;
    }
    let askhowtoPriceOut = {};
    let askhowtoPriceCount = 0;
    global.trades.forEach((trade, id) => {
      try {
        // multiplier: 'shippingbin:crop_sell_multiplier' -> 'crop'
        let askhowtoKind = String(trade.multiplier || "").replace(/^.*:/, "").replace(/_sell_multiplier$/, "");
        askhowtoPriceOut[String(id)] = { value: Number(trade.value), kind: askhowtoKind };
        askhowtoPriceCount++;
      } catch (err) {
        // 항목 하나가 실패해도 나머지는 남긴다.
      }
    });
    askhowtoPriceOut._askhowto = JSON.stringify({ version: 1, count: askhowtoPriceCount });
    JsonIO.write("local/askhowto/prices.json", askhowtoPriceOut);
    console.info(`[ASKHOWTO] ${askhowtoPriceCount} prices -> local/askhowto/prices.json`);
  } catch (err) {
    console.warn(`[ASKHOWTO] 판매가 덤프 실패: ${err}`);
  }
});
