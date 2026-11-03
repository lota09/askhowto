console.info("[ASKHOWTO] askhowto.js loaded");

// 도우미 봇: /ask <질문>, /q <질문> 을 파일 큐로 사이드카(workspace/askhowto)에 넘기고 답변을 채팅으로 전달한다.
// KubeJS는 네트워크 접근이 막혀 있어서 LLM 호출은 사이드카가 하고, 여기서는 JsonIO로 파일만 주고받는다.
//   요청: local/askhowto/requests/<id>.json  (여기서 씀, 사이드카가 읽고 지움)
//   답변: local/askhowto/responses/<id>.json (사이드카가 씀, 여기서 읽고 지움)
//   상태: local/askhowto/status.json         (사이드카 하트비트)
//   평가: local/askhowto/feedback/<id>.json  (답 아래 [도움됨]/[도움안됨] 클릭, 사이드카가 logs/feedback.jsonl 로 모음)

const ASKHOWTO_COMMANDS = ["ask", "q"];
// /askmodel <선택>: 플레이어마다 답할 LLM 서비스를 고른다(플레이어 데이터에 저장돼 재시작해도 유지).
// default는 사이드카의 폴백 순서(NIM → Gemini → 로컬), 나머지는 그 서비스만 쓰고 실패해도 넘어가지 않는다.
const ASKHOWTO_MODEL_COMMAND = "askmodel";
const ASKHOWTO_MODEL_KEY = "askhowtoModel";
const ASKHOWTO_MODELS = {
  "default": "자동 (NIM → Gemini → 로컬 순서로, 실패하면 다음 서비스)",
  nim: "NVIDIA NIM (glm-5.3-flash)",
  gemini: "Google Gemini (3.1-flash-lite)",
  local: "로컬 Gemma (작은 모델)",
};
const ASKHOWTO_FEEDBACK_COMMAND = "askfeedback";
// /askforget: 내 대화 기억 지우기 (사이드카가 최근 대화 3개를 기억해 '그거 어떻게 만들어?' 같은 후속 질문에 쓴다)
const ASKHOWTO_FORGET_COMMAND = "askforget";
// /askteam on|off: 같은 팀(FTB Teams 파티, 없으면 스코어보드 팀) 팀원의 질문과 답을 같이 받을지. 기본은 받음.
const ASKHOWTO_TEAM_COMMAND = "askteam";
const ASKHOWTO_TEAM_OFF_KEY = "askhowtoTeamOff";
const ASKHOWTO_MAX_INVENTORY = 15;
const ASKHOWTO_FEEDBACK_TTL_MS = 600000; // 답을 받은 뒤 10분 동안만 평가할 수 있음
// 정해진 문구라 평가할 필요 없는 답
const ASKHOWTO_NO_FEEDBACK_LABELS = ["CHAT", "SENSITIVE", "ERROR"];
const ASKHOWTO_DIR = "local/askhowto";
const ASKHOWTO_COOLDOWN_MS = 15000;
const ASKHOWTO_TIMEOUT_MS = 180000; // 답변이 이보다 늦으면 포기
const ASKHOWTO_STALE_MS = 30000; // 하트비트가 이보다 오래되면 사이드카가 꺼진 것으로 봄
const ASKHOWTO_MAX_PENDING = 5; // 전체 대기열 상한 (LLM은 한 번에 하나씩만 처리)
const ASKHOWTO_MAX_LENGTH = 200;
const ASKHOWTO_CHECK_TICKS = 20;

// 리로드하면 비워진다. 그때 대기 중이던 답변은 사이드카가 10분 뒤 지운다.
let askhowtoPending = {}; // id -> { uuid, name, time }
let askhowtoLastAsk = {}; // uuid -> 마지막 질문 시각
let askhowtoVotable = {}; // id -> { uuid, time } 평가할 수 있는 답 (한 번만)

const askhowtoPrefix = () => Text.gold("[도우미] ");

const askhowtoTell = (player, message) => player.tell(askhowtoPrefix().append(message));

