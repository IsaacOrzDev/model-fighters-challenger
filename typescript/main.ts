/** Connection and match management. Edit strategy.ts to change how the fighter plays. */
import { readFileSync, writeFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { parseArgs } from "node:util";
import { pathToFileURL } from "node:url";

import type { Slot, State } from "./fighter_actions.ts";
import { CpuPolicy, free, downed } from "./strategy.ts";
export { CpuPolicy } from "./strategy.ts";
export type { State } from "./fighter_actions.ts";

const log = (message: string) => console.log(new Date().toISOString(), message);

export function loadSettings(text: string, override?: string) {
  let settings: State | undefined;
  // Locate balanced JSON objects within copied instructions, respecting strings.
  for (let start = 0; start < text.length && !settings; start++) {
    if (text[start] !== "{") continue;
    let depth = 0,
      quoted = false,
      escaped = false;
    for (let end = start; end < text.length; end++) {
      const c = text[end];
      if (quoted) {
        if (escaped) escaped = false;
        else if (c === "\\") escaped = true;
        else if (c === '"') quoted = false;
      } else if (c === '"') quoted = true;
      else if (c === "{") depth++;
      else if (c === "}" && --depth === 0) {
        try {
          const value = JSON.parse(text.slice(start, end + 1));
          if (value.sessionId && value.headers) settings = value;
        } catch {
          /* next object */
        }
        break;
      }
    }
  }
  if (!settings)
    throw new Error("Use copied settings JSON or full agent instructions.");
  const session = settings.sessionId;
  const token = String(settings.headers.Authorization ?? "").replace(
    /^Bearer /,
    "",
  );
  if (
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      session,
    ) ||
    !/^[0-9a-f]{64}$/.test(token)
  )
    throw new Error("Invalid session ID or bearer token.");
  const url = new URL(
    override ??
      settings.websocketUrl ??
      String(settings.url).replace(/^http/, "ws") + "/ws",
  );
  if (!["ws:", "wss:"].includes(url.protocol) || url.username || url.password)
    throw new Error("Supply a WebSocket URL without credentials.");
  url.searchParams.set("session", session);
  url.hash = "";
  const slot = settings.player as Slot | undefined;
  if (slot && !["player", "opponent"].includes(slot))
    throw new Error("Invalid assigned player slot.");
  return { url: url.toString(), session, token, slot };
}

export function gameplayReady(message: State, slot: Slot) {
  const s = message.state ?? {},
    o = s.online ?? {};
  if (s.gameplayMode === "online" || s.remoteStart?.mode === "online") {
    if (
      o.status !== "matched" ||
      !o.confirmed ||
      o.localPlayer !== slot ||
      !o.started
    )
      return false;
  }
  return (
    !!message.acceptingCommands &&
    s.controllers?.[slot] === "api" &&
    !(s.countdown > 0) &&
    !s.openingDialogue &&
    !s.ready &&
    !s.paused &&
    !s.replay &&
    !s.matchOver &&
    (s.result ?? "playing") === "playing"
  );
}

export class ExecutionTracker {
  pending?: {
    requestId: string;
    commandId?: string;
    kind: string;
    sent: number;
    queued?: number;
  };
  slot: Slot;
  timing: boolean;
  constructor(slot: Slot, timing = false) {
    this.slot = slot;
    this.timing = timing;
  }
  begin(kind: string) {
    if (this.pending) throw new Error("A request is already outstanding.");
    const requestId = randomUUID();
    this.pending = { requestId, kind, sent: performance.now() };
    return requestId;
  }
  acknowledge(m: State) {
    const p = this.pending;
    if (!p || m.requestId !== p.requestId) return;
    if (m.type === "error") {
      if ([409, 429].includes(m.status)) {
        this.pending = undefined;
        return;
      }
      throw new Error("Request rejected; check settings and payload.");
    }
    p.commandId = m.commandId;
    p.queued = performance.now() - p.sent;
  }
  observe(s: State) {
    const p = this.pending;
    if (!p) return true;
    const starting = p.kind !== "input",
      e = starting ? s.remoteStart?.receipt : s.commands?.[this.slot];
    if (
      p.commandId &&
      (s.receipts ?? []).some(
        (r: State) => r.id === p.commandId && r.status === "rejected",
      ) &&
      !starting
    ) {
      this.pending = undefined;
      return false;
    }
    if (
      p.commandId &&
      e?.id === p.commandId &&
      (starting
        ? ["started", "rejected"]
        : ["completed", "cancelled"]
      ).includes(e.status)
    ) {
      if (this.timing)
        log(
          `TIMING ${p.kind}: queued ${p.queued?.toFixed(0)} ms; confirmed ${(performance.now() - p.sent).toFixed(0)} ms (${e.status})`,
        );
      this.pending = undefined;
      if (
        e.status === "rejected" &&
        !(
          s.ready === false &&
          !s.matchOver &&
          !s.replay &&
          s.controllers?.[this.slot] === "api"
        )
      )
        throw new Error("Configured start was rejected.");
      return true;
    }
    if (performance.now() - p.sent > (p.kind === "start" ? 300000 : 30000))
      throw new Error(
        "Command outcome unconfirmed; stopping without retrying.",
      );
    return false;
  }
}

