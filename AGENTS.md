# Agent instructions

## Purpose and controller workflow

Use `main.py`, `main.ts`, or `main.rs` to control the assigned Model fighter and
win the entire match. For an authorized live attempt, run, monitor and adapt the
controller through all rounds until the match ends or the user stops you. In Online mode,
continue the queue run across wins until a loss or queue completion. Report
verified outcomes; writing a script or demonstrating an attack is insufficient.

Read `README.md` and these instructions before adapting `CpuPolicy.choose(state)`
in `strategy.py`, `strategy.ts`, or `strategy.rs`. Use the named `FighterActions`
methods instead of writing raw button sequences in strategy rules. The action
methods build plans; the controller sends them only after readiness checks and
waits for execution receipts. Keep connection and match handling in the controller.
The policy supports either assigned slot with Modern controls. Verify the
fighter's moves and use `--player opponent` for Player 2; do not silently apply
tactics to an unsupported control mode. Preserve observation freshness,
authentication, one outstanding request, execution receipts and whole-match
verification. Use the copied live settings rather than assuming an endpoint. The `player`
field assigns Player 1 (`player`) or Player 2 (`opponent`); the script selects it
automatically. Do not override it to control the other fighter.
Keep the implementation in this controller repository.

## Local setup and execution

There is no need to inspect or control Chrome or any other browser. Run
the selected controller (normally through `make play`) to start an authorized
configured game and inspect its status through the supplied game API. Use the
script's observations, execution receipts, logs and whole-match goals to monitor
progress and verify the final outcome. Keep the game client open; do not use
browser automation to inspect status, start the game or influence gameplay.

Start the game locally with `npm run dev:ws` in the game repository, or use the
Cloudflare deployment. Select **Script** input and Modern controls. **Full script
control** is the default waiting mode. Normal CPU takes over local fighters on
disconnect. Save **Copy settings** or **Copy Agent instructions** privately as
`secrets/player-settings.json`; this directory is ignored.

```sh
make setup
make play
```

`make play` runs the selected controller with `--start` and the default
settings file. `make run` and `make play-ws` are aliases. Use
`SETTINGS=player-instructions.txt` to override the file and
`ARGS="--player opponent --max-seconds 30"` for the other slot or a bounded test.

`--start` starts the configured fight when allowed, or requests one rematch when
the previous local VS/Training match has finished. Rerun `make play` with the
same valid settings to play again. Joining an active match continues it without
restarting it. `ARGS="--matches 3"` explicitly requests three complete matches.
The previous finished match does not count toward the new run's match count.
Rematches preserve settings and require a verified whole-match result. Arena
and online rematches are unsupported. Without `--start`, wait for a manually
started fight. Never pause/resume a game, change settings, restart an active
fight, control the rival or use the browser to influence gameplay.

## After the match and rematches

After the entire local match finishes, report the verified outcome and final score,
then ask the user: "Do you want to rematch?" Wait for their answer before starting
another match, unless they already explicitly requested additional matches.
A completed round alone is not the end of the match. Online wins continue the
existing queue run automatically; do not ask for a local rematch between online
challengers. Report the run when it ends in a loss or queue completion.

If the user agrees, rerun the start command with the same valid settings and
assigned slot. For the default settings file, run:

```sh
make play
```

This runs the selected controller with the private settings and `--start` again. When the previous local VS/Training
match is finished, `--start` automatically requests a rematch. Preserve any
settings-file override and `--player` option from the previous run. Keep the
game tab open, monitor the new match through all rounds, report its verified
outcome, and ask again after it finishes. If the user declines, stop.
If the session was revoked or replaced, obtain fresh copied settings before
running again. Do not treat a disconnect as a completed match.

Keep the game tab open. Fresh settings are required after a session is revoked
or replaced. Settings and full instructions both contain private credentials;
never print tokens, commit credentials, or dump authenticated headers into logs.

## WebSocket protocol and execution boundaries

Connect to the copied `websocketUrl` with the session ID in the `session` query
parameter. Send JSON `hello` with `role: controller`, `sessionId` and `token`
(the bearer value without its prefix). Never put a token in the URL. `welcome`
confirms authentication. Send `ping` at least once per second for presence.
The browser targets observations every 50 ms with bounded backpressure; there
is no HTTP polling fallback. Inputs push immediately while persisted delivery
is deduplicated. Use `ARGS="--timing"` to log submission-to-queue and execution
confirmation durations when investigating remote slowness. These timings include
network, processing and simulation; never claim they measure network ping alone.
For Node development the socket gateway uses internal loopback requests to the
session store. Cloudflare routes the socket through the deployed Worker.

Wait for `acceptingCommands=true`, a fresh observation and
`state.controllers[assigned_slot] == "api"` before input. The internal device key
remains `api`, while the game labels it Script. Stop attacking through countdowns,
round transitions and pauses. Control only the assigned fighter.

Send `input` with a unique `requestId`, the assigned `player`, and ordered `steps`.
Each step replaces held buttons. Include releases between taps and finish with
an empty release. Limits are 30 steps, 1–120 frames per step, and 600 total frames
per sequence at 60 Hz. `queued` maps `requestId` to `commandId`; it is not execution
confirmation. Keep one request outstanding. Observe browser receipts and
`state.commands[assigned_slot]` until completed, cancelled or rejected.

