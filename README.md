# Persistent Goal-Agent Platform

A local-first, multi-model agent platform that accepts an **outcome** rather than a single prompt and continues planning, acting, observing, reviewing, and revising until that outcome is demonstrably achieved.

## Core Non-Negotiable Rules
1. **Explicit Success Criteria**: Every goal is converted into objective criteria (commands, tests, diffs, artifacts) before execution.
2. **Controller Owns State**: The durable state machine owns workflow transitions and stop decisions, never an LLM assertion alone.
3. **Observations & Evidence**: Every meaningful action produces an observation.
4. **Deterministic Verification**: Objective tests and commands are required for completion. Reviewer models are advisory.
5. **Durable Persistence**: All tasks, events, and checkpoints are stored in SQLite/PostgreSQL so work resumes seamlessly after restart.
6. **Sandboxing & Least Privilege**: Write tools prevent path traversal; shell commands adhere to an explicit allowlist.
7. **Budget Bounded**: Hard caps on iterations, elapsed wall-clock time, and model cost.
8. **Stall & Failure Classification**: Detects repeated errors or unchanged diffs, escalates reasoning models, or outputs structured blocked reports.

## Architecture

```text
       +-------------------------------+
       | Antigravity / CLI / Web API   |
       +---------------+---------------+
                       |
                       v
+----------------+   +-------------------+   +--------------------+
|   OpenViking   |<->| Workflow Machine  |<->| SQLite / Postgres  |
| Memory/Context |   | Durable Controller|   | Tasks/Events/State |
+----------------+   +---------+---------+   +--------------------+
                               |
                +--------------+--------------+
                |    Model Gateway / Router   |
                |  Claude / GPT / Gemini / OLL|
                +--------------+--------------+
                               |
                               v
                       +-------+--------+
                       |  Tool Runtime  |
                       | fs, git, shell |
                       +----------------+
```

## Quick Start

### 1. Installation
```bash
cd goal-agent
pip install -e ".[dev]"
cp .env.example .env
# Edit .env with your LLM API keys (Anthropic, OpenAI, or local Ollama)
```

### 2. Start the API Service
```bash
uvicorn app.main:app --reload --port 8000
```
Web UI Dashboard available at `http://localhost:8000`.
Interactive API docs available at `http://localhost:8000/docs`.

### Secure command sandbox (recommended outside local development)
Build the isolated command-runner image once, then enable it in `.env`:
```bash
docker build -f Dockerfile.sandbox -t goal-agent-sandbox:latest .
```
```env
USE_DOCKER_SANDBOX=true
DOCKER_SANDBOX_IMAGE=goal-agent-sandbox:latest
```
When enabled, every agent command runs in a short-lived container with no network,
dropped Linux capabilities, CPU/memory/process limits, and only the selected workspace mounted.

### 3. Run via CLI
```bash
# Submit a goal and watch persistent progress
python -m app.cli run "Fix test_auth and ensure all pytest tests pass" --workspace ./my-repo

# List recent tasks
python -m app.cli list-tasks
```

### 4. Run Test Suite
```bash
pytest tests/unit -v
```

## API Endpoints
- `POST /tasks` - Submit a new goal with workspace and optional constraints/budget.
- `GET /tasks/{id}` - Retrieve current status, criteria states, budget, and checkpoints.
- `GET /tasks/{id}/events` - Ordered chronological event timeline.
- `POST /tasks/{id}/pause` - Pause execution at safe checkpoint.
- `POST /tasks/{id}/resume` - Resume execution from latest durable state.
- `POST /tasks/{id}/approve` - Human-in-the-loop approval for gated actions.
- `GET /tasks/{id}/report` - Structured blocked or completion report with cited evidence.
