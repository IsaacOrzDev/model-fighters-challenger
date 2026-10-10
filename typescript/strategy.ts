/** START HERE to change the fighter's decisions.
 * Read choose() top to bottom: the first matching rule chooses the next move.
 * Distances are game units. fighter_actions.ts handles buttons and directions.
 * The controller sends the plan only after the match is ready.
 */
import {
  FighterActions,
  type ActionPlan,
  type Slot,
  type State,
} from "./fighter_actions.ts";

export const free = new Set([
  "idle",
  "walk",
  "crouch",
  "block",
  "guard",
  "run",
]);
export const downed = new Set([
  "ko",
  "grabbed",
  "thrown",
  "falling",
  "knockdown",
]);

export class CpuPolicy {
  round: unknown;
  lastProjectile = -100;
  pressureHealth?: number;
  ineffectivePressure = 0;
  slot: Slot;
  constructor(slot: Slot = "player") {
    this.slot = slot;
  }
  /** Read the fight; return one action plan, or undefined to wait. */
  choose(state: State): ActionPlan | undefined {
    // Start every new round with a clean memory of earlier attacks.
    if (state.roundNumber !== this.round) {
      this.round = state.roundNumber;
      this.lastProjectile = -100;
      this.pressureHealth = undefined;
      this.ineffectivePressure = 0;
    }
    const rivalSlot = this.slot === "player" ? "opponent" : "player";
    const fighter = state[this.slot],
      opponent = state[rivalSlot],
      distance = Math.abs(opponent.x - fighter.x);
    const actions = new FighterActions(fighter, opponent);
    const guard = opponent.guard ?? {},
      phase = guard.reason ?? "",
      remaining = guard.remaining ?? 0;
    const opponentAirborne = opponent.y > 0.15 || opponent.velocityY > 0,
      fighterAirborne = fighter.y > 0.15 || fighter.velocityY > 0;
    if (
      this.pressureHealth !== undefined &&
      opponent.health < this.pressureHealth
    ) {
      this.pressureHealth = undefined;
      this.ineffectivePressure = 0;
    }
    // Wait while our fighter is hurt or busy.
    if (
      fighter.stun > 0 ||
      downed.has(fighter.action) ||
      ["hurt", "getUp", "crumple"].includes(fighter.action)
    )
      return;
    if (fighterAirborne)
      return fighter.action === "jump" &&
        fighter.velocityY < -1.5 &&
        distance < 2.5
        ? actions.mediumAttack("air attack")
        : undefined;
    if (fighter.cooldown > 0 || !free.has(fighter.action)) return;
    if (actions.needsToTurn) return actions.faceOpponent();
    if (downed.has(opponent.action)) return;
    // Look for an incoming projectile or a nearby attack.
    const projectileNearby = (state.projectiles ?? []).some(
      (s: State) =>
        s.owner === rivalSlot &&
        Math.abs(s.x - fighter.x) < 3.5 &&
        (fighter.x - s.x) * s.direction >= 0,
    );
    const attackIncoming =
      ["ATTACK STARTUP", "ATTACK ACTIVE"].includes(phase) && distance < 3.5;
    const meter =
      state[this.slot === "player" ? "superMeter" : "opponentSuperMeter"] ?? 0;
    const punishWindow = Math.max(
      phase === "ATTACK RECOVERY" ? remaining : 0,
      opponent.stun,
    );
    const opponentBlocking =
      (guard.guarding ?? "none") !== "none" || opponent.action === "parry";
    // Spend Super when the opponent cannot block and the opening is long enough.
    if (!opponentAirborne && !opponentBlocking && punishWindow >= 0.3) {
      if (meter >= 3 && distance <= 1.8)
        return actions.useSuper(3, "level 3 punish");
      if (meter >= 2 && distance <= 2.5 && punishWindow >= 0.35)
        return actions.useSuper(2, "level 2 punish");
      if (meter >= 1 && distance <= 6)
        return actions.useSuper(1, "level 1 punish");
    }
    if (
      !opponentAirborne &&
      !projectileNearby &&
      meter >= 3 &&
      distance <= 1.8 &&
      phase === "ATTACK STARTUP" &&
      remaining >= 0.25 &&
      opponent.action !== "throw"
    )
      return actions.useSuper(3, "level 3 counter startup");
    if (opponent.action === "driveImpact" && attackIncoming)
      return !fighter.burnout && fighter.drive >= 1
        ? actions.driveImpact()
        : actions.block(false, 6, "guard impact");
    if (!opponentAirborne && distance <= 1.8 && opponent.action === "throw")
      return actions.breakThrow();
    if (projectileNearby || (attackIncoming && !opponentAirborne)) {
      if (
        !projectileNearby &&
        phase === "ATTACK STARTUP" &&
        remaining >= 0.15 &&
        distance <= 2.6 &&
        fighter.drive >= 2 &&
        !fighter.burnout
      )
        return actions.driveImpact("armor through startup");
      return actions.block(
        ["crouchKick", "crouchPunch"].includes(opponent.action),
      );
    }
    // Challenge jumps, then look for throws and short-range openings.
    if (opponentAirborne && distance < 2.7) return actions.risingSpecial();
    if (
      !opponentAirborne &&
      opponent.stun <= 0 &&
      distance <= 1.8 &&
      opponentBlocking
    )
      return actions.throwOpponent("throw guard");
    if (opponent.action === "parry")
      return distance > 1.8 ? actions.approach("approach parry") : undefined;
    const opening = phase === "ATTACK RECOVERY" && remaining >= 0.2;
    if (
      !opponentAirborne &&
      distance <= 1.5 &&
      opponent.stun <= 0 &&
      free.has(opponent.action)
    )
      return actions.throwOpponent();
    if (distance <= 2) {
      if (opening || opponent.health <= 6)
        return actions.lightAttack("quick punish");
      this.ineffectivePressure =
        this.pressureHealth === opponent.health
          ? this.ineffectivePressure + 1
          : 0;
      this.pressureHealth = opponent.health;
      if (this.ineffectivePressure >= 2) {
        this.ineffectivePressure = 0;
        return !fighter.burnout && fighter.drive >= 2
          ? actions.driveImpact("vary ineffective pressure")
          : actions.risingSpecial("close special pressure");
      }
      return actions.mediumAttack("normal pressure");
    }
    if (distance <= 2.6 && !opponentAirborne && (opening || opponent.burnout))
      return actions.advancingSpecial();
    // At long range, occasionally use a projectile; otherwise walk closer.
    const now = performance.now() / 1000;
    if (
      distance > 4 &&
      !opponentAirborne &&
      !attackIncoming &&
      now - this.lastProjectile >= 2
    ) {
      this.lastProjectile = now;
      return actions.projectileSpecial();
    }
    return actions.approach();
  }
}
