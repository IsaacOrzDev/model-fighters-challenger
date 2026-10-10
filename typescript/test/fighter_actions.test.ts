import assert from "node:assert/strict";
import { test } from "node:test";
import { FighterActions } from "../fighter_actions.ts";

test("movement follows the opponent while directional specials use facing", () => {
  const actions = new FighterActions({ x: 3, facing: 1 }, { x: 0 });
  assert.equal(actions.needsToTurn, true);
  assert.deepEqual(actions.faceOpponent().steps[0].buttons, ["left"]);
  assert.deepEqual(actions.approach().steps[0], {
    buttons: ["left"],
    frames: 12,
  });
  assert.deepEqual(actions.risingSpecial().steps[0].buttons, [
    "right",
    "special",
  ]);
});
test("Player 2's Supers, low blocking and action releases", () => {
  const actions = new FighterActions({ x: 3, facing: -1 }, { x: 0 });
  assert.equal(actions.needsToTurn, false);
  assert.deepEqual(actions.useSuper(1).steps[0].buttons, ["super"]);
  assert.deepEqual(actions.useSuper(2).steps[0].buttons, ["down", "super"]);
  assert.deepEqual(actions.useSuper(3).steps[0].buttons, ["left", "super"]);
  assert.deepEqual(actions.block(true).steps[0].buttons, ["guard", "down"]);
  const plans = [
    actions.approach(),
    actions.lightAttack(),
    actions.mediumAttack(),
    actions.block(),
    actions.throwOpponent(),
    actions.breakThrow(),
    actions.driveImpact(),
    actions.projectileSpecial(),
    actions.risingSpecial(),
    actions.advancingSpecial(),
    actions.useSuper(3),
  ];
  for (const plan of plans)
    assert.deepEqual(plan.steps.at(-1), { buttons: [], frames: 1 });
});
