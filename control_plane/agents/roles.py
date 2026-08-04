"""Role agents: bounded LLM capabilities behind the same worker contracts.

Each role has a defined objective, scoped context, a typed output schema, and
no side-effect authority. The requirement analyst interprets; the planner
composes a dependency graph — but only from the scenario's bounded action
palette (it cannot invent capabilities, seeds, or shell commands). Everything
they produce still passes schema validation, policy gates, and deterministic
verification before the workflow advances.
"""

from __future__ import annotations

import json
from typing import Any

from .adapter import ModelAdapter

REQUIREMENT_SCHEMA = {
    "required": {
        "goals": {"type": "list"},
        "non_goals": {"type": "list"},
        "acceptance_criteria": {"type": "list"},
        "ambiguities": {"type": "list", "items": "dict"},
        "proposed_assumptions": {"type": "list"},
    }
}

PLAN_SCHEMA = {
    "required": {
        "tasks": {"type": "list", "items": "dict"},
    }
}

NORMALIZE_SYSTEM = """\
You are the requirement analyst inside a governed software-engineering control
plane. Turn the raw request into structured intent. Do not invent business
rules: anything the request does not decide is an ambiguity. Mark an ambiguity
"material" when proceeding without a human decision could produce the wrong
system; propose a reversible default for it. Reply with ONLY a JSON object:
{"goals": [...], "non_goals": [...], "acceptance_criteria": [...],
 "ambiguities": [{"topic": str, "materiality": "low"|"material",
                  "proposed_default": str}],
 "proposed_assumptions": [str, ...]}"""

PLAN_SYSTEM = """\
You are the planner inside a governed software-engineering control plane.
Compose a dependency-graph plan using ONLY the provided available actions —
you may order them, set depends_on edges (task names), and adjust risk
(low|medium|high), but you may not invent capabilities, seeds, or commands.
Every action you include must keep its params exactly as given. Reply with
ONLY a JSON object: {"tasks": [{"name": str, "capability": str,
"depends_on": [str], "risk": str, "params": {...}}, ...]}"""


class RoleAgents:
    def __init__(self, adapter: ModelAdapter):
        self.adapter = adapter

    def normalize(self, request: str, scenario: dict[str, Any]) -> dict[str, Any]:
        data = self.adapter.complete_json(
            NORMALIZE_SYSTEM,
            f"Raw request:\n{request}\n\n"
            f"Known context (may be empty):\n"
            f"{json.dumps(scenario.get('requirement', {}), indent=2)[:4000]}",
            REQUIREMENT_SCHEMA,
        )
        data["request"] = request
        return data

    def plan(self, run: Any, scenario: dict[str, Any]) -> list[dict[str, Any]]:
        palette = scenario.get("plan", [])
        data = self.adapter.complete_json(
            PLAN_SYSTEM,
            f"Goal: {run.request}\n\n"
            f"Available actions (the only building blocks you may use):\n"
            f"{json.dumps(palette, indent=2)[:8000]}",
            PLAN_SCHEMA,
        )
        # Enforce the bounded palette: unknown capabilities or foreign params
        # are stripped back to the scenario's definitions.
        by_name = {entry["name"]: entry for entry in palette}
        tasks = []
        for entry in data["tasks"]:
            source = by_name.get(entry.get("name"))
            if source is None:
                continue  # the planner may not invent actions
            tasks.append({
                "name": source["name"],
                "capability": source["capability"],
                "depends_on": [d for d in entry.get("depends_on", source.get("depends_on", []))
                               if d in by_name],
                "risk": entry.get("risk", source.get("risk", "low")),
                "params": source.get("params", {}),
                **({"gate": source["gate"]} if "gate" in source else {}),
            })
        # Anything the planner dropped still runs: completeness beats brevity.
        for name, source in by_name.items():
            if not any(t["name"] == name for t in tasks):
                tasks.append(source)
        return tasks
