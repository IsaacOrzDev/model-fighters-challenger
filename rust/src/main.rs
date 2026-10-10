//! Connection and match management. Edit strategy.rs to change how the fighter plays.
mod fighter_actions;
mod strategy;
use clap::Parser;
use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use std::{
    fs,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use strategy::CpuPolicy;
use tokio_tungstenite::{connect_async, tungstenite::Message};
use url::Url;
use uuid::Uuid;

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
fn number(v: &Value) -> f64 {
    v.as_f64().unwrap_or(0.0)
}
fn is_true(v: &Value) -> bool {
    v.as_bool().unwrap_or(false)
}
fn as_text(v: &Value) -> &str {
    v.as_str().unwrap_or("")
}
fn rival(slot: &str) -> &str {
    if slot == "player" {
        "opponent"
    } else {
        "player"
    }
}
fn free(action: &str) -> bool {
    ["idle", "walk", "crouch", "block", "guard", "run"].contains(&action)
}
fn downed(action: &str) -> bool {
    ["ko", "grabbed", "thrown", "falling", "knockdown"].contains(&action)
}
fn log(message: &str) {
    println!("{message}");
}

fn online(state: &Value) -> bool {
    as_text(&state["gameplayMode"]) == "online"
        || as_text(&state["remoteStart"]["mode"]) == "online"
}
fn now_ms() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
        * 1000.0
}

struct Settings {
    url: String,
    session: String,
    token: String,
    slot: Option<String>,
}
fn load_settings(text: &str, override_url: Option<&str>) -> Result<Settings> {
    let mut settings = None;
    for (offset, c) in text.char_indices() {
        if c != '{' {
            continue;
        }
        let mut values = serde_json::Deserializer::from_str(&text[offset..]).into_iter::<Value>();
        if let Some(Ok(value)) = values.next() {
            if value["sessionId"].is_string() && value["headers"].is_object() {
                settings = Some(value);
                break;
            }
        }
    }
    let value = settings.ok_or("Use copied settings JSON or full agent instructions.")?;
    let session = as_text(&value["sessionId"]).to_owned();
    Uuid::parse_str(&session).map_err(|_| "Invalid session ID.")?;
    let token = as_text(&value["headers"]["Authorization"])
        .strip_prefix("Bearer ")
        .unwrap_or(as_text(&value["headers"]["Authorization"]))
        .to_owned();
    if token.len() != 64
        || !token
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
    {
        return Err("Invalid bearer token.".into());
    }
    let fallback = if let Ok(mut rest) = Url::parse(as_text(&value["url"])) {
        let scheme = if rest.scheme() == "https" {
            "wss"
        } else {
            "ws"
        };
        rest.set_scheme(scheme).map_err(|_| "Invalid URL.")?;
        rest.set_path(&format!("{}/ws", rest.path()));
        rest.set_query(None);
        rest.set_fragment(None);
        rest.to_string()
    } else {
        String::new()
    };
    let mut url =
        Url::parse(override_url.unwrap_or(value["websocketUrl"].as_str().unwrap_or(&fallback)))
            .map_err(|_| "Invalid WebSocket URL.")?;
    if !["ws", "wss"].contains(&url.scheme())
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
    {
        return Err("Supply a WebSocket URL without credentials.".into());
    }
    let query: Vec<(String, String)> = url
        .query_pairs()
        .filter(|(k, _)| k != "session")
        .map(|(k, v)| (k.into_owned(), v.into_owned()))
        .collect();
    url.set_query(None);
    url.query_pairs_mut()
        .extend_pairs(query)
        .append_pair("session", &session);
    url.set_fragment(None);
    let slot = value["player"].as_str().map(str::to_owned);
    if slot
        .as_deref()
        .is_some_and(|p| !["player", "opponent"].contains(&p))
    {
        return Err("Invalid assigned player slot.".into());
    }
    Ok(Settings {
        url: url.to_string(),
        session,
        token,
        slot,
    })
}

