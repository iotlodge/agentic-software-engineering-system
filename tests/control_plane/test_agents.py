"""Agent layer: schema validation, constrained repair, bounded planning.

All tests use the deterministic mock provider. A live smoke test exists in
the CLI (`ase agents-smoke`) for key-holders; CI never needs credentials.
"""

import json

import pytest

from control_plane.agents.adapter import (
    AdapterError,
    MockProvider,
    ModelAdapter,
    SchemaViolation,
    validate_output,
)
from control_plane.agents.roles import PLAN_SCHEMA, REQUIREMENT_SCHEMA, RoleAgents
from control_plane.models import Run


class TestValidation:
    def test_missing_required_key_rejected(self):
        with pytest.raises(SchemaViolation, match="missing required key"):
            validate_output({"goals": []}, REQUIREMENT_SCHEMA)

    def test_wrong_type_rejected(self):
        data = {"goals": "not a list", "non_goals": [], "acceptance_criteria": [],
                "ambiguities": [], "proposed_assumptions": []}
        with pytest.raises(SchemaViolation, match="should be list"):
            validate_output(data, REQUIREMENT_SCHEMA)

    def test_list_item_type_checked(self):
        with pytest.raises(SchemaViolation, match="not objects"):
            validate_output({"tasks": ["just a string"]}, PLAN_SCHEMA)


class TestAdapter:
    def _valid_requirement(self):
        return json.dumps({"goals": ["g"], "non_goals": [], "acceptance_criteria": ["c"],
                           "ambiguities": [], "proposed_assumptions": []})

    def test_valid_first_response_used(self):
        adapter = ModelAdapter(MockProvider([self._valid_requirement()]))
        out = adapter.complete_json("sys", "user", REQUIREMENT_SCHEMA)
        assert out["goals"] == ["g"]

    def test_fenced_json_tolerated(self):
        adapter = ModelAdapter(MockProvider([f"```json\n{self._valid_requirement()}\n```"]))
        out = adapter.complete_json("sys", "user", REQUIREMENT_SCHEMA)
        assert out["acceptance_criteria"] == ["c"]

    def test_one_repair_attempt_then_success(self):
        provider = MockProvider(["{\"goals\": \"wrong type\"}", self._valid_requirement()])
        adapter = ModelAdapter(provider)
        out = adapter.complete_json("sys", "user", REQUIREMENT_SCHEMA)
        assert out["goals"] == ["g"]
        assert len(provider.calls) == 2
        assert "invalid" in provider.calls[1][1]  # repair prompt names the defect

    def test_second_failure_escalates(self):
        provider = MockProvider(["not json at all", "still not json"])
        adapter = ModelAdapter(provider)
        with pytest.raises((SchemaViolation, json.JSONDecodeError)):
            adapter.complete_json("sys", "user", REQUIREMENT_SCHEMA)

    def test_empty_mock_raises_adapter_error(self):
        with pytest.raises(AdapterError):
            ModelAdapter(MockProvider([])).complete_json("s", "u", REQUIREMENT_SCHEMA)


class TestBoundedPlanner:
    PALETTE = [
        {"name": "impl", "capability": "apply_seed", "risk": "medium",
         "params": {"seed": "scenarios/seeds/greenfield/impl"}},
        {"name": "verify", "capability": "run_checks", "depends_on": ["impl"],
         "params": {"checks": []}},
    ]

    def test_planner_cannot_invent_actions_or_params(self):
        malicious = json.dumps({"tasks": [
            {"name": "impl", "capability": "apply_seed", "depends_on": [],
             "params": {"seed": "/etc/passwd"}},                # foreign params
            {"name": "exfiltrate", "capability": "shell",       # invented action
             "depends_on": [], "params": {"cmd": "curl evil"}},
        ]})
        agents = RoleAgents(ModelAdapter(MockProvider([malicious])))
        run = Run(request="do it")
        tasks = agents.plan(run, {"plan": self.PALETTE})
        names = [t["name"] for t in tasks]
        assert "exfiltrate" not in names
        impl = next(t for t in tasks if t["name"] == "impl")
        assert impl["params"]["seed"] == "scenarios/seeds/greenfield/impl"

    def test_dropped_actions_are_restored(self):
        partial = json.dumps({"tasks": [
            {"name": "impl", "capability": "apply_seed", "depends_on": []},
        ]})
        agents = RoleAgents(ModelAdapter(MockProvider([partial])))
        tasks = agents.plan(Run(request="x"), {"plan": self.PALETTE})
        assert {t["name"] for t in tasks} == {"impl", "verify"}

    def test_foreign_dependencies_filtered(self):
        sneaky = json.dumps({"tasks": [
            {"name": "impl", "capability": "apply_seed",
             "depends_on": ["ghost_task"]},
            {"name": "verify", "capability": "run_checks", "depends_on": ["impl"]},
        ]})
        agents = RoleAgents(ModelAdapter(MockProvider([sneaky])))
        tasks = agents.plan(Run(request="x"), {"plan": self.PALETTE})
        impl = next(t for t in tasks if t["name"] == "impl")
        assert impl["depends_on"] == []

    def test_normalizer_output_flows_through(self):
        req = json.dumps({"goals": ["fast links"], "non_goals": [],
                          "acceptance_criteria": ["p95 < 50ms"],
                          "ambiguities": [{"topic": "cache", "materiality": "material",
                                           "proposed_default": "in-process"}],
                          "proposed_assumptions": ["ttl cache"]})
        agents = RoleAgents(ModelAdapter(MockProvider([req])))
        out = agents.normalize("make it fast", {})
        assert out["request"] == "make it fast"
        assert out["ambiguities"][0]["materiality"] == "material"
