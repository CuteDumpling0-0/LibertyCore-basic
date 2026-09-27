# Using Goal Agent

## Set Up

From PowerShell:

```powershell
cd "D:\project get shit done\goal-agent"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Edit `.env` and keep only credentials you actually use. Replace the example key values with real keys or leave them empty. Without provider keys, install and start Ollama with the models configured in `app/models/routing.py`.

Set `ALLOWED_WORKSPACE_ROOTS` in `.env` to include the directory containing repositories you want the agent to work in. Separate multiple roots with commas. For example:

```env
ALLOWED_WORKSPACE_ROOTS=D:\repos
```

## Start The Service

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open the dashboard at <http://127.0.0.1:8000>. The API explorer is at <http://127.0.0.1:8000/docs>. Keep the service bound to localhost unless authentication is added.

## Submit And Follow A Task

In the dashboard, create a goal, choose its repository folder, and submit it. Or submit through the API:
Choose **Free local (Ollama)** in the dashboard to force a $0 budget; API clients can set `model_preset` to `free_local`.
Choose **Free Cloud** to use allowlisted free-tier models from Groq and Gemini API, plus OpenRouter's `openrouter/free` router. It requires at least one Groq, Google/Gemini, or OpenRouter key, forces a $0 budget, and never falls back to paid model IDs. If no provider is configured, the dashboard asks before switching to Free Local; if configured providers all fail during a task, the task pauses for the same approval. Declining stops the task. OpenRouter requests carry a zero-price cap. Use free-tier Groq and Gemini API keys; the app cannot inspect the provider account's billing tier. API clients can set `model_preset` to `free_cloud` and handle the `free_cloud_unavailable` response.

```powershell
$body = @{
  goal = "Fix the checkout rounding bug and add regression tests."
  workspace = "D:\repos\store-api"
  constraints = @("Do not change public API fields", "Do not deploy")
  budget = @{ max_iterations = 10; max_minutes = 30; max_cost_usd = 2 }
} | ConvertTo-Json -Depth 5

$task = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/tasks" `
  -ContentType "application/json" `
  -Body $body

$task.id
```

Poll task status and view its event history:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/tasks/$($task.id)"
Invoke-RestMethod "http://127.0.0.1:8000/tasks/$($task.id)/events"
```

The dashboard refreshes task status and displays pending approvals. Review the requested action before approving it; edits, commands, and generated verification commands require approval. Approvals are tied to the task and action details.

## Optional Command Sandbox

Docker is not required for the service. To run commands in the isolated runner, build its image:

```powershell
docker build -f Dockerfile.sandbox -t goal-agent-sandbox:latest .
```

Set these values in `.env`, then restart the service:

```env
USE_DOCKER_SANDBOX=true
DOCKER_SANDBOX_IMAGE=goal-agent-sandbox:latest
```

Use a dedicated repository checkout: the runner mounts the selected workspace and commands may change its files. Docker mode disables container networking and applies resource limits, but it is an additional boundary, not a replacement for reviewing approvals or keeping sensitive files out of the workspace.

## Run Tests

```powershell
python -m pytest tests -q
```