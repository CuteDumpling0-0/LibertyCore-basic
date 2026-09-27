# LibertyCore-basic

Free/basic open-source edition of the LibertyCore goal-agent platform.

LibertyCore-basic is a local-first, durable task runner for software work. It accepts a goal, turns it into explicit success criteria, plans work, executes approved actions, verifies outcomes with commands/tests, and keeps task state across restarts.

## What LibertyCore-basic does

This repository implements a persistent agent workflow that can:

- accept a high-level goal and workspace
- create a task contract with measurable success criteria
- plan iterative work with budget limits
- execute filesystem, git, and shell actions within the allowed workspace
- require approval before actions such as edits or commands that need explicit consent
- verify completion with commands or tests
- persist task state, events, and checkpoints for resumption
- choose among supported local/free cloud model modes

## Main capabilities actually implemented

- Goal submission through the web API or CLI
- Task lifecycle management with status updates and durable checkpointing
- Approval gating for sensitive operations
- Budget enforcement for iterations, time, and cost
- Role-based model routing across planner, executor, reviewer, and summarizer roles
- Local OpenAI/Anthropic/Google/Groq/OpenRouter integration through environment-configured keys
- Free Local / Ollama mode for offline work
- Free Cloud mode using only free-tier provider routes when configured
- Safe workspace constraints and tool guardrails

## High-level architecture

This repository contains the following components:

- API service: FastAPI app exposing task endpoints
- Workflow controller: durable task state machine with planning, execution, verification, and review
- Persistence layer: SQLite-backed task/event/checkpoint storage
- Model gateway: provider abstraction and retries/fallback handling
- Tool runtime: filesystem, git, shell, and approval enforcement
- Security layer: workspace validation and command allowlists

The implementation is local-first and keeps task state persistent across restarts.

## Requirements

- Python 3.11+
- A virtual environment is recommended
- Optional: Ollama for local/free model execution
- Optional API keys for provider-backed modes
- A writable workspace directory for the repository you want the agent to operate on

## Installation

From the project root:

```bash
python -m venv .venv
source .venv/bin/activate   # or .\.venv\Scripts\Activate.ps1 on Windows
python -m pip install -e ".[dev]"
cp .env.example .env
```

## Configuration

Set environment variables in `.env` as needed.

Typical values include:

```env
ANTHROPIC_API_KEY=YOUR_API_KEY
OPENAI_API_KEY=YOUR_API_KEY
GOOGLE_API_KEY=YOUR_API_KEY
GROQ_API_KEY=YOUR_API_KEY
OPENROUTER_API_KEY=YOUR_API_KEY
OLLAMA_BASE_URL=http://localhost:11434
ALLOWED_WORKSPACE_ROOTS=/workspace,/home,C:\Users
HOST=127.0.0.1
PORT=8000
```

Do not commit `.env` files. Keep API keys and credentials in local environment variables only.

## Safe handling of API keys and credentials

- Keep keys in `.env` locally only
- Do not commit `.env` or generated secret files
- Do not print credentials in logs or examples
- Use placeholders such as `YOUR_API_KEY` in documentation
- Prefer least-privilege provider credentials and local-only access where possible

## Available model/provider modes

This repository implements the following modes:

### Auto

Automatic profile selection based on which keys are configured.

### Free Local

Uses Ollama local models. This is the offline/no-cost mode and is intended for zero-cost work.

### Free Cloud

Uses only free-tier provider routes when configured, such as free Groq / Gemini / OpenRouter routes. The code enforces zero-dollar budget behavior and rejects paid model paths when free-only enforcement is active.

## Running the application

Start the API service:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then open the dashboard at:

- http://127.0.0.1:8000

API docs are available at:

- http://127.0.0.1:8000/docs

## Command-line usage

Create a goal through the CLI:

```bash
python -m app.cli run "Fix the authentication regression" --workspace ./my-repo
```

List stored recent tasks:

```bash
python -m app.cli list-tasks
```

## Basic examples

### Submit a task via API

```bash
curl -X POST http://127.0.0.1:8000/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Fix the checkout rounding bug and add regression tests.",
    "workspace": "/path/to/repo",
    "constraints": ["Do not change the public API", "Do not deploy"],
    "budget": {
      "max_iterations": 10,
      "max_minutes": 30,
      "max_cost_usd": 2.0
    },
    "model_preset": "free_local"
  }'
```

### Check task status

```bash
curl http://127.0.0.1:8000/tasks/<task-id>
curl http://127.0.0.1:8000/tasks/<task-id>/events
```

## Task lifecycle

The workflow behavior in this repository includes:

1. goal submission
2. contract review and goal decomposition
3. planning
4. execution and approvals
5. verification and evidence collection
6. review and iteration
7. final completion or blocked report

The task state is persisted in SQLite so work can resume after a restart.

## Approvals and security

Sensitive operations require explicit approval before execution. The app enforces workspace restrictions and only allows commands within the configured workspace roots.

The command sandbox is optional and can be enabled with Docker-based sandbox settings if the environment supports it.

## Running tests

```bash
python -m pytest tests -q
```

## Troubleshooting

Common setup issues include:

- missing Python dependencies
- missing `.env` keys for non-local provider modes
- workspace outside the configured allowed roots
- Ollama not running when Free Local is selected
- model/provider configuration not matching your environment

If a task is blocked because no configured free cloud provider is available, the app may require explicit approval to switch to Free Local when the task uses the free-cloud preset.

## Security guidance

- keep the service on localhost unless you have added authentication and network controls
- restrict `ALLOWED_WORKSPACE_ROOTS` to trusted directories
- review workflow approvals before approving edits or shell actions
- avoid running this tool against untrusted repositories without reviewing the risks
- never expose `.env` files or generated credentials in shared environments

## Licensing

This repository is the free/basic open-source edition of LibertyCore and is licensed under the MIT License.

The full text is in `LICENSE`.

This repository does not include the private paid-version OpenViking/Antigravity/MCP architecture or licensing terms.
