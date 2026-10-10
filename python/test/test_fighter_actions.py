"""Offline checks that named moves work for either side and always release buttons."""

import unittest

from fighter_actions import FighterActions


class FighterActionsTests(unittest.TestCase):
    def test_movement_uses_opponent_position_but_specials_use_facing(self):
        actions = FighterActions({"x": 3, "facing": 1}, {"x": 0})
        self.assertTrue(actions.needs_to_turn)
        self.assertEqual(actions.face_opponent()[1][0]["buttons"], ["left"])
        self.assertEqual(actions.approach()[1][0], {"buttons": ["left"], "frames": 12})
        self.assertEqual(actions.rising_special()[1][0]["buttons"], ["right", "special"])

    def test_player_two_supers_blocking_and_releases(self):
        actions = FighterActions({"x": 3, "facing": -1}, {"x": 0})
        self.assertFalse(actions.needs_to_turn)
        self.assertEqual(actions.use_super(1)[1][0]["buttons"], ["super"])
        self.assertEqual(actions.use_super(2)[1][0]["buttons"], ["down", "super"])
        self.assertEqual(actions.use_super(3)[1][0]["buttons"], ["left", "super"])
        self.assertEqual(actions.block(low=True)[1][0]["buttons"], ["guard", "down"])
        for plan in [
            actions.approach(),
            actions.light_attack(),
            actions.medium_attack(),
            actions.block(),
            actions.throw_opponent(),
            actions.break_throw(),
            actions.drive_impact(),
            actions.projectile_special(),
            actions.rising_special(),
            actions.advancing_special(),
            actions.use_super(3),
        ]:
            self.assertEqual(plan[1][-1], {"buttons": [], "frames": 1})
        with self.assertRaises(ValueError):
            actions.use_super(4)
