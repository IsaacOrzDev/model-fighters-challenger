import assert from "node:assert/strict";
import { test } from "node:test";
import {
  Controller,
  CpuPolicy,
  ExecutionTracker,
  gameplayReady,
  loadSettings,
} from "../main.ts";
function state() {
  const fighter = {
    x: 0,
    y: 0,
    velocityY: 0,
    facing: 1,
    health: 100,
    action: "idle",
    cooldown: 0,
    stun: 0,
    burnout: false,
    drive: 6,
    mode: "modern",
    guard: { guarding: "none" },
  };
  return {
    player: { ...fighter },
    opponent: { ...fighter, x: 3 },
    roundNumber: 1,
    controllers: { player: "api" },
    result: "playing",
    ready: false,
  };
}
function message() {
  return {
    type: "state",
    updatedAt: Date.now(),
    acceptingCommands: true,
    state: state(),
  };
}
function controller(start = false, matches = 1) {
  const sent: any[] = [];
  return {
    sent,
    c: new Controller(
      { slot: "player", start, matches, maxSeconds: 0, timing: false },
      (m) => sent.push(m),
    ),
  };
}
test("settings accept copied instructions without putting token in URL", () => {
  const token = "a".repeat(64);
  const settings = loadSettings(
    "Instructions\n" +
      JSON.stringify({
        url: "https://game.example/api/player",
        sessionId: "11111111-1111-1111-1111-111111111111",
        player: "opponent",
        headers: { Authorization: `Bearer ${token}` },
      }),
  );
  assert.equal(settings.slot, "opponent");
  assert.ok(
    settings.url.startsWith("wss://game.example/api/player/ws?session="),
  );
  assert.ok(!settings.url.includes(token));
});
test("invalid credentials and embedded URL credentials are rejected", () => {
  const data = {
    websocketUrl: "wss://user:secret@game.example/ws",
    sessionId: "11111111-1111-1111-1111-111111111111",
    headers: { Authorization: "Bearer " + "a".repeat(64) },
  };
  assert.throws(() => loadSettings(JSON.stringify(data)));
  data.websocketUrl = "wss://game.example/ws";
  data.headers.Authorization = "Bearer invalid";
  assert.throws(() => loadSettings(JSON.stringify(data)));
});
test("countdown and pauses prevent policy input", () => {
  const { c, sent } = controller();
  const m: any = message();
  m.state.countdown = 3;
  c.handle(m);
  assert.equal(sent.length, 0);
  m.state.countdown = 0;
  m.state.paused = true;
  c.handle(m);
  assert.equal(sent.length, 0);
  m.state.paused = false;
  c.handle(m);
  assert.equal(sent.length, 1);
});
test("online room must be confirmed and started", () => {
  const m: any = message();
  m.state.gameplayMode = "online";
  assert.equal(gameplayReady(m, "player"), false);
  m.state.online = {
    status: "matched",
    confirmed: true,
    localPlayer: "player",
    started: true,
  };
  assert.equal(gameplayReady(m, "player"), true);
});
test("queued acknowledgment does not authorize another input", () => {
  const { c, sent } = controller();
  const m: any = message();
  c.handle(m);
  c.handle({ type: "queued", requestId: sent[0].requestId, commandId: "cmd" });
  c.handle(m);
  assert.equal(sent.length, 1);
  m.state.commands = { player: { id: "cmd", status: "completed" } };
  c.handle(m);
  assert.equal(sent.length, 2);
});
test("stale observations never send gameplay input", () => {
  const { c, sent } = controller();
  const m = message();
  m.updatedAt -= 5000;
  c.handle(m);
  assert.equal(sent.length, 0);
});
test("policy controls Player 2 and releases every sequence", () => {
  const s = state();
  s.opponent.facing = -1;
  const decision = new CpuPolicy("opponent").choose(s)!;
  assert.deepEqual(decision.steps[0].buttons, ["left"]);
  assert.deepEqual(decision.steps.at(-1)?.buttons, []);
});
test("start sends once and waits for receipt", () => {
  const { c, sent } = controller(true);
  const m: any = message();
  m.state.ready = true;
  m.canStart = true;
  c.handle(m);
  c.handle(m);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].type, "start");
});
test("one local match stops without an unsolicited rematch", () => {
  const { c, sent } = controller();
  const m: any = message();
  m.state.matchOver = true;
  m.goals = { player: { completed: true, achieved: true } };
  m.canRematch = true;
  assert.equal(c.handle(m), true);
  assert.equal(sent.length, 0);
});
test("explicit additional matches wait for verified goals", () => {
  const { c, sent } = controller(false, 2);
  const m: any = message();
  m.state.matchOver = true;
  m.canRematch = true;
  assert.throws(() => c.handle(m), /unverified/);
  assert.equal(sent.length, 0);
});
test("online win continues queue and loss stops", () => {
  const { c, sent } = controller();
  const m: any = message();
  m.state.gameplayMode = "online";
  m.state.matchOver = true;
  m.state.online = { status: "matched", confirmed: true };
  m.goals = { player: { completed: true, achieved: true } };
  assert.equal(c.handle(m), false);
  assert.equal(sent.length, 0);
  m.state.matchOver = false;
  m.state.online = {
    status: "matched",
    confirmed: true,
    matchId: "next",
    localPlayer: "player",
    started: true,
  };
  c.handle(m);
  m.state.matchOver = true;
  m.goals.player.achieved = false;
  assert.equal(c.handle(m), true);
});
test("tracker never allows concurrent or timed-out requests", () => {
  const t = new ExecutionTracker("player");
  t.begin("input");
  assert.throws(() => t.begin("input"));
  t.pending!.sent -= 31000;
  assert.throws(() => t.observe(state()), /unconfirmed/);
});
