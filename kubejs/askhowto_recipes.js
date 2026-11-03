// priority: -1000
console.info("[ASKHOWTO] askhowto_recipes.js loaded");

// 도우미 봇이 조합법 질문에 답할 수 있게, 서버가 레시피를 불러올 때(시작할 때) 최종 레시피 목록을 파일로 남긴다.
// priority -1000이라 다른 스크립트의 추가·삭제(removeRecipes.js, addToolRecipes.js 등)가 끝난 뒤에 돈다.
//   출력: local/askhowto/recipes.json  { "<레시피 ID>": "<레시피 JSON 문자열>", ... }
// 사이드카(workspace/askhowto)의 build가 이 파일을 읽는다.

ServerEvents.recipes((e) => {
  const started = Date.now();
  const out = {};
  let count = 0;
  const save = (r) => {
    try {
      if (r.removed) return;
      // 스크립트로 추가·수정한 레시피는 serialize() 뒤에야 json이 채워진다 (KubeJS도 끝날 때 똑같이 부른다).
      // 원래 레시피는 건드리지 않는다.
      if (r.newRecipe || r.hasChanged()) r.serialize();
      if (r.json) {
        // 스크립트가 추가한 레시피는 json의 type이 'unknown'으로 남아 있을 수 있어서 실제 종류로 채운다.
        if (String(r.json.get("type")) == '"unknown"') r.json.addProperty("type", String(r.getType()));
        out[String(r.getOrCreateId())] = String(r.json);
        count++;
      }
    } catch (err) {
      // 레시피 하나가 실패해도 나머지는 남긴다.
    }
  };
  // forEachRecipe는 원래 레시피만 돌고, 스크립트가 추가한 레시피(e.smithing 등)는 addedRecipes에 따로 있다.
  e.forEachRecipe({}, save);
  e.addedRecipes.forEach(save);
  // 사이드카는 이 표시가 없는 덤프(추가 레시피가 빠진 옛 버전)를 쓰지 않는다.
  out._askhowto = JSON.stringify({ version: 2, count: count });
  JsonIO.write("local/askhowto/recipes.json", out);
  console.info(`[ASKHOWTO] ${count} recipes -> local/askhowto/recipes.json (${Date.now() - started} ms)`);
});