export type Options = {
  slot: Slot;
  start: boolean;
  matches: number;
  maxSeconds: number;
  timing: boolean;
  stateFile?: string;
};
export class Controller {
  policy: CpuPolicy;
  tracker: ExecutionTracker;
  slot: Slot;
  start: boolean;
  completed = 0;
  awaitingRematch = false;
  endedAt?: number;
  roomWait?: number;
  onlineMatch?: string;
  lastStatus = -Infinity;
  lastAction?: string;
  options: Options;
  send: (m: State) => void;
  constructor(options: Options, send: (m: State) => void) {
    this.options = options;
    this.send = send;
    this.slot = options.slot;
    this.start = options.start;
    this.policy = new CpuPolicy(this.slot);
    this.tracker = new ExecutionTracker(this.slot, options.timing);
  }
  request(kind: string, extra: State = {}) {
    this.send({ type: kind, requestId: this.tracker.begin(kind), ...extra });
  }
  handle(m: State): boolean {
    if (m.type === "error" && !m.requestId)
      throw new Error("Session authorization or observations unavailable.");
    if (["queued", "error"].includes(m.type)) {
      this.tracker.acknowledge(m);
      return false;
    }
    if (m.type !== "state") return false;
    const s = m.state ?? {},
      now = performance.now();
    if (this.options.stateFile)
      writeFileSync(this.options.stateFile, JSON.stringify(m));
    if (m.browserConnected === false || (m.shouldStop && !s.matchOver)) {
      log("Connection ended; result unknown.");
      return true;
    }
    if (Date.now() - (m.updatedAt ?? 0) > 3000) return false;
    if (now - this.lastStatus >= 5000) {
      this.lastStatus = now;
      log(
        "STATE " +
          JSON.stringify({
            slot: this.slot,
            round: s.roundNumber,
            health: s[this.slot]?.health,
            score: [s.playerWins, s.opponentWins],
          }),
      );
    }
    const o = s.online ?? {},
      online = s.gameplayMode === "online" || s.remoteStart?.mode === "online";
    if (online) {
      if (o.status === "disconnected")
        throw new Error("Online room disconnected.");
      if (o.status === "finished") {
        log(
          "FINAL ONLINE " +
            JSON.stringify({
              outcome: o.outcome,
              streak: o.streak,
              runComplete: o.runComplete,
              score: [s.playerWins, s.opponentWins],
            }),
        );
        return true;
      }
      if (o.confirmed && !s.matchOver) {
        this.roomWait = undefined;
        if (
          ["player", "opponent"].includes(o.localPlayer) &&
          o.localPlayer !== this.slot
        ) {
          this.slot = o.localPlayer;
          this.policy = new CpuPolicy(this.slot);
          this.tracker.slot = this.slot;
        }
        if (o.matchId && o.matchId !== this.onlineMatch) {
          this.onlineMatch = o.matchId;
          this.policy = new CpuPolicy(this.slot);
          this.tracker = new ExecutionTracker(this.slot, this.options.timing);
          this.endedAt = undefined;
          this.start = false;
        }
      } else if (
        ["joining", "waiting", "matched"].includes(o.status) ||
        s.matchOver
      ) {
        this.roomWait ??= now;
        if (now - this.roomWait >= 1800000)
          throw new Error("Online room assignment timed out after 30 minutes.");
      }
    }
    const action = gameplayReady(m, this.slot)
      ? s[this.slot]?.action
      : undefined;
    if (action !== this.lastAction) {
      this.lastAction = action;
      if (
        action &&
        !free.has(action) &&
        !downed.has(action) &&
        !["hurt", "getUp", "crumple"].includes(action)
      )
        log("Observed assigned fighter action: " + action);
    }
    const goal = m.goals?.[this.slot] ?? {};
    if (s.matchOver) {
      if (online) {
        if (goal.completed && this.endedAt === undefined) {
          this.endedAt = now;
          log(
            "FINAL " +
              JSON.stringify({ goal, score: [s.playerWins, s.opponentWins] }),
          );
          if (goal.achieved === false) return true;
        }
        return false;
      }
      if (this.start) {
        if (!goal.completed)
          throw new Error("Previous match result unverified.");
        this.endedAt ??= now;
        if (!this.tracker.pending && m.canRematch) {
          this.request("rematch");
          this.start = false;
          this.awaitingRematch = true;
        }
      } else {
        if (this.endedAt === undefined) {
          this.endedAt = now;
          this.completed++;
          log(
            "FINAL " +
              JSON.stringify({
                match: this.completed,
                goal,
                result: s.result,
                score: [s.playerWins, s.opponentWins],
              }),
          );
          if (this.completed >= this.options.matches) return true;
          if (!goal.completed)
            throw new Error("Match result unverified; no rematch.");
          this.tracker = new ExecutionTracker(this.slot, this.options.timing);
        }
        if (!this.awaitingRematch && m.canRematch) {
          this.request("rematch");
          this.awaitingRematch = true;
        } else if (this.awaitingRematch) this.tracker.observe(s);
      }
      if (now - this.endedAt! > 15000)
        throw new Error("Rematch unavailable or unconfirmed; no retry.");
      return false;
    }
    if (this.awaitingRematch) {
      if (!this.tracker.observe(s)) return false;
      this.awaitingRematch = false;
      this.endedAt = undefined;
      this.policy = new CpuPolicy(this.slot);
    }
    if (this.start && s.ready === false && s.controllers?.[this.slot] === "api")
      this.start = false;
    if (!this.tracker.observe(s)) return false;
    if (this.start && m.canStart) {
      this.request("start");
      this.start = false;
      if (online) this.roomWait = now;
      return false;
    }
    if (!gameplayReady(m, this.slot)) return false;
    if (s[this.slot]?.mode !== "modern")
      throw new Error("This policy requires Modern controls.");
    const d = this.policy.choose(s);
    if (d) {
      this.request("input", { player: this.slot, steps: d.steps });
      log("Requested " + d.name);
    }
    return false;
  }
}

