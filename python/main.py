#!/usr/bin/env python3
"""Connection and match management. Edit strategy.py to change how the fighter plays."""

import argparse
import json
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.sync.client import connect

from fighter_actions import tap as tap  # Public compatibility helper for offline tests.
from strategy import DOWNED, FREE, CpuPolicy

SUPER_ACTIONS = {1: "shinkuHadoken", 2: "shinHashogeki", 3: "shinShoryuken"}


def log(message):
    print(time.strftime("%H:%M:%S"), message, flush=True)


def load_settings(filename, override=None):
    """Accept the settings JSON or full copied agent instructions containing it."""
    text = Path(filename).read_text(encoding="utf-8")
    decoder = json.JSONDecoder()
    settings = None
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except ValueError:
            continue
        if isinstance(value, dict) and "sessionId" in value and "headers" in value:
            settings = value
            break
    if settings is None:
        raise ValueError("Use copied API settings JSON or full API agent instructions.")
    session = settings["sessionId"]
    token = settings["headers"].get("Authorization", "").removeprefix("Bearer ")
    try:
        uuid.UUID(session)
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Invalid session ID.") from error
    if len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
        raise ValueError("Invalid bearer token.")
    url = override or settings.get("websocketUrl")
    if not url:
        rest = urlsplit(settings.get("url", ""))
        url = urlunsplit(
            ("wss" if rest.scheme == "https" else "ws", rest.netloc, rest.path + "/ws", "", "")
        )
    parts = urlsplit(url)
    if parts.scheme not in {"ws", "wss"} or not parts.netloc or parts.username:
        raise ValueError("Supply a ws:// or wss:// endpoint without credentials in its URL.")
    query = dict(parse_qsl(parts.query))
    query["session"] = session
    url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
    player = settings.get("player")
    if player is not None and player not in {"player", "opponent"}:
        raise ValueError("Invalid assigned player slot.")
    return url, session, token, player


class SuperExecutionMonitor:
    """Report action evidence separately from the input execution receipt."""

    def __init__(self, slot):
        self.slot = slot
        self.pending = None

    def begin(self, request_id, steps, state):
        buttons = steps[0]["buttons"]
        if "super" not in buttons:
            return
        forward = "right" if state[self.slot]["facing"] > 0 else "left"
        level = 2 if "down" in buttons else 3 if forward in buttons else 1
        meter_key = "superMeter" if self.slot == "player" else "opponentSuperMeter"
        self.pending = {
            "request": request_id,
            "action": SUPER_ACTIONS[level],
            "meter": state.get(meter_key, 0),
            "observed": False,
        }

    def observe(self, state, tracker):
        pending = self.pending
        if not pending:
            return
        if state.get(self.slot, {}).get("action") == pending["action"] and not pending["observed"]:
            pending["observed"] = True
            meter_key = "superMeter" if self.slot == "player" else "opponentSuperMeter"
            log(
                f"SUPER action observed: {pending['action']}; "
                f"meter {pending['meter']:.2f} -> {state.get(meter_key, 0):.2f}"
            )
        execution = state.get("commands", {}).get(self.slot) or {}
        receipt = next(
            (r for r in state.get("receipts", []) if r.get("id") == tracker.command_id), {}
        )
        done = (
            tracker.request_id == pending["request"]
            and tracker.command_id is not None
            and (
                execution.get("id") == tracker.command_id
                and execution.get("status") in {"completed", "cancelled"}
                or receipt.get("status") == "rejected"
            )
        )
        if done or tracker.request_id is None:
            evidence = "observed" if pending["observed"] else "not observed"
            log(f"SUPER input outcome: {execution.get('status', 'unavailable')}; action {evidence}")
            self.pending = None


