//! START HERE to change the fighter's decisions.
//! Read choose() top to bottom: the first matching rule chooses the next move.
//! Distances are game units. fighter_actions.rs handles buttons and directions.
//! The controller sends the plan only after the match is ready.
use crate::fighter_actions::{ActionPlan, FighterActions, SuperLevel};
use crate::{as_text, downed, free, is_true, number, rival};
use serde_json::Value;
use std::time::Instant;

pub(crate) struct CpuPolicy {
    slot: String,
    round: Value,
    last_projectile: Option<Instant>,
    pressure_health: Option<f64>,
    ineffective: u32,
}
impl CpuPolicy {
    pub(crate) fn new(slot: &str) -> Self {
        Self {
            slot: slot.into(),
            round: Value::Null,
            last_projectile: None,
            pressure_health: None,
            ineffective: 0,
        }
    }
    /// Read the fight; return one action plan, or None to wait.
    pub(crate) fn choose(&mut self, state: &Value) -> Option<ActionPlan> {
        // Start every new round with a clean memory of earlier attacks.
        if state["roundNumber"] != self.round {
            self.round = state["roundNumber"].clone();
            self.last_projectile = None;
            self.pressure_health = None;
            self.ineffective = 0;
        }
        let other = rival(&self.slot);
        let fighter = &state[&self.slot];
        let opponent = &state[other];
        let distance = (number(&opponent["x"]) - number(&fighter["x"])).abs();
        let actions = FighterActions::new(fighter, opponent);
        let guard = &opponent["guard"];
        let phase = as_text(&guard["reason"]);
        let remaining = number(&guard["remaining"]);
        let opponent_airborne =
            number(&opponent["y"]) > 0.15 || number(&opponent["velocityY"]) > 0.0;
        let fighter_airborne = number(&fighter["y"]) > 0.15 || number(&fighter["velocityY"]) > 0.0;
        if self
            .pressure_health
            .is_some_and(|h| number(&opponent["health"]) < h)
        {
            self.pressure_health = None;
            self.ineffective = 0;
        }
        let action = as_text(&fighter["action"]);
        let opponent_action = as_text(&opponent["action"]);
        // Wait while our fighter is hurt or busy.
        if number(&fighter["stun"]) > 0.0
            || downed(action)
            || ["hurt", "getUp", "crumple"].contains(&action)
        {
            return None;
        }
        if fighter_airborne {
            return if action == "jump" && number(&fighter["velocityY"]) < -1.5 && distance < 2.5 {
                Some(actions.medium_attack("air attack"))
            } else {
                None
            };
        }
        if number(&fighter["cooldown"]) > 0.0 || !free(action) {
            return None;
        }
        if actions.needs_to_turn() {
            return Some(actions.face_opponent("face rival"));
        }
        if downed(opponent_action) {
            return None;
        }
        // Look for an incoming projectile or a nearby attack.
        let projectile_nearby = state["projectiles"].as_array().is_some_and(|shots| {
            shots.iter().any(|p| {
                as_text(&p["owner"]) == other
                    && (number(&p["x"]) - number(&fighter["x"])).abs() < 3.5
                    && (number(&fighter["x"]) - number(&p["x"])) * number(&p["direction"]) >= 0.0
            })
        });
        let attack_incoming =
            ["ATTACK STARTUP", "ATTACK ACTIVE"].contains(&phase) && distance < 3.5;
        let meter = number(
            &state[if self.slot == "player" {
                "superMeter"
            } else {
                "opponentSuperMeter"
            }],
        );
        let punish_window = (if phase == "ATTACK RECOVERY" {
            remaining
        } else {
            0.0_f64
        })
        .max(number(&opponent["stun"]));
        let opponent_blocking =
            guard["guarding"].as_str().unwrap_or("none") != "none" || opponent_action == "parry";
        // Spend Super when the opponent cannot block and the opening is long enough.
        if !opponent_airborne && !opponent_blocking && punish_window >= 0.3 {
            if meter >= 3.0 && distance <= 1.8 {
                return Some(actions.use_super(SuperLevel::Three, "level 3 punish"));
            }
            if meter >= 2.0 && distance <= 2.5 && punish_window >= 0.35 {
                return Some(actions.use_super(SuperLevel::Two, "level 2 punish"));
            }
            if meter >= 1.0 && distance <= 6.0 {
                return Some(actions.use_super(SuperLevel::One, "level 1 punish"));
            }
        }
        if !opponent_airborne
            && !projectile_nearby
            && meter >= 3.0
            && distance <= 1.8
            && phase == "ATTACK STARTUP"
            && remaining >= 0.25
            && opponent_action != "throw"
        {
            return Some(actions.use_super(SuperLevel::Three, "level 3 counter startup"));
        }
        if opponent_action == "driveImpact" && attack_incoming {
            return if !is_true(&fighter["burnout"]) && number(&fighter["drive"]) >= 1.0 {
                Some(actions.drive_impact("counter impact"))
            } else {
                Some(actions.block(false, 6, "guard impact"))
            };
        }
        if !opponent_airborne && distance <= 1.8 && opponent_action == "throw" {
            return Some(actions.break_throw("throw tech"));
        }
        if projectile_nearby || (attack_incoming && !opponent_airborne) {
            if !projectile_nearby
                && phase == "ATTACK STARTUP"
                && remaining >= 0.15
                && distance <= 2.6
                && number(&fighter["drive"]) >= 2.0
                && !is_true(&fighter["burnout"])
            {
                return Some(actions.drive_impact("armor through startup"));
            }
            return Some(actions.block(
                ["crouchKick", "crouchPunch"].contains(&opponent_action),
                12,
                "guard threat",
            ));
        }
        // Challenge jumps, then look for throws and short-range openings.
        if opponent_airborne && distance < 2.7 {
            return Some(actions.rising_special("anti-air"));
        }
        if !opponent_airborne
            && number(&opponent["stun"]) <= 0.0
            && distance <= 1.8
            && opponent_blocking
        {
            return Some(actions.throw_opponent("throw guard"));
        }
        if opponent_action == "parry" {
            return if distance > 1.8 {
                Some(actions.approach("approach parry"))
            } else {
                None
            };
        }
        let opening = phase == "ATTACK RECOVERY" && remaining >= 0.2;
        if !opponent_airborne
            && distance <= 1.5
            && number(&opponent["stun"]) <= 0.0
            && free(opponent_action)
        {
            return Some(actions.throw_opponent("close throw"));
        }
        if distance <= 2.0 {
            if opening || number(&opponent["health"]) <= 6.0 {
                return Some(actions.light_attack("quick punish"));
            }
            self.ineffective = if self.pressure_health == Some(number(&opponent["health"])) {
                self.ineffective + 1
            } else {
                0
            };
            self.pressure_health = Some(number(&opponent["health"]));
            if self.ineffective >= 2 {
                self.ineffective = 0;
                return if !is_true(&fighter["burnout"]) && number(&fighter["drive"]) >= 2.0 {
                    Some(actions.drive_impact("vary ineffective pressure"))
                } else {
                    Some(actions.rising_special("close special pressure"))
                };
            }
            return Some(actions.medium_attack("normal pressure"));
        }
        if distance <= 2.6 && !opponent_airborne && (opening || is_true(&opponent["burnout"])) {
            return Some(actions.advancing_special("special punish"));
        }
        // At long range, occasionally use a projectile; otherwise walk closer.
        if distance > 4.0
            && !opponent_airborne
            && !attack_incoming
            && self
                .last_projectile
                .is_none_or(|t| t.elapsed().as_secs_f64() >= 2.0)
        {
            self.last_projectile = Some(Instant::now());
            return Some(actions.projectile_special("ranged pressure"));
        }
        Some(actions.approach("approach"))
    }
}
