"""Offline tests for WebSocket policy, settings and execution tracking."""

import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "controller_ws", Path(__file__).resolve().parents[1] / "main.py"
)
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


def snapshot(distance=1.8):
    fighter = {
        "x": 0,
        "y": 0,
        "velocityY": 0,
        "facing": 1,
        "health": 100,
        "action": "idle",
        "cooldown": 0,
        "stun": 0,
        "guard": {"guarding": "none", "reason": "READY TO BLOCK"},
    }
    return {"player": fighter, "opponent": {**fighter, "x": distance}, "projectiles": []}


def state(distance=1.8):
    value = snapshot(distance)
    for slot in ("player", "opponent"):
        value[slot].update(burnout=False, drive=6, mode="modern")
    value.update(roundNumber=1, superMeter=3, opponentSuperMeter=3)
    return value


class WebSocketControllerTests(unittest.TestCase):
    def test_start_waits_for_vs_countdown_instead_of_fixed_delay(self):
        elapsed = [0]
        sent = []

        class Socket:
            def send(self, text):
                sent.append((elapsed[0], json.loads(text)))

            def recv(self, timeout):
                elapsed[0] += 1
                value = state()
                value.update(ready=False, controllers={"player": "api"})
                message = {
                    "type": "state",
                    "updatedAt": 100000,
                    "acceptingCommands": True,
                    "state": value,
                }
                if elapsed[0] == 1:
                    value["ready"] = True
                    message["canStart"] = True
                elif elapsed[0] == 2:
                    request = next(m for _, m in sent if m["type"] == "start")
                    return json.dumps(
                        {
                            "type": "queued",
                            "requestId": request["requestId"],
                            "commandId": "start",
                        }
                    )
                else:
                    value["remoteStart"] = {"receipt": {"id": "start", "status": "started"}}
                    value["countdown"] = max(0, 6 - elapsed[0])
                if elapsed[0] == 7:
                    value["matchOver"] = True
                    message["goals"] = {"player": {"completed": True, "achieved": True}}
                return json.dumps(message)

        with (
            patch.object(controller.time, "monotonic", side_effect=lambda: elapsed[0]),
            patch.object(controller.time, "time", return_value=100),
            patch.object(controller, "log"),
        ):
            controller.play(Socket(), "session", "token", "player", True, 0)
        self.assertEqual([t for t, m in sent if m["type"] == "start"], [1])
        self.assertEqual([t for t, m in sent if m["type"] == "input"], [6])
        self.assertGreater(sum(m["type"] == "ping" for _, m in sent), 0)

    def test_only_start_confirmation_waits_five_minutes(self):
        for kind, deadline in (("start", 300), ("input", 30), ("rematch", 30)):
            with self.subTest(kind=kind):
                tracker = controller.ExecutionTracker("player")
                with patch.object(controller.time, "monotonic", return_value=0):
                    request = tracker.begin(kind)
                    tracker.acknowledge({"type": "queued", "requestId": request, "commandId": kind})
                with patch.object(controller.time, "monotonic", return_value=deadline):
                    self.assertFalse(tracker.observe({}))
                with patch.object(controller.time, "monotonic", return_value=deadline + 1):
                    with self.assertRaisesRegex(RuntimeError, "Command outcome unconfirmed"):
                        tracker.observe({})

    def test_delayed_start_receipt_completes_without_resending(self):
        tracker = controller.ExecutionTracker("player")
        with patch.object(controller.time, "monotonic", return_value=0):
            request = tracker.begin("start")
            tracker.acknowledge({"type": "queued", "requestId": request, "commandId": "start"})
        with patch.object(controller.time, "monotonic", return_value=299):
            self.assertTrue(
                tracker.observe({"remoteStart": {"receipt": {"id": "start", "status": "started"}}})
            )
        self.assertIsNone(tracker.request_id)

    def test_meter_is_spent_on_recovery_before_low_health(self):
        value = state(2.5)
        value["opponent"]["guard"] = {"reason": "ATTACK RECOVERY", "remaining": 0.6}
        policy = controller.CpuPolicy()
        self.assertEqual(policy.choose(value)[1][0]["buttons"], ["down", "super"])
        value["superMeter"] = 1
        self.assertEqual(policy.choose(value)[1][0]["buttons"], ["super"])
        value["opponent"]["guard"]["remaining"] = 0.1
        self.assertNotIn("super", policy.choose(value)[1][0]["buttons"])

    def test_super_selection_uses_meter_range_and_opening(self):
        for distance, meter, buttons in (
            (1.5, 3, ["right", "super"]),
            (2.4, 3, ["down", "super"]),
            (4, 3, ["super"]),
            (1.5, 1, ["super"]),
        ):
            with self.subTest(distance=distance, meter=meter):
                value = state(distance)
                value["superMeter"] = meter
                value["opponent"]["stun"] = 0.4
                self.assertEqual(controller.CpuPolicy().choose(value)[1][0]["buttons"], buttons)
        for change in ({"stun": 0}, {"y": 1}, {"guard": {"guarding": "high"}}):
            value = state(1.5)
            value["opponent"]["stun"] = 0.4
            value["opponent"].update(change)
            self.assertNotIn("super", controller.CpuPolicy().choose(value)[1][0]["buttons"])
        value = state(1.5)
        value["superMeter"] = 0.99
        value["opponent"]["stun"] = 0.4
        self.assertNotIn("super", controller.CpuPolicy().choose(value)[1][0]["buttons"])

    def test_player_two_super_and_own_lockout(self):
        value = state(1.5)
        value["opponent"]["facing"] = -1
        value["opponentSuperMeter"] = 3
        value["superMeter"] = 0
        value["player"]["stun"] = 0.4
        policy = controller.CpuPolicy("opponent")
        self.assertEqual(policy.choose(value)[1][0]["buttons"], ["left", "super"])
        value["opponent"]["cooldown"] = 0.2
        self.assertIsNone(policy.choose(value))

    def test_super_counter_requires_sufficient_startup(self):
        value = state(1.5)
        value["opponent"].update(
            action="kick", guard={"reason": "ATTACK STARTUP", "remaining": 0.4}
        )
        self.assertEqual(controller.CpuPolicy().choose(value)[1][0]["buttons"], ["right", "super"])
        value["opponent"]["guard"]["remaining"] = 0.1
        self.assertNotIn("super", controller.CpuPolicy().choose(value)[1][0]["buttons"])

    def test_super_monitor_does_not_equate_receipt_with_action(self):
        value = state(1.5)
        tracker = controller.ExecutionTracker("player")
        request = tracker.begin("input")
        tracker.acknowledge({"type": "queued", "requestId": request, "commandId": "super"})
        monitor = controller.SuperExecutionMonitor("player")
        monitor.begin(request, controller.tap(["right", "super"], "super")[1], value)
        value["commands"] = {"player": {"id": "super", "status": "completed"}}
        with patch.object(controller, "log") as log:
            monitor.observe(value, tracker)
        log.assert_called_once_with("SUPER input outcome: completed; action not observed")
        monitor.begin(request, controller.tap(["right", "super"], "super")[1], value)
        value["player"]["action"] = "shinShoryuken"
        value["superMeter"] = 0
        with patch.object(controller, "log") as log:
            monitor.observe(value, tracker)
        self.assertIn("SUPER action observed", log.call_args_list[0].args[0])
        self.assertIn("3.00 -> 0.00", log.call_args_list[0].args[0])
        self.assertIsNone(monitor.pending)

    def test_ineffective_pressure_varies_and_resets_after_damage(self):
        value = state()
        policy = controller.CpuPolicy()
        self.assertEqual(policy.choose(value)[1][0]["buttons"], ["medium"])
        self.assertEqual(policy.choose(value)[1][0]["buttons"], ["medium"])
        self.assertEqual(policy.choose(value)[1][0]["buttons"], ["impact"])
        value["opponent"]["health"] -= 10
        self.assertEqual(policy.choose(value)[1][0]["buttons"], ["medium"])
        self.assertEqual(policy.ineffective_pressure, 0)

    def test_close_throw_requires_grounded_unstunned_target(self):
        value = state(1.4)
        policy = controller.CpuPolicy()
        self.assertEqual(policy.choose(value)[1][0]["buttons"], ["throw"])
        value["opponent"]["stun"] = 0.2
        self.assertNotIn("throw", policy.choose(value)[1][0]["buttons"])
        value["opponent"].update(stun=0, y=1)
        self.assertEqual(policy.choose(value)[1][0]["buttons"], ["right", "special"])

    def test_both_slots_and_defense(self):
        policy = controller.CpuPolicy("opponent")
        value = state(5)
        value["opponent"]["facing"] = -1
        value["projectiles"] = [{"owner": "player", "x": 3, "direction": 1}]
        self.assertEqual(policy.choose(value)[1][0]["buttons"], ["guard"])
        value["opponent"]["stun"] = 0.2
        self.assertIsNone(policy.choose(value))

    def test_anti_air_throw_punish_and_meter(self):
        policy = controller.CpuPolicy()
        value = state()
        value["opponent"]["y"] = 1
        self.assertEqual(policy.choose(value)[1][0]["buttons"], ["right", "special"])
        value["opponent"]["y"] = 0
        value["opponent"]["guard"] = {"guarding": "high"}
        self.assertEqual(policy.choose(value)[1][0]["buttons"], ["throw"])
        value["opponent"]["health"] = 30
        value["opponent"]["guard"] = {"reason": "ATTACK RECOVERY", "remaining": 0.6}
        self.assertEqual(policy.choose(value)[1][0]["buttons"], ["right", "super"])
        value["superMeter"] = 0
        self.assertEqual(policy.choose(value)[1][0]["buttons"], ["light"])

    def test_counter_impact_and_releases(self):
        value = state()
        value["opponent"].update(action="driveImpact", guard={"reason": "ATTACK STARTUP"})
        decision = controller.CpuPolicy().choose(value)
        self.assertEqual(decision[1][0]["buttons"], ["impact"])
        self.assertEqual(decision[1][-1]["buttons"], [])
        value["player"]["burnout"] = True
        self.assertEqual(controller.CpuPolicy().choose(value)[1][0]["buttons"], ["guard"])

    def test_sequences_fit_limits_and_release(self):
        for slot in ("player", "opponent"):
            for distance in (1.8, 5, -5):
                for action in ("idle", "hurt", "punch"):
                    value = state(distance)
                    value[slot]["action"] = action
                    decision = controller.CpuPolicy(slot).choose(value)
                    if decision:
                        steps = decision[1]
                        self.assertLessEqual(len(steps), 30)
                        self.assertLessEqual(sum(step["frames"] for step in steps), 600)
                        self.assertTrue(all(1 <= step["frames"] <= 120 for step in steps))
                        self.assertEqual(steps[-1]["buttons"], [])

    def test_settings_json_and_full_instructions(self):
        settings = {
            "url": "http://localhost:3000/api/player",
            "sessionId": "12345678-1234-1234-1234-123456789012",
            "headers": {"Authorization": "Bearer " + "a" * 64},
        }
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "settings.txt"
            for text in (json.dumps(settings), "API SETTINGS\n" + json.dumps(settings)):
                file.write_text(text)
                url, session, token, player = controller.load_settings(file)
                self.assertTrue(url.startswith("ws://localhost:3000/api/player/ws?session="))
                self.assertEqual(session, settings["sessionId"])
                self.assertNotIn(token, url)
                self.assertIsNone(player)
            file.write_text("bad settings")
            with self.assertRaises(ValueError):
                controller.load_settings(file)

    def test_player_two_settings_assign_the_opponent_slot(self):
        settings = {
            "websocketUrl": "wss://game.test/api/player/ws",
            "player": "opponent",
            "sessionId": "12345678-1234-1234-1234-123456789012",
            "headers": {"Authorization": "Bearer " + "a" * 64},
        }
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "player2.json"
            file.write_text(json.dumps(settings))
            self.assertEqual(controller.load_settings(file)[3], "opponent")
            with (
                patch("sys.argv", ["main.py", str(file), "--start"]),
                patch.object(controller, "connect"),
                patch.object(controller, "play") as play,
            ):
                controller.main()
                self.assertEqual(play.call_args.args[3], "opponent")
                self.assertTrue(play.call_args.args[4])
            settings["player"] = "invalid"
            file.write_text(json.dumps(settings))
            with self.assertRaises(ValueError):
                controller.load_settings(file)

    def test_duplicate_vs_start_joins_the_already_started_match(self):
        tracker = controller.ExecutionTracker("opponent")
        request = tracker.begin("start")
        tracker.acknowledge({"type": "queued", "requestId": request, "commandId": "duplicate"})
        self.assertTrue(
            tracker.observe(
                {
                    "ready": False,
                    "matchOver": False,
                    "controllers": {"opponent": "api"},
                    "remoteStart": {"receipt": {"id": "duplicate", "status": "rejected"}},
                }
            )
        )

    def test_command_tracking_waits_for_execution_not_queue(self):
        tracker = controller.ExecutionTracker("opponent")
        request = tracker.begin("input")
        tracker.acknowledge({"type": "queued", "requestId": request, "commandId": "command"})
        self.assertFalse(
            tracker.observe({"commands": {"opponent": {"id": "command", "status": "running"}}})
        )
        self.assertTrue(
            tracker.observe({"commands": {"opponent": {"id": "command", "status": "completed"}}})
        )
        tracker.begin("input")
        tracker.sent_at -= 31
        with self.assertRaises(RuntimeError):
            tracker.observe({})

    def test_timing_measures_queue_and_confirmed_execution(self):
        tracker = controller.ExecutionTracker("player", timing=True)
        with patch.object(controller.time, "monotonic", side_effect=[10.0, 10.1, 10.35]):
            request = tracker.begin("input")
            tracker.acknowledge({"type": "queued", "requestId": request, "commandId": "timed"})
            with patch.object(controller, "log") as log:
                self.assertTrue(
                    tracker.observe(
                        {"commands": {"player": {"id": "timed", "status": "completed"}}}
                    )
                )
            log.assert_called_once_with("TIMING input: queued 100 ms; confirmed 350 ms (completed)")

    def test_start_receipt_round_wait_and_stale_ack(self):
        tracker = controller.ExecutionTracker("player")
        request = tracker.begin("start")
        tracker.acknowledge({"type": "queued", "requestId": "old", "commandId": "old"})
        self.assertIsNone(tracker.command_id)
        tracker.acknowledge({"type": "queued", "requestId": request, "commandId": "start"})
        self.assertFalse(
            tracker.observe({"remoteStart": {"receipt": {"id": "start", "status": "starting"}}})
        )
        self.assertTrue(
            tracker.observe({"remoteStart": {"receipt": {"id": "start", "status": "started"}}})
        )

    def test_two_matches_use_one_socket_and_one_rematch(self):
        sent = []
        ended = {
            "type": "state",
            "updatedAt": time.time() * 1000,
            "shouldStop": True,
            "canRematch": True,
            "goals": {"player": {"completed": True, "achieved": True}},
            "state": {"matchOver": True, "result": "win"},
        }
        messages = [ended, "ack", ended, "started", ended]

        class Socket:
            def send(self, text):
                sent.append(json.loads(text))

            def recv(self, timeout):
                message = messages.pop(0)
                if message == "ack":
                    return json.dumps(
                        {
                            "type": "queued",
                            "requestId": sent[-1]["requestId"],
                            "commandId": "rematch",
                        }
                    )
                if message == "started":
                    return json.dumps(
                        {
                            "type": "state",
                            "updatedAt": time.time() * 1000,
                            "state": {
                                "matchOver": False,
                                "remoteStart": {"receipt": {"id": "rematch", "status": "started"}},
                            },
                        }
                    )
                return json.dumps(message)

        with patch.object(controller, "log") as log:
            controller.play(Socket(), "session", "token", "player", False, 0, matches=2)
        self.assertEqual([m["type"] for m in sent], ["hello", "rematch"])
        self.assertEqual(sum(call.args[0].startswith("FINAL ") for call in log.call_args_list), 2)
        self.assertEqual(messages, [])

    def test_rerunning_start_rematches_once_and_reports_only_the_new_match(self):
        sent = []
        ended = {
            "type": "state",
            "updatedAt": time.time() * 1000,
            "shouldStop": True,
            "canRematch": True,
            "goals": {"player": {"completed": True}},
            "state": {"ready": False, "matchOver": True, "result": "lose"},
        }
        messages = [ended, "ack", ended, "started", ended]

        class Socket:
            def send(self, text):
                sent.append(json.loads(text))

            def recv(self, timeout):
                message = messages.pop(0)
                if message == "ack":
                    return json.dumps(
                        {"type": "queued", "requestId": sent[-1]["requestId"], "commandId": "rerun"}
                    )
                if message == "started":
                    return json.dumps(
                        {
                            "type": "state",
                            "updatedAt": time.time() * 1000,
                            "state": {
                                "ready": False,
                                "matchOver": False,
                                "remoteStart": {"receipt": {"id": "rerun", "status": "started"}},
                            },
                        }
                    )
                return json.dumps(message)

        with patch.object(controller, "log") as log:
            controller.play(Socket(), "session", "token", "player", True, 0)
        self.assertEqual([m["type"] for m in sent], ["hello", "rematch"])
        self.assertEqual(sum(call.args[0].startswith("FINAL ") for call in log.call_args_list), 1)
        self.assertEqual(messages, [])

    def test_start_attaches_to_active_match_without_requesting_an_extra_match(self):
        sent = []
        messages = [
            {
                "type": "state",
                "updatedAt": time.time() * 1000,
                "state": {"ready": False, "matchOver": False, "controllers": {"player": "api"}},
            },
            {
                "type": "state",
                "updatedAt": time.time() * 1000,
                "canRematch": True,
                "shouldStop": True,
                "state": {"matchOver": True, "result": "win"},
            },
        ]

        class Socket:
            def send(self, text):
                sent.append(json.loads(text))

            def recv(self, timeout):
                return json.dumps(messages.pop(0))

        with patch.object(controller, "log"):
            controller.play(Socket(), "session", "token", "player", True, 0)
        self.assertEqual([m["type"] for m in sent], ["hello"])

    def test_room_and_countdown_gates_even_with_accepting_commands(self):
        message = {
            "acceptingCommands": True,
            "state": {
                "controllers": {"opponent": "api"},
                "gameplayMode": "online",
                "online": {
                    "status": "matched",
                    "confirmed": False,
                    "localPlayer": "opponent",
                    "started": True,
                },
            },
        }
        self.assertIn("room confirmation", controller.gameplay_wait_reason(message, "opponent"))
        message["state"]["online"]["confirmed"] = True
        message["state"]["online"]["started"] = False
        self.assertIn("both players", controller.gameplay_wait_reason(message, "opponent"))
        message["state"]["online"]["started"] = True
        self.assertIsNone(controller.gameplay_wait_reason(message, "opponent"))
        self.assertIsNotNone(controller.gameplay_wait_reason(message, "player"))
        for mode in ("online", "vs"):
            message["state"]["gameplayMode"] = mode
            for countdown in (3, 2, 1):
                message["state"]["countdown"] = countdown
                self.assertIn("countdown", controller.gameplay_wait_reason(message, "opponent"))
            message["state"].update(countdown=None, openingDialogue=True)
            self.assertIsNotNone(controller.gameplay_wait_reason(message, "opponent"))
            message["state"]["openingDialogue"] = False
            self.assertIsNone(controller.gameplay_wait_reason(message, "opponent"))

    def test_online_queue_waits_then_guest_sends_only_after_countdown(self):
        sent = []
        playable = state(5)
        playable.update(
            ready=False, gameplayMode="online", controllers={"player": "online", "opponent": "api"}
        )
        stages = [
            {"online": {"status": "waiting", "confirmed": False}},
            {
                "online": {
                    "status": "matched",
                    "confirmed": True,
                    "localPlayer": "opponent",
                    "started": False,
                }
            },
            {
                "online": {
                    "status": "matched",
                    "confirmed": True,
                    "localPlayer": "opponent",
                    "started": True,
                },
                "countdown": 3,
            },
            {"countdown": 1},
            {"countdown": None},
            {
                "matchOver": True,
                "result": "lose",
                "online": {"status": "finished", "outcome": "lose", "streak": 0},
            },
        ]
        seen = 0

        class Socket:
            def send(self, text):
                message = json.loads(text)
                if message["type"] == "input":
                    self_test.assertEqual(seen, 5)
                    self_test.assertEqual(message["player"], "opponent")
                sent.append(message)

            def recv(self, timeout):
                nonlocal seen
                playable.update(stages.pop(0))
                seen += 1
                return json.dumps(
                    {
                        "type": "state",
                        "updatedAt": time.time() * 1000,
                        "acceptingCommands": True,
                        "state": playable,
                    }
                )

        self_test = self
        with (
            patch.object(controller, "log"),
            patch.object(
                controller.CpuPolicy, "choose", autospec=True, return_value=None
            ) as choose,
        ):
            controller.play(Socket(), "session", "token", "player", False, 0)
        self.assertEqual(
            choose.call_count, 1, "no action is chosen until room and countdown are ready"
        )
        playable_frame = choose.call_args.args[1]
        self.assertTrue(playable_frame["online"]["confirmed"])
        self.assertTrue(playable_frame["online"]["started"])
        self.assertIsNone(playable_frame["countdown"])
        self.assertFalse(any(m["type"] == "input" for m in sent))
        self.assertEqual(stages, [])

    def test_online_room_assignment_times_out_at_thirty_minutes(self):
        sent = []
        clock = [0.0]

        class Socket:
            def send(self, text):
                sent.append(json.loads(text))

            def recv(self, timeout):
                message = {
                    "type": "state",
                    "updatedAt": time.time() * 1000,
                    "state": {
                        "gameplayMode": "online",
                        "online": {"status": "waiting", "confirmed": False},
                    },
                }
                clock[0] += 900
                return json.dumps(message)

        with (
            patch.object(controller, "log"),
            patch.object(controller.time, "monotonic", side_effect=lambda: clock[0]),
        ):
            with self.assertRaisesRegex(RuntimeError, "30 minutes"):
                controller.play(Socket(), "session", "token", "player", False, 0)
        self.assertFalse(any(m["type"] in {"start", "input", "rematch"} for m in sent))
        self.assertTrue(any(m["type"] == "ping" for m in sent))

    def test_online_win_waits_for_next_room_and_role_then_stops_on_loss(self):
        sent = []
        playable = state(5)
        playable.update(
            ready=False, gameplayMode="online", controllers={"player": "online", "opponent": "api"}
        )
        stages = [
            (
                {
                    "online": {
                        "status": "matched",
                        "confirmed": True,
                        "started": True,
                        "localPlayer": "opponent",
                        "matchId": "first",
                        "streak": 0,
                    }
                },
                {},
            ),
            (
                {"matchOver": True, "result": "lose", "opponentWins": 2},
                {"opponent": {"completed": True, "achieved": True}},
            ),
            (
                {"online": {"status": "waiting", "confirmed": False, "streak": 1}},
                {"opponent": {"completed": True, "achieved": True}},
            ),
            (
                {
                    "online": {
                        "status": "matched",
                        "confirmed": False,
                        "localPlayer": "player",
                        "matchId": "second",
                        "streak": 1,
                    }
                },
                {},
            ),
            (
                {
                    "matchOver": False,
                    "result": "playing",
                    "opponentWins": 0,
                    "countdown": 3,
                    "controllers": {"player": "api", "opponent": "online"},
                    "online": {
                        "status": "matched",
                        "confirmed": True,
                        "started": True,
                        "localPlayer": "player",
                        "matchId": "second",
                        "streak": 1,
                    },
                },
                {},
            ),
            ({"countdown": None}, {}),
            (
                {"matchOver": True, "result": "lose", "opponentWins": 2},
                {"player": {"completed": True, "achieved": False}},
            ),
        ]
        seen = 0
        self_test = self

        class Socket:
            def send(self, text):
                message = json.loads(text)
                if message["type"] == "input":
                    self_test.assertIn(seen, (1, 6))
                sent.append(message)

            def recv(self, timeout):
                nonlocal seen
                update, goals = stages.pop(0)
                playable.update(update)
                seen += 1
                return json.dumps(
                    {
                        "type": "state",
                        "updatedAt": time.time() * 1000,
                        "acceptingCommands": True,
                        "shouldStop": playable.get("matchOver", False),
                        "goals": goals,
                        "state": playable,
                    }
                )

        with patch.object(controller, "log"):
            controller.play(Socket(), "session", "token", "player", False, 0)
        self.assertEqual(
            [m["player"] for m in sent if m["type"] == "input"], ["opponent", "player"]
        )
        self.assertFalse(any(m["type"] in {"start", "rematch"} for m in sent))
        self.assertEqual(stages, [])

    def test_online_queue_complete_stops_with_verified_streak(self):
        sent = []

        class Socket:
            def send(self, text):
                sent.append(json.loads(text))

            def recv(self, timeout):
                return json.dumps(
                    {
                        "type": "state",
                        "updatedAt": time.time() * 1000,
                        "state": {
                            "gameplayMode": "online",
                            "matchOver": True,
                            "online": {
                                "status": "finished",
                                "outcome": "win",
                                "streak": 3,
                                "runComplete": True,
                            },
                        },
                    }
                )

        with patch.object(controller, "log") as log:
            controller.play(Socket(), "session", "token", "player", True, 0)
        finals = [
            call.args[0] for call in log.call_args_list if call.args[0].startswith("FINAL ONLINE ")
        ]
        self.assertEqual(json.loads(finals[0].removeprefix("FINAL ONLINE "))["streak"], 3)
        self.assertEqual([m["type"] for m in sent], ["hello"])

    def test_disconnect_never_requests_rematch(self):
        sent = []

        class Socket:
            def send(self, text):
                sent.append(json.loads(text))

            def recv(self, timeout):
                return json.dumps({"type": "state", "browserConnected": False, "shouldStop": True})

        with patch.object(controller, "log"):
            controller.play(Socket(), "session", "token", "player", False, 0, matches=3)
        self.assertEqual([m["type"] for m in sent], ["hello"])


if __name__ == "__main__":
    unittest.main()