const askhowtoStatus = () => {
  try {
    return JsonIO.read(`${ASKHOWTO_DIR}/status.json`);
  } catch (err) {
    return null;
  }
};

const askhowtoModelOf = (player) => {
  let model = String(player.persistentData.getString(ASKHOWTO_MODEL_KEY) || "");
  return ASKHOWTO_MODELS[model] ? model : "default";
};

const askhowtoSetModel = (source, model) => {
  let player = source.player;
  if (!player) {
    source.sendFailure(Text.of("플레이어만 사용할 수 있습니다."));
    return 0;
  }
  if (model) {
    player.persistentData.putString(ASKHOWTO_MODEL_KEY, model);
    askhowtoTell(player, Text.gray(`답변 모델: ${ASKHOWTO_MODELS[model]}`));
    return 1;
  }
  let current = askhowtoModelOf(player);
  askhowtoTell(player, Text.gray(`지금 답변 모델: ${ASKHOWTO_MODELS[current]}`));
  Object.keys(ASKHOWTO_MODELS).forEach((name) => {
    let button = name == current ? Text.green(`[${name}]`) : Text.aqua(`[${name}]`);
    player.tell(
      Text.darkGray("  ")
        .append(button.clickRunCommand(`/${ASKHOWTO_MODEL_COMMAND} ${name}`).hover(Text.gray("이 모델로 바꾸기")))
        .append(Text.gray(` ${ASKHOWTO_MODELS[name]}`))
    );
  });
  return 1;
};

let $FTBTeamsAPI = null;
try {
  $FTBTeamsAPI = Java.loadClass("dev.ftb.mods.ftbteams.api.FTBTeamsAPI");
} catch (err) {
  console.warn(`[ASKHOWTO] FTB Teams API를 못 불러와 스코어보드 팀만 씁니다: ${err}`);
}

// 질문한 플레이어의 지금 상태 (사이드카 state.py가 한국어로 바꿔 '관련 있을 때만 참고'로 넣는다). 좌표는 보내지 않는다.
// 항목마다 따로 감싸서 하나가 실패해도 질문은 그대로 간다.
const askhowtoStack = (stack) => (!stack || stack.isEmpty() ? null : { id: String(stack.id), count: Number(stack.count) });

const askhowtoState = (player) => {
  let state = {};
  let level = player.level;
  let pos = player.blockPosition();
  const safe = (name, fn) => {
    try {
      fn();
    } catch (err) {
      console.warn(`[ASKHOWTO] state ${name}: ${err}`);
    }
  };
  safe("hands", () => {
    state.mainhand = askhowtoStack(player.mainHandItem);
    state.offhand = askhowtoStack(player.offHandItem);
  });
  safe("inventory", () => {
    let counts = {};
    let inv = player.inventory;
    for (let i = 0; i < inv.getContainerSize(); i++) {
      let stack = inv.getItem(i);
      if (stack.isEmpty()) continue;
      let id = String(stack.id);
      counts[id] = (counts[id] || 0) + Number(stack.count);
    }
    state.inventory = Object.keys(counts)
      .map((id) => ({ id: id, count: counts[id] }))
      .sort((a, b) => b.count - a.count)
      .slice(0, ASKHOWTO_MAX_INVENTORY);
  });
  safe("weather", () => {
    state.weather = level.isThundering() ? "thunder" : level.isRaining() ? "rain" : "clear";
  });
  safe("time", () => {
    // level.dayTime 속성은 undefined로 와서(NaN) 메서드를 직접 부른다(11/03).
    let t = Number(level.getDayTime());
    state.day = Math.floor(t / 24000) + 1;
    state.daytime = t % 24000;
  });
  // 바이옴 기준 계절 (열대 바이옴은 늘 여름 등, 서버 startup 스크립트의 함수)
  safe("season", () => {
    state.season = String(global.getSeasonFromBiome(level, pos));
  });
  safe("biome", () => {
    state.biome = String(level.getBiome(pos).unwrapKey().get().location());
  });
  safe("dimension", () => {
    state.dimension = String(level.dimension);
  });
  return state;
};

