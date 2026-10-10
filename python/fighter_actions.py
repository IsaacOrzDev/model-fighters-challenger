"""The fighter's action menu. Strategy rules use these names instead of button codes.

Every method returns a plan; it does not send input to the game. The controller
sends one plan at a time, waits for its receipt, and releases all buttons at the end.
"""


def tap(buttons, name, frames=1):
    """Hold buttons for a short time, then release them. The game runs at 60 frames/sec."""
    return name, [{"buttons": buttons, "frames": frames}, {"buttons": [], "frames": 1}]


class FighterActions:
    """Named moves for either player, with left/right directions handled automatically."""

    def __init__(self, fighter, opponent):
        self.forward = "right" if fighter["facing"] > 0 else "left"
        self.toward_opponent = "right" if opponent["x"] > fighter["x"] else "left"
        self.needs_to_turn = self.forward != self.toward_opponent

    def face_opponent(self, reason="face rival"):
        """Turn toward the other fighter before attacking."""
        return tap([self.toward_opponent], reason)

    def approach(self, reason="approach"):
        """Walk toward the other fighter for a short moment."""
        return tap([self.toward_opponent], reason, frames=12)

    def light_attack(self, reason="light attack"):
        """Use a quick, light normal attack."""
        return tap(["light"], reason)

    def medium_attack(self, reason="medium attack"):
        """Use a medium normal attack, including while jumping."""
        return tap(["medium"], reason)

    def block(self, low=False, frames=12, reason="guard threat"):
        """Protect against an attack; low=True also crouches to block low attacks."""
        return tap(["guard", "down"] if low else ["guard"], reason, frames)

    def throw_opponent(self, reason="close throw"):
        """Try to grab a nearby opponent. The strategy checks range first."""
        return tap(["throw"], reason)

    def break_throw(self, reason="throw tech"):
        """Try to escape the opponent's grab with the throw button."""
        return tap(["throw"], reason)

    def drive_impact(self, reason="counter impact"):
        """Use the armored Drive Impact attack; the strategy checks Drive first."""
        return tap(["impact"], reason)

    def projectile_special(self, reason="ranged pressure"):
        """Use the neutral special, normally a projectile on supported fighters."""
        return tap(["special"], reason)

    def rising_special(self, reason="anti-air"):
        """Use the forward special to challenge an opponent jumping nearby."""
        return tap([self.forward, "special"], reason)

    def advancing_special(self, reason="special punish"):
        """Use the down special, normally the advancing spinning move."""
        return tap(["down", "special"], reason)

    def use_super(self, level, reason="use Super"):
        """Choose Super level 1, 2, or 3. The strategy checks meter and range first."""
        buttons = {1: ["super"], 2: ["down", "super"], 3: [self.forward, "super"]}
        if level not in buttons:
            raise ValueError("Super level must be 1, 2, or 3.")
        return tap(buttons[level], reason)
