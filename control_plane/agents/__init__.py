"""LLM role agents behind stable contracts.

Live mode is optional. The orchestration layer is fully testable with the
deterministic mock provider; agents only ever *propose* structured content —
policy, evidence and human gates still decide whether the workflow advances.
"""