// 같은 팀의 접속 중인 다른 플레이어들. FTB Teams 파티를 먼저 보고(혼자인 개인 팀은 제외), 없으면 스코어보드 팀.
const askhowtoTeammates = (server, player) => {
  let uuid = String(player.uuid);
  let mates = [];
  try {
    if ($FTBTeamsAPI) {
      let team = $FTBTeamsAPI.api().getManager().getTeamForPlayer(player);
      if (team.isPresent() && team.get().isPartyTeam()) {
        team.get().getOnlineMembers().forEach((p) => {
          if (String(p.uuid) != uuid) mates.push(p);
        });
        return mates;
      }
    }
  } catch (err) {
    console.warn(`[ASKHOWTO] FTB Teams: ${err}`);
  }
  try {
    let team = player.team;
    if (team) {
      let name = String(team.name);
      server.players.forEach((p) => {
        if (String(p.uuid) != uuid && p.team && String(p.team.name) == name) mates.push(p);
      });
    }
  } catch (err) {
    console.warn(`[ASKHOWTO] scoreboard team: ${err}`);
  }
  return mates;
};

const askhowtoSetTeam = (source, on) => {
  let player = source.player;
  if (!player) return 0;
  if (on === null) {
    on = !player.persistentData.getBoolean(ASKHOWTO_TEAM_OFF_KEY);
    askhowtoTell(player, Text.gray(`팀원 질문·답 같이 받기: ${on ? "켜짐" : "꺼짐"} (/${ASKHOWTO_TEAM_COMMAND} on|off)`));
    return 1;
  }
  player.persistentData.putBoolean(ASKHOWTO_TEAM_OFF_KEY, !on);
  askhowtoTell(player, Text.gray(on ? "팀원의 질문과 답을 같이 받아요." : "이제 팀원의 질문과 답을 받지 않아요."));
  return 1;
};

const askhowtoUsage = (source) => {
  source.sendSystemMessage(
    askhowtoPrefix().append(Text.gray("선릿 밸리나 마인크래프트에 대해 물어보세요. 예: /ask 계절이 작물에 미치는 영향은?"))
  );
  return 1;
};

const askhowtoSubmit = (source, rawQuestion, action) => {
  let player = source.player;
  if (!player) {
    source.sendFailure(Text.of("플레이어만 사용할 수 있습니다."));
    return 0;
  }
  let forget = action == "forget";
  let question = forget ? "" : String(rawQuestion).trim();
  let uuid = String(player.uuid);
  let now = Date.now();

  if (question.length > ASKHOWTO_MAX_LENGTH) {
    askhowtoTell(player, Text.red(`질문은 ${ASKHOWTO_MAX_LENGTH}자 이내로 해주세요.`));
    return 0;
  }
  let waitMs = forget ? 0 : (askhowtoLastAsk[uuid] || 0) + ASKHOWTO_COOLDOWN_MS - now;
  if (waitMs > 0) {
    askhowtoTell(player, Text.red(`${Math.ceil(waitMs / 1000)}초 뒤에 다시 질문해 주세요.`));
    return 0;
  }
  let ids = Object.keys(askhowtoPending);
  if (ids.some((id) => askhowtoPending[id].uuid == uuid)) {
    askhowtoTell(player, Text.red("이전 질문의 답변을 기다리는 중이에요."));
    return 0;
  }
  if (ids.length >= ASKHOWTO_MAX_PENDING) {
    askhowtoTell(player, Text.red("질문이 밀려 있어요. 잠시 후 다시 시도해 주세요."));
    return 0;
  }
  let status = askhowtoStatus();
  if (!status || now - Number(status.time) > ASKHOWTO_STALE_MS) {
    askhowtoTell(player, Text.red("도우미가 꺼져 있어요. 관리자에게 알려주세요."));
    return 0;
  }

  let id = `${now}-${uuid.substring(0, 8)}`;
  try {
    let request = {
      id: id,
      player: String(player.username),
      uuid: uuid,
      question: question,
      time: now,
      model: askhowtoModelOf(player),
    };
    if (forget) request.action = "forget";
    else request.state = askhowtoState(player);
    JsonIO.write(`${ASKHOWTO_DIR}/requests/${id}.json`, request);
  } catch (err) {
    console.warn(`[ASKHOWTO] could not write request: ${err}`);
    askhowtoTell(player, Text.red("질문을 전달하지 못했어요. 관리자에게 알려주세요."));
    return 0;
  }
  askhowtoPending[id] = { uuid: uuid, name: String(player.username), time: now, question: question, forget: forget };
  if (forget) return 1;
  askhowtoLastAsk[uuid] = now;

  let ahead = ids.length + (String(status.busy) == "true" ? 1 : 0);
  askhowtoTell(player, Text.gray(`"${question}" 생각 중... (보통 10~20초${ahead > 0 ? `, 앞에 ${ahead}개 대기` : ""})`));
  return 1;
};