fn gameplay_ready(message: &Value, slot: &str) -> bool {
    let state = &message["state"];
    let o = &state["online"];
    if online(state)
        && (as_text(&o["status"]) != "matched"
            || !is_true(&o["confirmed"])
            || as_text(&o["localPlayer"]) != slot
            || !is_true(&o["started"]))
    {
        return false;
    }
    is_true(&message["acceptingCommands"])
        && as_text(&state["controllers"][slot]) == "api"
        && number(&state["countdown"]) <= 0.0
        && !is_true(&state["openingDialogue"])
        && !is_true(&state["ready"])
        && !is_true(&state["paused"])
        && !is_true(&state["replay"])
        && !is_true(&state["matchOver"])
        && state["result"].as_str().unwrap_or("playing") == "playing"
}
struct Pending {
    request: String,
    command: Option<String>,
    kind: String,
    sent: Instant,
    queued: Option<Duration>,
}
struct Tracker {
    slot: String,
    pending: Option<Pending>,
    timing: bool,
}
impl Tracker {
    fn new(slot: &str, timing: bool) -> Self {
        Self {
            slot: slot.into(),
            pending: None,
            timing,
        }
    }
    fn begin(&mut self, kind: &str) -> Result<String> {
        if self.pending.is_some() {
            return Err("A request is already outstanding.".into());
        }
        let request = Uuid::new_v4().to_string();
        self.pending = Some(Pending {
            request: request.clone(),
            command: None,
            kind: kind.into(),
            sent: Instant::now(),
            queued: None,
        });
        Ok(request)
    }
    fn acknowledge(&mut self, m: &Value) -> Result<()> {
        let Some(p) = self.pending.as_mut() else {
            return Ok(());
        };
        if as_text(&m["requestId"]) != p.request {
            return Ok(());
        }
        if as_text(&m["type"]) == "error" {
            if [409.0, 429.0].contains(&number(&m["status"])) {
                self.pending = None;
                return Ok(());
            }
            return Err("Request rejected; check settings and payload.".into());
        }
        p.command = m["commandId"].as_str().map(str::to_owned);
        p.queued = Some(p.sent.elapsed());
        Ok(())
    }
    fn observe(&mut self, state: &Value) -> Result<bool> {
        let Some(p) = &self.pending else {
            return Ok(true);
        };
        let starting = p.kind != "input";
        let execution = if starting {
            &state["remoteStart"]["receipt"]
        } else {
            &state["commands"][&self.slot]
        };
        if let Some(id) = &p.command {
            if !starting
                && state["receipts"].as_array().is_some_and(|rs| {
                    rs.iter()
                        .any(|r| as_text(&r["id"]) == id && as_text(&r["status"]) == "rejected")
                })
            {
                self.pending = None;
                return Ok(false);
            }
            let status = as_text(&execution["status"]);
            if as_text(&execution["id"]) == id
                && (if starting {
                    ["started", "rejected"].contains(&status)
                } else {
                    ["completed", "cancelled"].contains(&status)
                })
            {
                if self.timing {
                    log(&format!(
                        "TIMING {}: queued {} ms; confirmed {} ms ({status})",
                        p.kind,
                        p.queued.unwrap_or_default().as_millis(),
                        p.sent.elapsed().as_millis()
                    ));
                }
                self.pending = None;
                if status == "rejected"
                    && !(state["ready"] == false
                        && !is_true(&state["matchOver"])
                        && !is_true(&state["replay"])
                        && as_text(&state["controllers"][&self.slot]) == "api")
                {
                    return Err("Configured start was rejected.".into());
                }
                return Ok(true);
            }
        }
        if p.sent.elapsed().as_secs() > if p.kind == "start" { 300 } else { 30 } {
            return Err("Command outcome unconfirmed; stopping without retrying.".into());
        }
        Ok(false)
    }
}

