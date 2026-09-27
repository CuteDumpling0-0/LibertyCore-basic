"""
app/cli.py — Rich CLI for Goal-Agent Platform.
Provides direct command-line commands:
  goal-agent run <goal> --workspace <path>
  goal-agent status <task_id>
  goal-agent list
  goal-agent events <task_id>
  goal-agent resume <task_id>
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(help="Persistent Goal-Agent CLI")
console = Console()


@app.command()
def run(
    goal: str = typer.Argument(..., help="Natural-language goal to achieve"),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace repository path"),
    max_iterations: int = typer.Option(40, "--max-iterations", "-i", help="Maximum iterations budget"),
    max_minutes: int = typer.Option(120, "--max-minutes", "-m", help="Maximum minutes budget"),
    max_cost_usd: float = typer.Option(25.0, "--max-cost", "-c", help="Cost budget in USD"),
):
    """Submit a goal and watch persistent execution until outcome is verified."""
    from app.persistence.db import AsyncSessionLocal, create_all_tables
    from app.persistence.repositories import TaskRepository
    from app.schemas import Budget
    from app.workflow.graph import WorkflowController
    from app.workflow.state import WorkflowState

    abs_workspace = str(Path(workspace).resolve())

    async def _execute():
        await create_all_tables()
        budget = Budget(
            max_iterations=max_iterations,
            max_minutes=max_minutes,
            max_cost_usd=max_cost_usd,
        )

        async with AsyncSessionLocal() as session:
            repo = TaskRepository(session)
            task_model = await repo.create(
                objective=goal,
                workspace=abs_workspace,
                max_iterations=budget.max_iterations,
                max_minutes=budget.max_minutes,
                max_cost_usd=budget.max_cost_usd,
            )
            task_id = task_model.id

        console.print(Panel(f"[bold cyan]Task Created:[/bold cyan] {task_id}\n[bold]Goal:[/bold] {goal}\n[bold]Workspace:[/bold] {abs_workspace}"))

        state = WorkflowState(
            task_id=task_id,
            workspace=abs_workspace,
            budget=budget,
        )
        controller = WorkflowController(state)
        final_status = await controller.run()

        color = "green" if final_status.value == "success" else "yellow"
        console.print(Panel(f"Execution finished with status: [{color}]{final_status.value.upper()}[/{color}] (Iteration: {state.iteration}, Spent: ${state.total_cost_usd:.3f})"))

    asyncio.run(_execute())


@app.command()
def list_tasks(limit: int = 20):
    """List recent goal agent tasks."""
    from app.persistence.db import AsyncSessionLocal, create_all_tables
    from app.persistence.repositories import TaskRepository

    async def _list():
        await create_all_tables()
        async with AsyncSessionLocal() as session:
            repo = TaskRepository(session)
            tasks = await repo.list_all(limit=limit)

        table = Table(title="Recent Goal Tasks")
        table.add_column("Task ID", style="cyan")
        table.add_column("Status", style="magenta")
        table.add_column("Objective", style="white")
        table.add_column("Iters", justify="right")
        table.add_column("Cost ($)", justify="right")

        for t in tasks:
            table.add_row(t.id[:8], t.status, t.objective[:40], str(t.iteration), f"${t.total_cost_usd:.2f}")

        console.print(table)

    asyncio.run(_list())


if __name__ == "__main__":
    app()