async function main() {
  const { values, positionals } = parseArgs({
    allowPositionals: true,
    options: {
      help: { type: "boolean", short: "h" },
      player: { type: "string" },
      url: { type: "string" },
      start: { type: "boolean" },
      matches: { type: "string", default: "1" },
      "max-seconds": { type: "string", default: "0" },
      timing: { type: "boolean" },
      "state-file": { type: "string" },
    },
  });
  if (values.help) {
    console.log(
      "Usage: node main.ts SETTINGS [--start] [--player player|opponent] [--url ws://...] [--matches N] [--max-seconds N] [--timing] [--state-file FILE]",
    );
    return;
  }
  if (positionals.length !== 1)
    throw new Error("Supply one private settings file.");
  let settings: ReturnType<typeof loadSettings>;
  try {
    settings = loadSettings(readFileSync(positionals[0], "utf8"), values.url);
  } catch {
    throw new Error(
      "Cannot read valid settings; copy fresh settings into a private file.",
    );
  }
  const slot = settings.slot ?? values.player ?? "player";
  if (
    !["player", "opponent"].includes(slot) ||
    (settings.slot && values.player && values.player !== settings.slot)
  )
    throw new Error("Player conflicts with the assigned fighter.");
  const matches = Number(values.matches),
    maxSeconds = Number(values["max-seconds"]);
  if (
    !Number.isInteger(matches) ||
    matches < 1 ||
    !Number.isFinite(maxSeconds) ||
    maxSeconds < 0
  )
    throw new Error("Invalid match count or time limit.");
  await new Promise<void>((resolve, reject) => {
    const socket = new WebSocket(settings.url);
    let done = false,
      lastState = performance.now();
    const began = lastState;
    const finish = (error?: Error) => {
      if (done) return;
      done = true;
      clearInterval(timer);
      socket.close();
      process.off("SIGINT", stop);
      error ? reject(error) : resolve();
    };
    const stop = () => {
      log("Controller stopped by user.");
      finish();
    };
    const controller = new Controller(
      {
        slot: slot as Slot,
        start: !!values.start,
        matches,
        maxSeconds,
        timing: !!values.timing,
        stateFile: values["state-file"],
      },
      (m) => socket.send(JSON.stringify(m)),
    );
    const timer = setInterval(() => {
      if (maxSeconds && performance.now() - began >= maxSeconds * 1000) {
        log("Time limit reached; result unknown.");
        finish();
        return;
      }
      if (performance.now() - lastState > 5000) {
        finish(new Error("Observation stream timed out."));
        return;
      }
      if (socket.readyState === WebSocket.OPEN)
        socket.send(JSON.stringify({ type: "ping" }));
    }, 1000);
    process.on("SIGINT", stop);
    socket.addEventListener("open", () =>
      socket.send(
        JSON.stringify({
          type: "hello",
          role: "controller",
          sessionId: settings.session,
          token: settings.token,
        }),
      ),
    );
    socket.addEventListener("message", (event) => {
      try {
        const m = JSON.parse(String(event.data));
        if (m.type === "state" && Date.now() - (m.updatedAt ?? 0) <= 3000)
          lastState = performance.now();
        if (controller.handle(m)) finish();
      } catch {
        finish(
          new Error(
            "Controller stopped: invalid observation or command outcome unavailable; no automatic retries.",
          ),
        );
      }
    });
    socket.addEventListener("error", () =>
      finish(new Error("WebSocket connection failed; result unknown.")),
    );
    socket.addEventListener("close", () => {
      if (!done) finish(new Error("WebSocket disconnected; result unknown."));
    });
  });
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
