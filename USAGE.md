# Using LibertyCore-basic

This guide covers the functionality in this repository: the local web dashboard and API, CLI, model presets, workspace restrictions, task approvals, and verification.

## Requirements

- Python 3.11 or newer
- Optional: Ollama and the models selected by the Free Local preset
- Optional: provider API keys for cloud model presets

Run commands from the repository root. On Windows, use PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

On macOS or Linux, activate with `source .venv/bin/activate` and copy the example environment file with `cp .env.example .env`.

## Configure

Edit `.env` locally. Add only the provider keys you intend to use, and do not commit this file. Set `ALLOWED_WORKSPACE_ROOTS` to the parent directories containing repositories LibertyCore-basic may access. Separate roots with commas:

```env
ALLOWED_WORKSPACE_ROOTS=D:\repos
```

The service defaults to `127.0.0.1:8000`. Its configuration validates that the host is localhost or a loopback IP. Keep it local unless you separately provide authentication and network protections.

## Start the dashboard and API

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open the dashboard at <http://127.0.0.1:8000> and the interactive API documentation at <http://127.0.0.1:8000/docs>.

## Choose a model mode

- **Auto** selects a configured provider profile, or Free Local when no supported provider key is configured.
- **Free Local** uses Ollama and forces the task's model-cost budget to `$0`.
- **Free Cloud** uses the configured free routes for Groq, Gemini API, and/or OpenRouter. It also forces a `$0` model-cost budget and rejects paid or unverifiable model routes. OpenRouter calls use a zero-price cap. The app cannot inspect the billing tier of your provider account, so configure and use credentials eligible for the free tier.
- Other configured profiles can use usage-priced providers. Their costs are controlled by the task budget.

The dashboard shows detected setup and available Free Cloud providers. A task using Free Cloud may require approval before switching to Free Local when no cloud provider is configured or all configured free routes fail.

## Submit a task

Create a task in the dashboard, choose an allowed workspace, and provide the goal, constraints, budget, and model mode. You can also submit through the API. For example, in PowerShell:

```powershell
$body = @{
  goal = "Fix the checkout rounding bug and add regression tests."
  workspace = "D:\repos\store-api"
  constraints = @("Do not change public API fields", "Do not deploy")
  model_preset = "free_local"
  budget = @{ max_iterations = 10; max_minutes = 30; max_cost_usd = 0 }
} | ConvertTo-Json -Depth 5

$task = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/tasks" `
  -ContentType "application/json" `
  -Body $body

$task.id
```

The service checks that the selected workspace is within an allowed root. You can list tasks, inspect a task and its events, and manage its lifecycle through the dashboard or the API documented at `/docs`.

## Use the CLI

Submit a goal with explicit iteration, time, and cost limits:

```powershell
python -m app.cli run "Fix test_auth and ensure pytest passes" `
  --workspace "D:\repos\my-repo" `
  --max-iterations 10 `
  --max-minutes 30 `
  --max-cost 0
```

List recent tasks:

```powershell
python -m app.cli list-tasks
```

## Approvals and verification

Review each pending action in the dashboard before approving it. The workflow gates sensitive tool actions and verification commands, records task events, and persists checkpoints. Completion depends on configured objective evidence such as command/test, file, API, or artifact checks; a reviewer model's assertion alone is not verification. Tasks can end in success, require approval, pause, or report a blocked, failed, or exhausted-budget outcome.

## Optional command sandbox

Docker sandboxing is optional. Build the command-runner image:

```powershell
docker build -f Dockerfile.sandbox -t goal-agent-sandbox:latest .
```

Set these values in `.env` and restart the service:

```env
USE_DOCKER_SANDBOX=true
DOCKER_SANDBOX_IMAGE=goal-agent-sandbox:latest
```

Use a dedicated checkout for work that may modify files. The container has no network access and applies resource limits, but you should still review approvals and keep sensitive files out of the selected workspace.

## Run tests

```powershell
python -m pytest tests -q
```

## Troubleshooting

- If the workspace is rejected, add its parent directory to `ALLOWED_WORKSPACE_ROOTS` and restart the service.
- If using Free Local, verify Ollama is running and the configured model is available.
- If using a cloud preset, verify the corresponding key is set in `.env` and restart the service.
- If a task requests approval, inspect the pending action and its details before deciding.
- If Free Cloud is unavailable, configure an eligible free provider or explicitly approve a switch to Free Local.