#[derive(Parser)]
#[command(about = "Control the assigned Modern-controls fighter through WebSocket")]
struct Args {
    /// Private copied settings JSON or full agent instructions
    settings: String,
    #[arg(long, value_parser = ["player", "opponent"])]
    player: Option<String>,
    #[arg(long)]
    url: Option<String>,
    #[arg(long)]
    start: bool,
    #[arg(long, default_value_t = 1)]
    matches: u32,
    #[arg(long, default_value_t = 0.0)]
    max_seconds: f64,
    #[arg(long)]
    timing: bool,
    #[arg(long)]
    state_file: Option<String>,
}
struct Controller {
    slot: String,
    policy: CpuPolicy,
    tracker: Tracker,
    start: bool,
    matches: u32,
    completed: u32,
    awaiting_rematch: bool,
    ended: Option<Instant>,
    room_wait: Option<Instant>,
    online_match: String,
    last_status: Option<Instant>,
    state_file: Option<String>,
    timing: bool,
    last_action: String,
}
impl Controller {
    fn new(slot: &str, args: &Args) -> Self {
        Self {
            slot: slot.into(),
            policy: CpuPolicy::new(slot),
            tracker: Tracker::new(slot, args.timing),
            start: args.start,
            matches: args.matches,
            completed: 0,
            awaiting_rematch: false,
            ended: None,
            room_wait: None,
            online_match: String::new(),
            last_status: None,
            state_file: args.state_file.clone(),
            timing: args.timing,
            last_action: String::new(),
        }
    }
    fn request(&mut self, kind: &str) -> Result<Value> {
        Ok(json!({ "type": kind, "requestId": self.tracker.begin(kind)? }))
    }
    // Returns (stop, optional outbound command), keeping transport separate for offline tests.
    fn handle(&mut self, m: &Value) -> Result<(bool, Option<Value>)> {
        let kind = as_text(&m["type"]);
        if kind == "error" && !m["requestId"].is_string() {
            return Err("Session authorization or observations unavailable.".into());
        }
        if ["queued", "error"].contains(&kind) {
            self.tracker.acknowledge(m)?;
            return Ok((false, None));
        }
        if kind != "state" {
            return Ok((false, None));
        }
        let state = &m["state"];
        if let Some(file) = &self.state_file {
            fs::write(file, m.to_string())?;
        }
        if m["browserConnected"] == false
            || (is_true(&m["shouldStop"]) && !is_true(&state["matchOver"]))
        {
            log("Connection ended; result unknown.");
            return Ok((true, None));
        }
        if now_ms() - number(&m["updatedAt"]) > 3000.0 {
            return Ok((false, None));
        }
        if self.last_status.is_none_or(|t| t.elapsed().as_secs() >= 5) {
            self.last_status = Some(Instant::now());
            log(&format!(
                "STATE {}",
                json!({"slot":self.slot,"round":state["roundNumber"],"health":state[&self.slot]["health"],"score":[state["playerWins"],state["opponentWins"]]})
            ));
        }
        let o = &state["online"];
        let is_online = online(state);
        if is_online {
            if as_text(&o["status"]) == "disconnected" {
                return Err("Online room disconnected.".into());
            }
            if as_text(&o["status"]) == "finished" {
                log(&format!(
                    "FINAL ONLINE {}",
                    json!({"outcome":o["outcome"],"streak":o["streak"],"runComplete":o["runComplete"],"score":[state["playerWins"],state["opponentWins"]]})
                ));
                return Ok((true, None));
            }
            if is_true(&o["confirmed"]) && !is_true(&state["matchOver"]) {
                self.room_wait = None;
                let assigned = as_text(&o["localPlayer"]);
                if ["player", "opponent"].contains(&assigned) && assigned != self.slot {
                    self.slot = assigned.into();
                    self.policy = CpuPolicy::new(&self.slot);
                    self.tracker.slot = self.slot.clone();
                }
                let id = as_text(&o["matchId"]);
                if !id.is_empty() && id != self.online_match {
                    self.online_match = id.into();
                    self.policy = CpuPolicy::new(&self.slot);
                    self.tracker = Tracker::new(&self.slot, self.timing);
                    self.ended = None;
                    self.start = false;
                }
            } else if ["joining", "waiting", "matched"].contains(&as_text(&o["status"]))
                || is_true(&state["matchOver"])
            {
                let began = self.room_wait.get_or_insert_with(Instant::now);
                if began.elapsed().as_secs() >= 1800 {
                    return Err("Online room assignment timed out after 30 minutes.".into());
                }
            }
        }
        let action = if gameplay_ready(m, &self.slot) {
            as_text(&state[&self.slot]["action"])
        } else {
            ""
        };
        if action != self.last_action {
            self.last_action = action.into();
            if !action.is_empty()
                && !free(action)
                && !downed(action)
                && !["hurt", "getUp", "crumple"].contains(&action)
            {
                log(&format!("Observed assigned fighter action: {action}"));
            }
        }
        let goal = &m["goals"][&self.slot];
        if is_true(&state["matchOver"]) {
            if is_online {
                if is_true(&goal["completed"]) && self.ended.is_none() {
                    self.ended = Some(Instant::now());
                    log(&format!(
                        "FINAL {}",
                        json!({"goal":goal,"score":[state["playerWins"],state["opponentWins"]]})
                    ));
                    if goal["achieved"] == false {
                        return Ok((true, None));
                    }
                }
                return Ok((false, None));
            }
            let mut command = None;
            if self.start {
                if !is_true(&goal["completed"]) {
                    return Err("Previous match result unverified.".into());
                }
                self.ended.get_or_insert_with(Instant::now);
                if self.tracker.pending.is_none() && is_true(&m["canRematch"]) {
                    command = Some(self.request("rematch")?);
                    self.start = false;
                    self.awaiting_rematch = true;
                }
            } else {
                if self.ended.is_none() {
                    self.ended = Some(Instant::now());
                    self.completed += 1;
                    log(&format!(
                        "FINAL {}",
                        json!({"match":self.completed,"goal":goal,"result":state["result"],"score":[state["playerWins"],state["opponentWins"]]})
                    ));
                    if self.completed >= self.matches {
                        return Ok((true, None));
                    }
                    if !is_true(&goal["completed"]) {
                        return Err("Match result unverified; no rematch.".into());
                    }
                    self.tracker = Tracker::new(&self.slot, self.timing);
                }
                if !self.awaiting_rematch && is_true(&m["canRematch"]) {
                    command = Some(self.request("rematch")?);
                    self.awaiting_rematch = true;
                } else if self.awaiting_rematch {
                    self.tracker.observe(state)?;
                }
            }
            if self.ended.is_some_and(|t| t.elapsed().as_secs() > 15) {
                return Err("Rematch unavailable or unconfirmed; no retry.".into());
            }
            return Ok((false, command));
        }
        if self.awaiting_rematch {
            if !self.tracker.observe(state)? {
                return Ok((false, None));
            }
            self.awaiting_rematch = false;
            self.ended = None;
            self.policy = CpuPolicy::new(&self.slot);
        }
        if self.start
            && state["ready"] == false
            && as_text(&state["controllers"][&self.slot]) == "api"
        {
            self.start = false;
        }
        if !self.tracker.observe(state)? {
            return Ok((false, None));
        }
        if self.start && is_true(&m["canStart"]) {
            let command = self.request("start")?;
            self.start = false;
            if is_online {
                self.room_wait = Some(Instant::now());
            }
            return Ok((false, Some(command)));
        }
        if !gameplay_ready(m, &self.slot) {
            return Ok((false, None));
        }
        if as_text(&state[&self.slot]["mode"]) != "modern" {
            return Err("This policy requires Modern controls.".into());
        }
        if let Some((name, steps)) = self.policy.choose(state) {
            let mut command = self.request("input")?;
            command["player"] = json!(self.slot);
            command["steps"] = steps;
            log(&format!("Requested {name}"));
            return Ok((false, Some(command)));
        }
        Ok((false, None))
    }
}