class ExecutionTracker:
    """Keep one request outstanding and never resend an uncertain command."""

    def __init__(self, slot, timing=False):
        self.slot = slot
        self.timing = timing
        self.queued_after = None
        self.request_id = None
        self.command_id = None
        self.sent_at = 0
        self.kind = None

    def begin(self, kind):
        self.request_id = str(uuid.uuid4())
        self.command_id = None
        self.sent_at = time.monotonic()
        self.queued_after = None
        self.kind = kind
        return self.request_id

    def acknowledge(self, message):
        if message.get("requestId") != self.request_id:
            return
        if message["type"] == "error":
            if message.get("status") in {409, 429}:
                self.request_id = None
                return
            raise RuntimeError("Request rejected; check connection settings and input payload.")
        self.command_id = message["commandId"]
        self.queued_after = (time.monotonic() - self.sent_at) * 1000

    def observe(self, state):
        if not self.request_id:
            return True
        if self.command_id:
            if self.kind in {"start", "rematch"}:
                execution = state.get("remoteStart", {}).get("receipt") or {}
                done = {"started", "rejected"}
            else:
                execution = state.get("commands", {}).get(self.slot) or {}
                done = {"completed", "cancelled"}
                receipt = next(
                    (r for r in state.get("receipts", []) if r.get("id") == self.command_id), {}
                )
                if receipt.get("status") == "rejected":
                    self.request_id = None
                    return False
            if execution.get("id") == self.command_id and execution.get("status") in done:
                if self.timing:
                    queued = (
                        f"{self.queued_after:.0f}" if self.queued_after is not None else "unknown"
                    )
                    total = (time.monotonic() - self.sent_at) * 1000
                    log(
                        f"TIMING {self.kind}: queued {queued} ms; confirmed {total:.0f} ms ({execution.get('status')})"
                    )
                self.request_id = None
                if execution.get("status") == "rejected":
                    # The other VS script may have completed the shared start first.
                    if (
                        self.kind in {"start", "rematch"}
                        and state.get("ready") is False
                        and not state.get("matchOver")
                        and not state.get("replay")
                        and state.get("controllers", {}).get(self.slot) == "api"
                    ):
                        return True
                    raise RuntimeError("Configured match start was rejected.")
                return True
        if time.monotonic() - self.sent_at > (300 if self.kind == "start" else 30):
            raise RuntimeError(
                "Command outcome unconfirmed; reconnect before submitting more input."
            )
        return False


def gameplay_wait_reason(message, slot):
    """Require room confirmation and a playable frame before choosing an input."""
    state = message.get("state") or {}
    online = state.get("online") or {}
    if (
        state.get("gameplayMode") == "online"
        or state.get("remoteStart", {}).get("mode") == "online"
    ):
        if (
            online.get("status") != "matched"
            or not online.get("confirmed")
            or online.get("localPlayer") != slot
        ):
            return "Waiting for online room confirmation (up to 30 minutes)."
        if not online.get("started"):
            return "Room confirmed; waiting for both players to be ready."
    if (state.get("countdown") or 0) > 0 or state.get("openingDialogue"):
        return "Waiting for the round countdown to finish."
    if (
        not message.get("acceptingCommands")
        or state.get("controllers", {}).get(slot) != "api"
        or state.get("ready")
        or state.get("paused")
        or state.get("replay")
        or state.get("matchOver")
        or state.get("result", "playing") != "playing"
    ):
        return "Waiting for the assigned fighter to accept commands."
    return None


