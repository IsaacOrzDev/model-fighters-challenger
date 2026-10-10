# Challenger Controller

A controller for a Model fighter. It connects to the game, chooses moves,
and tracks the result through every round of a match.

Choose Python, TypeScript, or Rust. Python is the default.

## Quick start

1. Keep the game open. Select **Script** input and **Modern** controls for your fighter.
2. Use **Copy settings** in the game and save the result as `secrets/player-settings.json`.
3. Install the dependencies and start the controller:

```sh
make setup
make play
```

Python needs `uv`. TypeScript needs Node.js 22.18 or newer and npm.
Rust needs Cargo, rustfmt, and Clippy.

Run commands from the repository root. The settings file selects your assigned
fighter automatically. Keep this file private; it contains credentials.

## Choose a language

Pass the same language when setting up and playing:

```sh
make setup LANGUAGE=typescript
make play LANGUAGE=typescript
```

Use `LANGUAGE=rust` for Rust or `LANGUAGE=python` for Python.

## Match behavior

- `make play` starts the configured fight or joins an active match.
- The controller waits during loading, countdowns, pauses, and round changes.
- Local play stops after one complete match. Run `make play` again when you want a rematch.
- Online play stays in the queue across wins and stops after a loss or queue completion.
- Press **Ctrl+C** to stop. Connection or observation failures also stop the controller.

A round win is not a match win. The controller verifies the whole-match goal
before reporting a victory.

Use another settings file or request multiple local matches:

```sh
make play SETTINGS=secrets/other-settings.json
make play ARGS="--matches 3"
```

Copy fresh settings if the game session is replaced or revoked. Never commit
settings, tokens, or live logs.

## Change the fighter's strategy

Read `AGENTS.md` first, then edit the strategy for your chosen language:

| Language   | Strategy file            |
| ---------- | ------------------------ |
| Python     | `python/strategy.py`     |
| TypeScript | `typescript/strategy.ts` |
| Rust       | `rust/src/strategy.rs`   |

Rules run from top to bottom and choose the first matching move. Use the named
`FighterActions` methods to build moves. The controller handles the connection,
input readiness, execution receipts, and match results.

The strategy uses approximate ranges and does not guarantee wins.

## Check your changes

```sh
make format
make check
```

Checks cover all three languages and their offline tests. Use
`make check-python`, `make check-typescript`, or `make check-rust` to check one language.