fn initialize_tls() {
    // A failed install means a process-wide provider is already installed.
    // Select ring explicitly rather than relying on transitive feature inference.
    let _ = rustls::crypto::ring::default_provider().install_default();
}

async fn run(args: Args) -> Result<()> {
    initialize_tls();
    if args.matches < 1 || !args.max_seconds.is_finite() || args.max_seconds < 0.0 {
        return Err("Invalid match count or time limit.".into());
    }
    let settings = fs::read_to_string(&args.settings)
        .ok()
        .and_then(|t| load_settings(&t, args.url.as_deref()).ok())
        .ok_or("Cannot read valid settings; copy fresh settings into a private file.")?;
    if settings.slot.is_some() && args.player.is_some() && settings.slot != args.player {
        return Err("Player conflicts with assigned fighter.".into());
    }
    let slot = settings
        .slot
        .as_deref()
        .or(args.player.as_deref())
        .unwrap_or("player");
    let mut controller = Controller::new(slot, &args);
    let (mut socket, _) =
        tokio::time::timeout(Duration::from_secs(5), connect_async(settings.url.as_str()))
            .await
            .map_err(|_| "Connection timed out.")?
            .map_err(|_| "WebSocket connection failed.")?;
    socket.send(Message::Text(json!({"type":"hello","role":"controller","sessionId":settings.session,"token":settings.token}).to_string().into())).await.map_err(|_| "Connection unavailable.")?;
    let began = Instant::now();
    let mut last_state = Instant::now();
    let mut heartbeat = tokio::time::interval(Duration::from_secs(1));
    loop {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => { log("Controller stopped by user."); break; },
            _ = heartbeat.tick() => {
                if args.max_seconds > 0.0 && began.elapsed().as_secs_f64() >= args.max_seconds { log("Time limit reached; result unknown."); break; }
                if last_state.elapsed().as_secs() >= 5 { return Err("Observation stream timed out.".into()); }
                socket.send(Message::Text(json!({"type":"ping"}).to_string().into())).await.map_err(|_| "Connection unavailable.")?;
            },
            incoming = socket.next() => {
                let incoming = incoming.ok_or("WebSocket disconnected; result unknown.")?.map_err(|_| "Connection unavailable; no automatic retries.")?;
                match incoming {
                    Message::Text(text) => {
                        let m: Value = serde_json::from_str(&text).map_err(|_| "Invalid observation.")?;
                        if as_text(&m["type"]) == "state" && now_ms() - number(&m["updatedAt"]) <= 3000.0 { last_state = Instant::now(); }
                        let (stop,command) = controller.handle(&m)?;
                        if stop { break; }
                        if let Some(command) = command { socket.send(Message::Text(command.to_string().into())).await.map_err(|_| "Command outcome unavailable; no automatic retries.")?; }
                    },
                    Message::Close(_) => return Err("WebSocket disconnected; result unknown.".into()),
                    _ => {}
                }
            }
        }
    }
    let _ = tokio::time::timeout(Duration::from_secs(2), socket.close(None)).await;
    Ok(())
}
#[tokio::main]
async fn main() {
    if let Err(error) = run(Args::parse()).await {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
#[path = "../test/controller.rs"]
mod tests;
