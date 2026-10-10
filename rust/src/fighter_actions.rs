//! The fighter's action menu. Methods build plans; they do not send game input.
//! The controller sends one plan at a time and waits for its execution receipt.
use crate::number;
use serde_json::{json, Value};

pub(crate) type ActionPlan = (String, Value);

/// Hold for a short time, then release every button. The game runs at 60 frames/sec.
fn press_and_release(buttons: &[&str], reason: &str, frames: u32) -> ActionPlan {
    (
        reason.into(),
        json!([{ "buttons": buttons, "frames": frames }, { "buttons": [], "frames": 1 }]),
    )
}

/// Named moves for either player; directions come from the current observation.
pub(crate) struct FighterActions {
    forward: &'static str,
    toward_opponent: &'static str,
}
impl FighterActions {
    pub(crate) fn new(fighter: &Value, opponent: &Value) -> Self {
        Self {
            forward: if number(&fighter["facing"]) > 0.0 {
                "right"
            } else {
                "left"
            },
            toward_opponent: if number(&opponent["x"]) > number(&fighter["x"]) {
                "right"
            } else {
                "left"
            },
        }
    }
    pub(crate) fn needs_to_turn(&self) -> bool {
        self.forward != self.toward_opponent
    }
    /// Turn toward the other fighter before attacking.
    pub(crate) fn face_opponent(&self, reason: &str) -> ActionPlan {
        press_and_release(&[self.toward_opponent], reason, 1)
    }
    /// Walk toward the other fighter for a short moment.
    pub(crate) fn approach(&self, reason: &str) -> ActionPlan {
        press_and_release(&[self.toward_opponent], reason, 12)
    }
    /// Use a quick, light normal attack.
    pub(crate) fn light_attack(&self, reason: &str) -> ActionPlan {
        press_and_release(&["light"], reason, 1)
    }
    /// Use a medium normal attack, including while jumping.
    pub(crate) fn medium_attack(&self, reason: &str) -> ActionPlan {
        press_and_release(&["medium"], reason, 1)
    }
    /// Protect against an attack; low=true crouches to block low attacks.
    pub(crate) fn block(&self, low: bool, frames: u32, reason: &str) -> ActionPlan {
        press_and_release(
            if low { &["guard", "down"] } else { &["guard"] },
            reason,
            frames,
        )
    }
    /// Try to grab a nearby opponent. The strategy checks range first.
    pub(crate) fn throw_opponent(&self, reason: &str) -> ActionPlan {
        press_and_release(&["throw"], reason, 1)
    }
    /// Try to escape the opponent's grab with the throw button.
    pub(crate) fn break_throw(&self, reason: &str) -> ActionPlan {
        press_and_release(&["throw"], reason, 1)
    }
    /// Use armored Drive Impact; the strategy checks Drive first.
    pub(crate) fn drive_impact(&self, reason: &str) -> ActionPlan {
        press_and_release(&["impact"], reason, 1)
    }
    /// Use the neutral special, normally a projectile on supported fighters.
    pub(crate) fn projectile_special(&self, reason: &str) -> ActionPlan {
        press_and_release(&["special"], reason, 1)
    }
    /// Use the forward special against an opponent jumping nearby.
    pub(crate) fn rising_special(&self, reason: &str) -> ActionPlan {
        press_and_release(&[self.forward, "special"], reason, 1)
    }
    /// Use the down special, normally the advancing spinning move.
    pub(crate) fn advancing_special(&self, reason: &str) -> ActionPlan {
        press_and_release(&["down", "special"], reason, 1)
    }
    /// Choose the Super level. The strategy checks meter and range first.
    pub(crate) fn use_super(&self, level: SuperLevel, reason: &str) -> ActionPlan {
        match level {
            SuperLevel::One => press_and_release(&["super"], reason, 1),
            SuperLevel::Two => press_and_release(&["down", "super"], reason, 1),
            SuperLevel::Three => press_and_release(&[self.forward, "super"], reason, 1),
        }
    }
}
/// These are the three valid Super choices; other levels cannot be requested.
pub(crate) enum SuperLevel {
    One,
    Two,
    Three,
}

#[cfg(test)]
#[path = "../test/fighter_actions.rs"]
mod tests;