def play(
    socket, session, token, slot, start, max_seconds, matches=1, timing=False, state_file=None
):
    policy, tracker = CpuPolicy(slot), ExecutionTracker(slot, timing)
    super_monitor = SuperExecutionMonitor(slot)
    socket.send(
        json.dumps({"type": "hello", "role": "controller", "sessionId": session, "token": token})
    )
    began = last_ping = time.monotonic()
    completed = 0
    awaiting_rematch = False
    room_wait_since = None
    last_wait_reason = None
    online_match_id = None
    online_streak = 0
    ended_at = None
    last_status = -100.0
    last_action = None
    log("Connecting; waiting for fresh observations of the assigned API fighter.")
    while True:
        now = time.monotonic()
        if max_seconds and now - began >= max_seconds:
            log("Local test time limit reached; stopping without claiming a match result.")
            return
        if now - last_ping >= 1:
            socket.send(json.dumps({"type": "ping"}))
            last_ping = now
        try:
            message = json.loads(socket.recv(timeout=5))
        except TimeoutError as error:
            raise RuntimeError("Observation stream timed out; stopping inputs.") from error
        if message["type"] in {"queued", "error"}:
            if message["type"] == "error" and not message.get("requestId"):
                raise RuntimeError("Session authorization or observation unavailable.")
            tracker.acknowledge(message)
            continue
        if message["type"] != "state":
            continue
        state = message.get("state") or {}
        goal = message.get("goals", {}).get(slot) or {}
        action = (
            state.get(slot, {}).get("action")
            if gameplay_wait_reason(message, slot) is None
            else None
        )
        if action != last_action:
            last_action = action
            if action and action not in FREE | DOWNED | {"hurt", "getUp", "crumple"}:
                log("Observed assigned fighter action: " + action)
        if state_file:
            Path(state_file).write_text(json.dumps(message), encoding="utf-8")
        if now - last_status >= 5:
            last_status = now
            rival_slot = policy.rival_slot
            log(
                "STATE "
                + json.dumps(
                    {
                        "slot": slot,
                        "controller": state.get("controllers", {}).get(slot),
                        "acceptingCommands": message.get("acceptingCommands"),
                        "paused": state.get("paused"),
                        "round": state.get("roundNumber"),
                        "health": state.get(slot, {}).get("health"),
                        "rivalHealth": state.get(rival_slot, {}).get("health"),
                        "score": [state.get("playerWins"), state.get("opponentWins")],
                        "winsRequired": state.get("winsRequired"),
                    }
                )
            )
        if message.get("browserConnected") is False or (
            message.get("shouldStop") and not state.get("matchOver")
        ):
            log("Connection ended; stopping without requesting a rematch.")
            return
        if time.time() * 1000 - message.get("updatedAt", 0) > 3000:
            continue
        online = state.get("online") or {}
        online_mode = (
            state.get("gameplayMode") == "online"
            or state.get("remoteStart", {}).get("mode") == "online"
        )
        if online_mode:
            if online.get("status") == "disconnected":
                log("Online room disconnected; stopping inputs.")
                return
            if online.get("confirmed"):
                if not state.get("matchOver"):
                    room_wait_since = None
                assigned = online.get("localPlayer")
                if (
                    not state.get("matchOver")
                    and assigned in {"player", "opponent"}
                    and assigned != slot
                ):
                    slot = assigned
                    policy = CpuPolicy(slot)
                    tracker.slot = slot
                    super_monitor = SuperExecutionMonitor(slot)
                    goal = message.get("goals", {}).get(slot) or {}
                    log("Online room confirmed; assigned " + slot + ".")
            elif online.get("status") in {"joining", "waiting", "matched"}:
                if room_wait_since is None:
                    room_wait_since = time.monotonic()
                if time.monotonic() - room_wait_since >= 30 * 60:
                    raise RuntimeError("Online room assignment timed out after 30 minutes.")
        super_monitor.observe(state, tracker)
        if online_mode:
            streak = online.get("streak") or 0
            if streak > online_streak:
                online_streak = streak
                log("ONLINE verified win streak: " + str(streak))
            if online.get("status") == "finished":
                log(
                    "FINAL ONLINE "
                    + json.dumps(
                        {
                            "outcome": online.get("outcome"),
                            "streak": streak,
                            "runComplete": online.get("runComplete", False),
                            "score": [state.get("playerWins"), state.get("opponentWins")],
                        }
                    )
                )
                return
            if state.get("matchOver"):
                # Keep the finished fighter assignment until the next engine reset.
                # The room may already be assigning a new host/guest role.
                if goal.get("completed") and ended_at is None:
                    ended_at = time.monotonic()
                    log(
                        "FINAL "
                        + json.dumps(
                            {
                                "matchId": online_match_id,
                                "goal": goal,
                                "score": [state.get("playerWins"), state.get("opponentWins")],
                            }
                        )
                    )
                    if goal.get("achieved") is False:
                        return
                if room_wait_since is None:
                    room_wait_since = time.monotonic()
                if time.monotonic() - room_wait_since >= 30 * 60:
                    raise RuntimeError("Online room assignment timed out after 30 minutes.")
                if last_wait_reason != "Waiting for the next online room assignment.":
                    last_wait_reason = "Waiting for the next online room assignment."
                    log(last_wait_reason)
                continue
            next_match = online.get("matchId")
            if online.get("confirmed") and next_match and next_match != online_match_id:
                online_match_id = next_match
                policy = CpuPolicy(slot)
                tracker = ExecutionTracker(slot, timing)
                super_monitor = SuperExecutionMonitor(slot)
                ended_at = None
                start = False
                log("Online match confirmed: " + next_match)
        if state.get("matchOver"):
            if start:
                if not goal.get("completed"):
                    raise RuntimeError(
                        "Previous match result could not be verified; not requesting a rematch."
                    )
                if ended_at is None:
                    ended_at = time.monotonic()
                if message.get("canRematch"):
                    socket.send(
                        json.dumps({"type": "rematch", "requestId": tracker.begin("rematch")})
                    )
                    start = False
                    awaiting_rematch = True
                if time.monotonic() - ended_at > 15:
                    raise RuntimeError("Rematch is unavailable; stopping without retrying.")
                continue
            if ended_at is None:
                completed += 1
                ended_at = time.monotonic()
                log(
                    "FINAL "
                    + json.dumps(
                        {
                            "match": completed,
                            "goal": goal,
                            "result": state.get("result"),
                            "score": [state.get("playerWins"), state.get("opponentWins")],
                            "winsRequired": state.get("winsRequired"),
                        }
                    )
                )
                if completed >= matches:
                    return
                if not goal.get("completed"):
                    raise RuntimeError(
                        "Match result could not be verified; not requesting a rematch."
                    )
                tracker = ExecutionTracker(slot, timing)
            if not awaiting_rematch and message.get("canRematch"):
                socket.send(json.dumps({"type": "rematch", "requestId": tracker.begin("rematch")}))
                awaiting_rematch = True
            elif awaiting_rematch:
                tracker.observe(state)
            if time.monotonic() - ended_at > 15:
                raise RuntimeError("Rematch was not confirmed; stopping without retrying.")
            continue
        if awaiting_rematch:
            if not tracker.observe(state):
                continue
            awaiting_rematch = False
            ended_at = None
            policy = CpuPolicy(slot)
        # Joining an active match consumes --start without restarting it.
        if (
            start
            and state.get("ready") is False
            and state.get("controllers", {}).get(slot) == "api"
        ):
            start = False
        if not tracker.observe(state):
            continue
        if start and message.get("canStart"):
            socket.send(json.dumps({"type": "start", "requestId": tracker.begin("start")}))
            if online_mode:
                room_wait_since = time.monotonic()
            log("Start sent; waiting for room readiness and the round countdown.")
            start = False
            continue
        wait_reason = gameplay_wait_reason(message, slot)
        if wait_reason != last_wait_reason:
            last_wait_reason = wait_reason
            log(wait_reason or "Fight ready; gameplay inputs enabled.")
        if wait_reason:
            continue
        if time.time() * 1000 - message.get("updatedAt", 0) > 3000:
            continue
        if state.get(slot, {}).get("mode") != "modern":
            raise RuntimeError("This policy requires Modern controls for the assigned fighter.")
        decision = policy.choose(state)
        if decision:
            name, steps = decision
            request_id = tracker.begin("input")
            socket.send(
                json.dumps(
                    {
                        "type": "input",
                        "requestId": request_id,
                        "player": slot,
                        "steps": steps,
                    }
                )
            )
            super_monitor.begin(request_id, steps, state)
            log("Requested " + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "settings", help="Private file containing copied API settings or instructions"
    )
    parser.add_argument(
        "--player",
        choices=["player", "opponent"],
        help="Assigned fighter (defaults to copied settings, or Player 1 for legacy settings)",
    )
    parser.add_argument("--url", help="Override the WebSocket endpoint for local testing")
    parser.add_argument(
        "--start",
        action="store_true",
        help="Start the configured match, or rematch if the previous match has finished",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=0,
        help="Stop after this many seconds (0: entire match)",
    )
    parser.add_argument(
        "--matches",
        type=int,
        default=1,
        help="Play this many complete matches, requesting rematches after each (default: 1)",
    )
    parser.add_argument(
        "--timing",
        action="store_true",
        help="Log command queue and execution-confirmation durations",
    )
    parser.add_argument("--state-file", help="Private live observation file for match monitoring")
    args = parser.parse_args()
    if args.matches < 1:
        parser.error("--matches must be at least 1")
    if args.max_seconds < 0:
        parser.error("--max-seconds must be nonnegative")
    try:
        url, session, token, assigned_player = load_settings(args.settings, args.url)
    except (OSError, ValueError, KeyError, TypeError):
        parser.error(
            "Cannot read valid connection settings; copy fresh API settings into a private file."
        )
    if assigned_player and args.player and args.player != assigned_player:
        parser.error("--player conflicts with the assigned fighter in these settings.")
    player = assigned_player or args.player or "player"
    try:
        with connect(url, open_timeout=5, close_timeout=2, max_size=131072, proxy=None) as socket:
            play(
                socket,
                session,
                token,
                player,
                args.start,
                args.max_seconds,
                args.matches,
                args.timing,
                args.state_file,
            )
    except KeyboardInterrupt:
        log("Controller stopped by user.")
    except RuntimeError as error:
        log(str(error))
        raise SystemExit(1) from None
    except (ConnectionClosed, WebSocketException, OSError, ValueError):
        log(
            "Controller stopped: connection or command outcome unavailable. No automatic retries; reconnect before another run."
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
