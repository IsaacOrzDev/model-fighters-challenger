/** The fighter's action menu. These methods build plans; they do not send input.
 * The controller sends one plan at a time and waits for its execution receipt.
 */
export type Slot = "player" | "opponent";
export type State = Record<string, any>;
export type ActionPlan = {
  name: string;
  steps: { buttons: string[]; frames: number }[];
};

/** Hold for a short time, then release every button. The game runs at 60 frames/sec. */
function pressAndRelease(
  buttons: string[],
  name: string,
  frames = 1,
): ActionPlan {
  return {
    name,
    steps: [
      { buttons, frames },
      { buttons: [], frames: 1 },
    ],
  };
}

/** Named moves for either player; directions are calculated from the observation. */
export class FighterActions {
  forward: string;
  towardOpponent: string;
  needsToTurn: boolean;
  constructor(fighter: State, opponent: State) {
    this.forward = fighter.facing > 0 ? "right" : "left";
    this.towardOpponent = opponent.x > fighter.x ? "right" : "left";
    this.needsToTurn = this.forward !== this.towardOpponent;
  }
  /** Turn toward the other fighter before attacking. */
  faceOpponent(reason = "face rival") {
    return pressAndRelease([this.towardOpponent], reason);
  }
  /** Walk toward the other fighter for a short moment. */
  approach(reason = "approach") {
    return pressAndRelease([this.towardOpponent], reason, 12);
  }
  /** Use a quick, light normal attack. */
  lightAttack(reason = "light attack") {
    return pressAndRelease(["light"], reason);
  }
  /** Use a medium normal attack, including while jumping. */
  mediumAttack(reason = "medium attack") {
    return pressAndRelease(["medium"], reason);
  }
  /** Block an incoming attack. Set low=true to crouch and block low attacks. */
  block(low = false, frames = 12, reason = "guard threat") {
    return pressAndRelease(low ? ["guard", "down"] : ["guard"], reason, frames);
  }
  /** Try to grab a nearby opponent. The strategy checks range first. */
  throwOpponent(reason = "close throw") {
    return pressAndRelease(["throw"], reason);
  }
  /** Try to escape the opponent's grab with the throw button. */
  breakThrow(reason = "throw tech") {
    return pressAndRelease(["throw"], reason);
  }
  /** Use armored Drive Impact; the strategy checks Drive first. */
  driveImpact(reason = "counter impact") {
    return pressAndRelease(["impact"], reason);
  }
  /** Use the neutral special, normally a projectile on supported fighters. */
  projectileSpecial(reason = "ranged pressure") {
    return pressAndRelease(["special"], reason);
  }
  /** Use the forward special against an opponent jumping nearby. */
  risingSpecial(reason = "anti-air") {
    return pressAndRelease([this.forward, "special"], reason);
  }
  /** Use the down special, normally the advancing spinning move. */
  advancingSpecial(reason = "special punish") {
    return pressAndRelease(["down", "special"], reason);
  }
  /** Choose Super level 1, 2, or 3. The strategy checks meter and range first. */
  useSuper(level: 1 | 2 | 3, reason = "use Super") {
    const buttons = {
      1: ["super"],
      2: ["down", "super"],
      3: [this.forward, "super"],
    };
    if (!(level in buttons)) throw new Error("Super level must be 1, 2, or 3.");
    return pressAndRelease(buttons[level], reason);
  }
}
