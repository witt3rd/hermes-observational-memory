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

## Installation & Setup

1. **Clone the repository** to your local external plugins directory:
   ```bash
   cd ~/src/ext
   git clone https://github.com/witt3rd/hermes-observational-memory.git
   ```

2. **Wire it into your active Hermes profile** by symlinking the plugin:
   ```bash
   # Replace <profile_name> with your active profile (e.g., 'forge')
   mkdir -p ~/.hermes/profiles/<profile_name>/plugins/context_engine/
   ln -s ~/src/ext/hermes-observational-memory ~/.hermes/profiles/<profile_name>/plugins/context_engine/observational-memory
   ```

3. **Configure your `config.yaml`** to select the engine:
   ```yaml
   context:
     engine: "observational-memory"
   ```

---

## License

MIT
