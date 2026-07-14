# hermes-observational-memory

Long Hermes Agent sessions lose context. Each compaction summarises the previous
summary — a telephone game where project decisions, rejected approaches, and
hard-won constraints quietly vanish. By the time you notice, rebuilding the lost
ground costs more turns than the compaction saved.

**hermes-observational-memory** keeps a curated ledger of observations and
reflections in a local SQLite database. When compaction triggers, it projects
that ledger deterministically into the summary — no LLM call at compaction
time, no telephone game, no silent loss.

---

## Why use this?

- **No silent degradation.** Observations extracted in the background survive
  every compaction as verbatim facts, not summaries of summaries.
- **Zero compaction latency.** The compression step is a database read and a
  markdown render — under 1 ms, no API call.
- **Incremental background work.** The Observer, Reflector, and Dropper run
  asynchronously on each turn; they never block the conversation.
- **Plain SQLite.** The ledger is a local `om_ledger.db` file. Inspect it
  directly with any SQLite viewer. No external service required.
- **Drop-in replacement.** One config change activates it; reverting is the
  same one change back.

---

## Getting started

**1. Clone**

```bash
git clone https://github.com/witt3rd/hermes-observational-memory.git \
    ~/src/ext/hermes-observational-memory
```

**2. Symlink into your profile's plugins directory**

```bash
ln -s ~/src/ext/hermes-observational-memory \
    "$HERMES_HOME/plugins/observational-memory"
```

`HERMES_HOME` is the root of your active Hermes profile — the directory that
contains your `config.yaml`. Verify it with `echo $HERMES_HOME`.

**3. Enable in `config.yaml`**

```yaml
plugins:
  enabled:
    - observational-memory   # alongside your existing plugins

context:
  engine: "observational-memory"
```

**4. Restart the gateway**

```bash
hermes -p <your-profile> gateway run --replace
```

Errors on load? Check `logs/errors.log` in your profile directory.

---

## Verifying it's active

The ledger file appears at `$HERMES_HOME/om_ledger.db` on the first session
that uses the engine. Confirm it's there and the session is registered:

```bash
sqlite3 "$HERMES_HOME/om_ledger.db" \
    "SELECT session_id, compression_count FROM sessions ORDER BY rowid DESC LIMIT 5;"
```

When compaction fires, the compressed context will open with:

```
[CONTEXT COMPACTION — OBSERVATIONAL MEMORY PROJECTED]
```

rather than the standard LLM-generated summary.

---

## How it works

Two Hermes extension points, two separate jobs:

**Background hydration (`system_prompt` hook — fires every turn)**

Tracks unobserved tokens since the last pass. When thresholds are crossed it
dispatches three background LLM passes:

- **Observer** — extracts atomic factual observations from raw turns into SQLite
- **Reflector** — distills active observations into durable, long-lived reflections
- **Dropper** — prunes observations fully covered by reflections

**Deterministic compaction (`ContextEngine` subclass — fires at threshold)**

At 68% of the context window, `compress()` fetches the current ledger and
renders it into a structured markdown block. No LLM call. The result replaces
the lossy summary the built-in compressor would have produced.

---

## Configuration reference

| `config.yaml` key | Default | Description |
|---|---|---|
| `context.engine` | `"compressor"` | Set to `"observational-memory"` to activate |
| `plugins.enabled` | `[]` | Must include `"observational-memory"` |
| `auxiliary.compression.model` | profile default | Model used for Observer, Reflector, and Dropper passes |
| `auxiliary.compression.provider` | profile default | Provider for the above |

The background Observer, Reflector, and Dropper each make one LLM call via the
`auxiliary.compression` model — the same side-channel model Hermes uses for
built-in context summarisation. Configure it in `config.yaml`:

```yaml
auxiliary:
  compression:
    provider: anthropic
    model: claude-sonnet-4-6
```

Each threshold crossing triggers up to three passes (one per actor). With the
default thresholds (10k / 20k tokens since last pass), this is infrequent in
normal use, but worth accounting for in cost-sensitive deployments.

The observation and reflection thresholds are currently constants in `hook.py`
(`observe_threshold = 10000`, `reflect_threshold = 20000` tokens since last pass).

---

## Uninstalling

```bash
# 1. Revert config.yaml
sed -i 's/engine: "observational-memory"/engine: compressor/' "$HERMES_HOME/config.yaml"
sed -i '/- observational-memory/d' "$HERMES_HOME/config.yaml"

# 2. Remove the symlink
rm "$HERMES_HOME/plugins/observational-memory"

# 3. Restart the gateway
hermes -p <your-profile> gateway run --replace
```

The `om_ledger.db` file is left in place. Delete it manually if you want a
clean slate.

---

## Acknowledgments

Based on the [Mastra Observational Memory](https://mastra.ai/blog/observational-memory)
architecture and inspired by the
[elpapi42/pi-observational-memory](https://github.com/elpapi42/pi-observational-memory)
extension.

---

## License

MIT