const askhowtoFeedback = (source, id, vote) => {
  let player = source.player;
  if (!player) return 0;
  id = String(id);
  vote = String(vote);
  let votable = askhowtoVotable[id];
  if (!votable || votable.uuid != String(player.uuid) || (vote != "good" && vote != "bad")) {
    askhowtoTell(player, Text.gray("이 답은 평가할 수 없어요."));
    return 0;
  }
  delete askhowtoVotable[id];
  try {
    JsonIO.write(`${ASKHOWTO_DIR}/feedback/${id}.json`, { id: id, player: String(player.username), vote: vote });
  } catch (err) {
    console.warn(`[ASKHOWTO] could not write feedback: ${err}`);
  }
  askhowtoTell(player, Text.gray(vote == "good" ? "고마워요!" : "알려줘서 고마워요. 틀린 답은 모아서 고칠게요."));
  return 1;
};

const askhowtoFeedbackButtons = (id) => {
  let cmd = `/${ASKHOWTO_FEEDBACK_COMMAND} ${id}`;
  return Text.darkGray("  ")
    .append(Text.green("[도움됨]").clickRunCommand(`${cmd} good`).hover(Text.gray("이 답이 도움이 됐어요")))
    .append(Text.darkGray(" "))
    .append(Text.red("[도움안됨]").clickRunCommand(`${cmd} bad`).hover(Text.gray("답이 틀렸거나 도움이 안 됐어요")));
};

ServerEvents.commandRegistry((event) => {
  const { commands: Commands, arguments: Arguments } = event;
  event.register(
    Commands.literal(ASKHOWTO_FEEDBACK_COMMAND).then(
      Commands.argument("id", Arguments.WORD.create(event)).then(
        Commands.argument("vote", Arguments.WORD.create(event)).executes((c) =>
          askhowtoFeedback(c.source, Arguments.WORD.getResult(c, "id"), Arguments.WORD.getResult(c, "vote"))
        )
      )
    )
  );
  // 선택지를 literal로 등록해서 탭 자동완성이 된다.
  let modelCommand = Commands.literal(ASKHOWTO_MODEL_COMMAND).executes((c) => askhowtoSetModel(c.source, null));
  Object.keys(ASKHOWTO_MODELS).forEach((name) => {
    modelCommand = modelCommand.then(Commands.literal(name).executes((c) => askhowtoSetModel(c.source, name)));
  });
  event.register(modelCommand);
  event.register(Commands.literal(ASKHOWTO_FORGET_COMMAND).executes((c) => askhowtoSubmit(c.source, "", "forget")));
  event.register(
    Commands.literal(ASKHOWTO_TEAM_COMMAND)
      .executes((c) => askhowtoSetTeam(c.source, null))
      .then(Commands.literal("on").executes((c) => askhowtoSetTeam(c.source, true)))
      .then(Commands.literal("off").executes((c) => askhowtoSetTeam(c.source, false)))
  );
  ASKHOWTO_COMMANDS.forEach((name) => {
    event.register(
      Commands.literal(name)
        .executes((c) => askhowtoUsage(c.source))
        .then(
          Commands.argument("question", Arguments.GREEDY_STRING.create(event)).executes((c) =>
            askhowtoSubmit(c.source, Arguments.GREEDY_STRING.getResult(c, "question"), "ask")
          )
        )
    );
  });
});

