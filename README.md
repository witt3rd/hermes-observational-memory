# hermes-observational-memory 🐦🧠

**Make long Hermes Agent sessions feel endless.**

`hermes-observational-memory` is an advanced context-compression engine and background memory ledger plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent). It maintains a curated observations pool and reflections ledger asynchronously in the background, rendering them deterministically during context compression to ensure important details survive compactions indefinitely.

Based on the [Mastra Observational Memory](https://mastra.ai/blog/observational-memory) architecture and inspired by the `pi-observational-memory` extension.

---

## The Problem

Long agent sessions eventually hit a context window wall. Built-in context compressors use lossy summarization over historical turns. Each subsequent compression summarises the previous summary, leading to "telephone-game" context degradation where crucial project details, rejected approaches, and constraints disappear.

`hermes-observational-memory` solves this by introducing a three-actor ledger system that runs incrementally *above* the compaction boundary.

## How It Works

This plugin operates in two layers:

1. **Background Hydration (System Prompt Hook):**
   Fires on every turn, counting unobserved tokens. When thresholds are reached, it asynchronously dispatches:
   - **Observer**: Extracts atomic, single-line factual observations from raw turns into an SQLite database.
   - **Reflector**: Distills active observations into durable, long-lived reflections (preferences, project decisions, constraints).
   - **Dropper**: Prunes observations that are fully covered by reflections or are redundant, keeping the active pool small.

2. **Deterministic Compaction (Context Engine):**
   Fires when prompt tokens exceed the threshold (default: 68% of context window). Instead of slow, lossy LLM summarization on the spot, it projects active observations and reflections deterministically into a structured markdown block, ensuring instantaneous and lossless compaction.

---

## Installation and Setup

### Step 1 — Clone the repository

```bash
git clone https://github.com/witt3rd/hermes-observational-memory.git ~/src/ext/hermes-observational-memory
```

### Step 2 — Wire it into your Hermes profile

The plugin is loaded from your profile's `plugins/` directory. `HERMES_HOME` is the root of your active profile (typically `~/.hermes` for a default install, or a profile-specific path when using named profiles).

```bash
# Place (or symlink) the plugin into your profile's plugins directory
ln -s ~/src/ext/hermes-observational-memory "$HERMES_HOME/plugins/observational-memory"
```

The `plugins/` directory sits directly under `HERMES_HOME` — the same directory that contains your `config.yaml`. To find the correct path for your active profile:

```bash
echo $HERMES_HOME
```

After symlinking, verify Hermes sees the plugin:

```bash
hermes plugins list
# Should show: observational-memory
```

### Step 3 — Enable the plugin in config.yaml

Open `$HERMES_HOME/config.yaml` and add two settings:

```yaml
# 1. Enable the plugin so Hermes loads it
plugins:
  enabled:
    - observational-memory   # add alongside your existing enabled plugins

# 2. Tell Hermes to use observational-memory as the active context engine
context:
  engine: "observational-memory"
```

The `context.engine` key selects which engine handles context compression. The default is `"compressor"` (the built-in). Setting it to `"observational-memory"` activates this plugin's `ObservationalMemoryEngine` in its place.

That is it — start a new Hermes session and the plugin begins tracking observations immediately.

---

## How the Plugin Integrates with Hermes

This plugin uses two Hermes extension points:

| Extension point | What it does |
|---|---|
| `register_context_engine(engine)` | Registers `ObservationalMemoryEngine` as the active context compressor, replacing the built-in summariser. |
| `register_hook("system_prompt", handler)` | Drives the background Observer / Reflector / Dropper passes each turn so the ledger stays current. |

Both are wired in `__init__.py -> register(ctx)`:

```python
def register(ctx):
    engine = ObservationalMemoryEngine()
    ctx.register_context_engine(engine)
    ctx.register_hook(
        "system_prompt",
        system_prompt_handler,
        description="Drives incremental background observation/reflection for Observational Memory"
    )
```

Hermes calls `register(ctx)` automatically when it loads the plugin. The `context.engine: "observational-memory"` config key tells Hermes to activate the engine that `register_context_engine` registered.

---

## Verifying the Installation

After starting a session, you can confirm the engine is active:

```
/compress
```

If the observational memory engine is live, compaction will produce a block beginning with `[CONTEXT COMPACTION — OBSERVATIONAL MEMORY PROJECTED]` rather than the standard LLM-generated summary.

The SQLite ledger lives at `$HERMES_HOME/om_ledger.db`. You can inspect it directly to see accumulated observations and reflections.

---

## Uninstalling

To revert to the built-in compressor:

1. Remove `context.engine: "observational-memory"` from `config.yaml` (or set it to `"compressor"`).
2. Remove `observational-memory` from `plugins.enabled`.
3. Optionally delete the symlink: `rm "$HERMES_HOME/plugins/observational-memory"`.

---

## License

MIT
