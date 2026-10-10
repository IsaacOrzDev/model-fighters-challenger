use super::*;
#[test]
fn tls_configuration_has_a_crypto_provider() {
    initialize_tls();
    initialize_tls();
    let config = rustls::ClientConfig::builder()
        .with_root_certificates(rustls::RootCertStore::empty())
        .with_no_client_auth();
    assert!(!config.crypto_provider().cipher_suites.is_empty());
}

fn state() -> Value {
    let fighter = json!({"x":0,"y":0,"velocityY":0,"facing":1,"health":100,"action":"idle","cooldown":0,"stun":0,"burnout":false,"drive":6,"mode":"modern","guard":{"guarding":"none"}});
    let mut other = fighter.clone();
    other["x"] = json!(3);
    json!({"player":fighter,"opponent":other,"roundNumber":1,"controllers":{"player":"api"},"result":"playing","ready":false})
}
#[test]
fn countdown_blocks_inputs() {
    let mut m =
        json!({"type":"state","updatedAt":now_ms(),"acceptingCommands":true,"state":state()});
    assert!(gameplay_ready(&m, "player"));
    m["state"]["countdown"] = json!(3);
    assert!(!gameplay_ready(&m, "player"));
}
#[test]
fn online_requires_confirmation() {
    let mut m = json!({"acceptingCommands":true,"state":state()});
    m["state"]["gameplayMode"] = json!("online");
    assert!(!gameplay_ready(&m, "player"));
    m["state"]["online"] =
        json!({"status":"matched","confirmed":true,"localPlayer":"player","started":true});
    assert!(gameplay_ready(&m, "player"));
}
#[test]
fn queued_is_not_executed() {
    let mut t = Tracker::new("player", false);
    let id = t.begin("input").unwrap();
    t.acknowledge(&json!({"type":"queued","requestId":id,"commandId":"cmd"}))
        .unwrap();
    assert!(!t.observe(&state()).unwrap());
    assert!(t
        .observe(&json!({"commands":{"player":{"id":"cmd","status":"completed"}}}))
        .unwrap());
}
#[test]
fn policy_releases_buttons() {
    let (_, steps) = CpuPolicy::new("player").choose(&state()).unwrap();
    assert_eq!(steps[1]["buttons"], json!([]));
}
#[test]
fn player_two_uses_own_fighter() {
    let mut st = state();
    st["opponent"]["facing"] = json!(-1);
    let (_, steps) = CpuPolicy::new("opponent").choose(&st).unwrap();
    assert_eq!(steps[0]["buttons"], json!(["left"]));
}
#[test]
fn copied_instructions_parse() {
    let text = format!(
        "Instructions\n{}",
        json!({"sessionId":"11111111-1111-1111-1111-111111111111","url":"https://game.example/api/player","player":"opponent","headers":{"Authorization":format!("Bearer {}","a".repeat(64))}})
    );
    let settings = load_settings(&text, None).unwrap();
    assert_eq!(settings.slot.as_deref(), Some("opponent"));
    assert!(settings
        .url
        .starts_with("wss://game.example/api/player/ws?session="));
    assert!(!settings.url.contains(&settings.token));
}
fn args() -> Args {
    Args {
        settings: String::new(),
        player: None,
        url: None,
        start: false,
        matches: 1,
        max_seconds: 0.0,
        timing: false,
        state_file: None,
    }
}
fn message() -> Value {
    json!({"type":"state","updatedAt":now_ms(),"acceptingCommands":true,"state":state()})
}
#[test]
fn stale_state_cannot_attack() {
    let mut c = Controller::new("player", &args());
    let mut m = message();
    m["updatedAt"] = json!(now_ms() - 5000.0);
    assert!(c.handle(&m).unwrap().1.is_none());
}
#[test]
fn start_is_sent_only_once() {
    let mut a = args();
    a.start = true;
    let mut c = Controller::new("player", &a);
    let mut m = message();
    m["state"]["ready"] = json!(true);
    m["canStart"] = json!(true);
    assert_eq!(c.handle(&m).unwrap().1.unwrap()["type"], json!("start"));
    assert!(c.handle(&m).unwrap().1.is_none());
}
#[test]
fn match_completion_never_rematches_by_default() {
    let mut c = Controller::new("player", &args());
    let mut m = message();
    m["state"]["matchOver"] = json!(true);
    m["goals"] = json!({"player":{"completed":true,"achieved":true}});
    m["canRematch"] = json!(true);
    let (stop, command) = c.handle(&m).unwrap();
    assert!(stop);
    assert!(command.is_none());
}
#[test]
fn additional_matches_require_verified_goal() {
    let mut a = args();
    a.matches = 2;
    let mut c = Controller::new("player", &a);
    let mut m = message();
    m["state"]["matchOver"] = json!(true);
    m["canRematch"] = json!(true);
    assert!(c.handle(&m).is_err());
}
#[test]
fn tracker_rejects_concurrent_and_unconfirmed_requests() {
    let mut t = Tracker::new("player", false);
    t.begin("input").unwrap();
    assert!(t.begin("input").is_err());
    t.pending.as_mut().unwrap().sent = Instant::now() - Duration::from_secs(31);
    assert!(t.observe(&state()).is_err());
}
#[test]
fn online_win_continues_then_loss_stops() {
    let mut c = Controller::new("player", &args());
    let mut m = message();
    m["state"]["gameplayMode"] = json!("online");
    m["state"]["matchOver"] = json!(true);
    m["state"]["online"] = json!({"status":"matched","confirmed":true});
    m["goals"] = json!({"player":{"completed":true,"achieved":true}});
    assert!(!c.handle(&m).unwrap().0);
    m["state"]["matchOver"] = json!(false);
    m["state"]["online"] = json!({"status":"matched","confirmed":true,"matchId":"next","localPlayer":"player","started":true});
    c.handle(&m).unwrap();
    m["state"]["matchOver"] = json!(true);
    m["goals"]["player"]["achieved"] = json!(false);
    assert!(c.handle(&m).unwrap().0);
}