// 답에 나온 아이템 이름에 마우스를 올리면 실제 아이템 툴팁이 뜨게 한다(show_item). 실패하면 글자만 보여준다.
let $HoverEvent = null;
let $HoverAction = null;
let $ItemStackInfo = null;
try {
  $HoverEvent = Java.loadClass("net.minecraft.network.chat.HoverEvent");
  $HoverAction = Java.loadClass("net.minecraft.network.chat.HoverEvent$Action");
  $ItemStackInfo = Java.loadClass("net.minecraft.network.chat.HoverEvent$ItemStackInfo");
} catch (err) {
  console.warn(`[ASKHOWTO] 아이템 툴팁을 못 씁니다: ${err}`);
}

const askhowtoItemText = (name, id) => {
  let text = Text.aqua(name);
  if (!$HoverEvent) return text;
  try {
    let event = new $HoverEvent($HoverAction.SHOW_ITEM, new $ItemStackInfo(Item.of(id)));
    text.setStyle(text.getStyle().withHoverEvent(event));
  } catch (err) {
    // 없는 아이템 ID 등: 글자만
  }
  return text;
};

// 답 한 줄을 글자 조각과 아이템 이름 조각으로 나눠 잇는다. items: [{name, id}] (긴 이름부터 겹치지 않게)
const askhowtoRichLine = (line, items) => {
  let marks = [];
  items.forEach((item) => {
    let from = 0;
    while (true) {
      let at = line.indexOf(item.name, from);
      if (at < 0) break;
      let end = at + item.name.length;
      if (!marks.some((m) => at < m.end && m.start < end)) marks.push({ start: at, end: end, id: item.id });
      from = end;
    }
  });
  marks.sort((a, b) => a.start - b.start);
  let out = Text.white("");
  let last = 0;
  marks.forEach((m) => {
    if (m.start > last) out.append(Text.white(line.substring(last, m.start)));
    out.append(askhowtoItemText(line.substring(m.start, m.end), m.id));
    last = m.end;
  });
  if (last < line.length) out.append(Text.white(line.substring(last)));
  return out;
};

const askhowtoParseItems = (text) =>
  (text ? String(text) : "")
    .split("\n")
    .map((row) => row.split("\t"))
    .filter((cols) => cols.length == 2 && cols[0].length > 0)
    .map((cols) => ({ name: cols[0], id: cols[1] }))
    .sort((a, b) => b.name.length - a.name.length);

// 실패 안내(줄바꿈으로 이은 문자열) 중 아직 안 보여준 것만 보여준다. pending.shown에 보여준 개수를 둔다.
const askhowtoShowNotices = (player, pending, text) => {
  let lines = text.split("\n").filter((line) => line.length > 0);
  for (let i = pending.shown || 0; i < lines.length; i++) player.tell(Text.darkGray(`(${lines[i]})`));
  pending.shown = Math.max(pending.shown || 0, lines.length);
};

