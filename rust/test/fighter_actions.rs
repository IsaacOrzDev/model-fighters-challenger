use super::*;
#[test]
fn movement_follows_opponent_but_specials_use_facing() {
    let actions = FighterActions::new(&json!({"x":3,"facing":1}), &json!({"x":0}));
    assert!(actions.needs_to_turn());
    assert_eq!(
        actions.face_opponent("turn").1[0]["buttons"],
        json!(["left"])
    );
    assert_eq!(
        actions.approach("walk").1[0],
        json!({"buttons":["left"],"frames":12})
    );
    assert_eq!(
        actions.rising_special("jump threat").1[0]["buttons"],
        json!(["right", "special"])
    );
}
#[test]
fn player_two_supers_blocking_and_releases() {
    let actions = FighterActions::new(&json!({"x":3,"facing":-1}), &json!({"x":0}));
    assert!(!actions.needs_to_turn());
    assert_eq!(
        actions.use_super(SuperLevel::One, "Super").1[0]["buttons"],
        json!(["super"])
    );
    assert_eq!(
        actions.use_super(SuperLevel::Two, "Super").1[0]["buttons"],
        json!(["down", "super"])
    );
    assert_eq!(
        actions.use_super(SuperLevel::Three, "Super").1[0]["buttons"],
        json!(["left", "super"])
    );
    assert_eq!(
        actions.block(true, 12, "low attack").1[0]["buttons"],
        json!(["guard", "down"])
    );
    let plans = [
        actions.approach("walk"),
        actions.light_attack("attack"),
        actions.medium_attack("attack"),
        actions.block(false, 12, "block"),
        actions.throw_opponent("grab"),
        actions.break_throw("escape"),
        actions.drive_impact("impact"),
        actions.projectile_special("projectile"),
        actions.rising_special("rising"),
        actions.advancing_special("advance"),
        actions.use_super(SuperLevel::Three, "Super"),
    ];
    for (_, steps) in plans {
        assert_eq!(steps[1], json!({"buttons":[],"frames":1}));
    }
}