Send `start` only when `canStart=true` and live play is authorized. Track
`state.remoteStart.receipt` until started or rejected, including model loading
and countdown. Send `rematch` with a unique `requestId` only when `canRematch=true`
and the user authorized that rematch or requested additional matches. Track its
queued ID through the same start receipt. Never resend an uncertain request.

Wait for live readiness rather than a fixed delay after `start`. In online mode,
allow up to 30 minutes for `state.online.confirmed=true`; use its `localPlayer`
assignment (host is `player`, guest is `opponent`) and wait for `started=true`.
Keep receiving fresh observations and sending heartbeats while queued. A queue
join receipt alone does not confirm a room. Never send gameplay inputs before
room confirmation. While joining, queued, preparing or counting down, do not
choose, assign or buffer gameplay actions for later. Only connection heartbeats,
observations and the authorized queue/start workflow are active. In every mode, including VS and rematches, wait until
`state.countdown` is null/zero, `openingDialogue=false`, and
`acceptingCommands=true`. Pauses and round transitions also suspend inputs.

Stop on authorization errors, missing/stale observations, socket disconnect,
30 seconds without a confirmed input or rematch outcome, or the user's stop
request. Only a sent `start` request allows up to 300 seconds for confirmation;
connection and observation timeouts remain unchanged.
Stop after one local match unless additional matches were explicitly requested.
Online mode is a queue run: after a verified win keep the same socket open for
the next challenger, allowing another 30 minutes for room assignment and waiting
for each new countdown. Follow the newly assigned fighter on each match. Never
send local `rematch` or rejoin automatically. Stop on a verified loss or a
`state.online.status=finished` result (queue complete), and report the verified
win streak and final outcome.
Controller disconnect clears held input and drains queued commands. A win
requires `goals[assigned_slot].completed=true` and `achieved=true`; report the
observed outcome and score, or unknown if disconnected. A round win does not
complete the whole-match goal.

## Tactics and evidence

Observe positions, facing, health, actions/timing, cooldown, stun, guard, Drive/burnout, both Super meters and projectiles before deciding. Close distance when needed, attack when an effective hit is available, vary ineffective pressure, throw only eligible grounded targets, and defend against immediate threats. Spend Super on supported openings or reliable finishing hits. Do not stall in defense or repeat attacks that deal no damage. Keep claims about hits and outcomes tied to observed state and receipts.

## Language folders and verification

The root Makefile selects `LANGUAGE=python` (default), `LANGUAGE=typescript`,
or `LANGUAGE=rust`. Each native implementation lives in its own language folder:
`python`, `typescript`, or `rust`. Keep language dependencies, lockfiles and
unit tests with their implementation, in its `test` folder. Keep the Makefile, shared instructions,
README and private secrets directory at the repository root.

Choose the implementation matching the requested language. The templates are
`main.py` for Python, `main.ts` for TypeScript,
and `main.rs` for Rust. Review the selected policy before adapting it; preserve
the same protocol and execution boundaries in every implementation.

```sh
make setup LANGUAGE=typescript
make play LANGUAGE=typescript
make setup LANGUAGE=rust
make play LANGUAGE=rust
```

TypeScript uses Node.js 22.18 or newer, native WebSocket and TypeScript stripping,
with npm managing its development dependencies. Rust uses Cargo with a committed
lockfile and Rustls for secure sockets. Use `make setup-all` to prepare all three.
`make check` checks all languages; `make check-python`, `make check-typescript`,
and `make check-rust` check one implementation offline. Preserve `LANGUAGE`,
`SETTINGS` and `ARGS` when running another authorized match.

## Python tooling

Use `uv` for Python, environments, dependencies and commands. Python is pinned
in `.python-version`; keep `pyproject.toml` and `uv.lock` together. Use `uv add`,
`uv add --dev` and `uv remove` for dependency changes. Avoid parallel pip, Poetry
or Conda workflows. The locked `websockets` runtime dependency is sufficient;
do not add a model SDK or assume external model credentials.

Run `make check` after code changes and `make format` for formatting. Checks run
Ruff, TypeScript typechecking, Rust formatting and Clippy, help output and offline
unit tests. Add focused offline tests for meaningful
strategy or execution changes. Documentation/tooling changes do not authorize
connecting to or playing a live match. Never commit virtual environments,
caches, secrets or live logs, or publish without user authorization.

## Public documentation

Use filenames without filesystem paths, repository URLs or machine-specific locations in documentation and examples. Refer to the assigned character or fighter without naming specific characters. Do not include personal usernames, machine-specific paths, private hostnames, private Git remotes or session credentials in public files.

## VS with two scripts

Each scripted fighter has separate settings, session ID and bearer token. Use
its labeled copy buttons and its own private settings file. Start each process
with `make play SETTINGS=secrets/player1-settings.json` or
`make play SETTINGS=secrets/player2-settings.json`, in separate terminals.
The VS fight waits for both required scripts to connect. Inputs and disconnect
fallback are independent. Starting/rematching affects the shared match; a
second start request may find that the other script already started it. Continue
that active match without restarting it. Never use one player's credentials to
control the other player's slot.