// 답 하나를 한 플레이어에게 보낸다. header가 있으면(팀원에게 보낼 때) 누구의 질문인지 먼저 보여준다.
// 질문한 사람은 실패 안내를 그때그때 받으므로(askhowtoShowNotices) 한 줄 요약(notice)은 팀원에게만 보낸다.
const askhowtoDeliver = (target, response, header) => {
  if (header) askhowtoTell(target, Text.gray(header));
  let items = askhowtoParseItems(response.items);
  // Java List 변환에 기대지 않도록 답변·출처는 줄바꿈으로 이은 문자열 하나로 받는다.
  String(response.text)
    .split("\n")
    .forEach((line, i) => {
      let rich = askhowtoRichLine(line, items);
      if (i == 0 && !header) askhowtoTell(target, rich);
      else target.tell(rich);
    });
  let sources = response.sources ? String(response.sources) : "";
  if (sources) target.tell(Text.darkGray(`참고: ${sources.split("\n").join(" · ")}`));
  // 넣어 준 자료 중 답에 쓰이지 않은 것 (빠뜨린 내용이 있는지 보이게)
  let unused = response.unused ? String(response.unused) : "";
  if (unused) target.tell(Text.darkGray(`(답에 안 쓴 관련 자료: ${unused.split("\n").join(", ")})`));
  // 서비스가 실패해 다음 서비스가 답했으면 알린다 (예: NVIDIA NIM 응답 실패(시간 초과) → Gemini로 답했어요)
  let notice = response.notice ? String(response.notice) : "";
  if (notice && header) target.tell(Text.darkGray(`(${notice})`));
};

ServerEvents.tick((e) => {
  const { server } = e;
  if (server.getTickCount() % ASKHOWTO_CHECK_TICKS) return;
  let ids = Object.keys(askhowtoPending);
  if (ids.length == 0) return;

  let now = Date.now();
  ids.forEach((id) => {
    let pending = askhowtoPending[id];
    let player = server.getPlayer(pending.name);
    let path = `${ASKHOWTO_DIR}/responses/${id}.json`;
    let progressPath = `${ASKHOWTO_DIR}/progress/${id}.json`;
    let response = null;
    try {
      response = JsonIO.read(path);
    } catch (err) {
      // 사이드카가 쓰는 도중이면 다음 확인 때 다시 읽는다.
    }
    // 답을 기다리는 동안 서비스가 실패할 때마다 바로 알린다 (예: NVIDIA NIM 응답 실패(사용 한도 초과) → Gemini로 다시 시도해요).
    if (!response && player) {
      let progress = null;
      try {
        progress = JsonIO.read(progressPath);
      } catch (err) {
        // 쓰는 도중이면 다음 확인 때
      }
      if (progress && progress.notices) askhowtoShowNotices(player, pending, String(progress.notices));
    }
    if (response) {
      delete askhowtoPending[id];
      JsonIO.write(path, null);
      try {
        JsonIO.write(progressPath, null);
      } catch (err) {
        // 진행 파일이 없으면 지울 것도 없다
      }
      if (!player) return;
      askhowtoDeliver(player, response, null);
      // 진행 파일로 아직 못 보여준 실패 안내(답과 거의 동시에 실패·성공한 경우)를 마저 보여준다.
      if (response.notices) askhowtoShowNotices(player, pending, String(response.notices));
      // 같은 팀 팀원에게도 질문과 답을 보낸다(평가 버튼은 질문한 사람만). 기억 지우기 같은 명령 결과는 보내지 않는다.
      if (!pending.forget) {
        askhowtoTeammates(server, player).forEach((mate) => {
          if (mate.persistentData.getBoolean(ASKHOWTO_TEAM_OFF_KEY)) return;
          askhowtoDeliver(mate, response, `${pending.name}님의 질문: ${pending.question}`);
        });
      }
      if (ASKHOWTO_NO_FEEDBACK_LABELS.indexOf(String(response.label)) < 0 && !pending.forget) {
        Object.keys(askhowtoVotable).forEach((old) => {
          if (now - askhowtoVotable[old].time > ASKHOWTO_FEEDBACK_TTL_MS) delete askhowtoVotable[old];
        });
        askhowtoVotable[id] = { uuid: pending.uuid, time: now };
        player.tell(askhowtoFeedbackButtons(id));
      }
      return;
    }
    if (now - pending.time > ASKHOWTO_TIMEOUT_MS) {
      delete askhowtoPending[id];
      if (player) askhowtoTell(player, Text.red("답변이 너무 오래 걸려서 취소했어요. 다시 질문해 주세요."));
    }
  });
});
