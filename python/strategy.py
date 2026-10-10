"""START HERE to change the fighter's decisions.

Read choose() from top to bottom: the first matching rule chooses the next move.
Distances are game units. Names such as actions.approach() describe the intent;
fighter_actions.py handles directions, button combinations and button releases.
A plan is only sent when the controller confirms the match is ready.
"""

import time

from fighter_actions import FighterActions

DOWNED = {"ko", "grabbed", "thrown", "falling", "knockdown"}
FREE = {"idle", "walk", "crouch", "block", "guard", "run"}


class CpuPolicy:
    """Approximate Normal CPU priorities using the public observation and button API.

    Unlike the in-engine CPU, this policy has no private collision geometry or
    direct action setters. Ranges are conservative estimates, not guaranteed hits.
    """

    def __init__(self, slot="player"):
        self.slot = slot
        self.rival_slot = "opponent" if slot == "player" else "player"
        self.round = None
        self.last_projectile = -100.0
        self.pressure_health = None
        self.ineffective_pressure = 0

    def choose(self, state):
        """Read the fight, then return one action plan (or None to wait)."""
        # Start every new round with a clean memory of earlier attacks.
        if state.get("roundNumber") != self.round:
            self.round = state.get("roundNumber")
            self.last_projectile = -100.0
            self.pressure_health = None
            self.ineffective_pressure = 0
        fighter, opponent = state[self.slot], state[self.rival_slot]
        distance = abs(opponent["x"] - fighter["x"])
        actions = FighterActions(fighter, opponent)
        guard = opponent.get("guard", {})
        phase = guard.get("reason", "")
        remaining = guard.get("remaining", 0)
        opponent_airborne = opponent["y"] > 0.15 or opponent["velocityY"] > 0
        fighter_airborne = fighter["y"] > 0.15 or fighter["velocityY"] > 0
        opponent_grounded = not opponent_airborne
        if self.pressure_health is not None and opponent["health"] < self.pressure_health:
            self.pressure_health = None
            self.ineffective_pressure = 0
        # Wait while our fighter is hurt or busy; finish a jump before ground moves.
        if fighter["stun"] > 0 or fighter["action"] in DOWNED | {"hurt", "getUp", "crumple"}:
            return None
        if fighter_airborne:
            if fighter["action"] == "jump" and fighter["velocityY"] < -1.5 and distance < 2.5:
                return actions.medium_attack("air attack")
            return None
        if fighter["cooldown"] > 0 or fighter["action"] not in FREE:
            return None
        if actions.needs_to_turn:
            return actions.face_opponent()
        if opponent["action"] in DOWNED:
            return None
        # Look for an incoming projectile or a nearby attack.
        projectile_nearby = any(
            shot["owner"] == self.rival_slot
            and abs(shot["x"] - fighter["x"]) < 3.5
            and (fighter["x"] - shot["x"]) * shot["direction"] >= 0
            for shot in state.get("projectiles", [])
        )
        attack_incoming = phase in {"ATTACK STARTUP", "ATTACK ACTIVE"} and distance < 3.5
        meter = state.get("superMeter" if self.slot == "player" else "opponentSuperMeter", 0)
        # Leave room for remote delivery plus startup. Higher levels have shorter
        # reach, so fall back to the one-bar projectile outside melee range.
        punish_window = max(
            remaining if phase == "ATTACK RECOVERY" else 0,
            opponent["stun"],
        )
        opponent_blocking = guard.get("guarding", "none") != "none" or opponent["action"] == "parry"
        if opponent_grounded and not opponent_blocking and punish_window >= 0.3:
            if meter >= 3 and distance <= 1.8:
                return actions.use_super(3, "level 3 punish")
            if meter >= 2 and distance <= 2.5 and punish_window >= 0.35:
                return actions.use_super(2, "level 2 punish")
            if meter >= 1 and distance <= 6:
                return actions.use_super(1, "level 1 punish")
        # The close level-three uppercut has startup invulnerability in the
        # verified client implementation; use it against a slow visible startup.
        if (
            opponent_grounded
            and not projectile_nearby
            and meter >= 3
            and distance <= 1.8
            and phase == "ATTACK STARTUP"
            and remaining >= 0.25
            and opponent["action"] != "throw"
        ):
            return actions.use_super(3, "level 3 counter startup")
        # Counter armor and grabs before ordinary defense, as Normal CPU does.
        if opponent["action"] == "driveImpact" and attack_incoming:
            if not fighter["burnout"] and fighter["drive"] >= 1:
                return actions.drive_impact()
            return actions.block(frames=6, reason="guard impact")
        if opponent_grounded and distance <= 1.8 and opponent["action"] == "throw":
            return actions.break_throw()
        if projectile_nearby or (attack_incoming and not opponent_airborne):
            if (
                not projectile_nearby
                and phase == "ATTACK STARTUP"
                and remaining >= 0.15
                and distance <= 2.6
                and fighter["drive"] >= 2
                and not fighter["burnout"]
            ):
                return actions.drive_impact("armor through startup")
            low = opponent["action"] in {"crouchKick", "crouchPunch"}
            return actions.block(low=low)
        # Challenge jumps, then look for throws and short-range openings.
        if opponent_airborne and distance < 2.7:
            return actions.rising_special()
        if opponent_grounded and opponent["stun"] <= 0 and distance <= 1.8:
            if guard.get("guarding", "none") != "none" or opponent["action"] == "parry":
                return actions.throw_opponent("throw guard")
        if opponent["action"] == "parry":
            return actions.approach("approach parry") if distance > 1.8 else None
        opening = phase == "ATTACK RECOVERY" and remaining >= 0.2
        if (
            opponent_grounded
            and distance <= 1.5
            and opponent["stun"] <= 0
            and opponent["action"] in FREE
        ):
            return actions.throw_opponent()
        if distance <= 2:
            if opening or opponent["health"] <= 6:
                return actions.light_attack("quick punish")
            if self.pressure_health == opponent["health"]:
                self.ineffective_pressure += 1
            else:
                self.ineffective_pressure = 0
            self.pressure_health = opponent["health"]
            if self.ineffective_pressure >= 2:
                self.ineffective_pressure = 0
                if not fighter["burnout"] and fighter["drive"] >= 2:
                    return actions.drive_impact("vary ineffective pressure")
                return actions.rising_special("close special pressure")
            return actions.medium_attack("normal pressure")
        if distance <= 2.6 and opponent_grounded and (opening or opponent["burnout"]):
            return actions.advancing_special()
        # At long range, occasionally use a projectile; otherwise walk closer.
        now = time.monotonic()
        if (
            distance > 4
            and not opponent_airborne
            and not attack_incoming
            and now - self.last_projectile >= 2
        ):
            self.last_projectile = now
            return actions.projectile_special()
        return actions.approach()
