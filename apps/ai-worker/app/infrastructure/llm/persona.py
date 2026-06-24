"""
System prompt builder from a tenant blueprint row.

Builds the LLM system prompt from a tenant_blueprints row.

# TODO S2.8 (payload_guard.py): raw_payload injection happens in router.py
# The system prompt itself must NEVER include raw user content.
"""

from __future__ import annotations


def build_system_prompt(blueprint_row: dict[str, object]) -> str:
    """
    Build the LLM system prompt from a tenant_blueprints row.

    blueprint_row keys used:
    - cognitive_state: dict (parsed from JSONB) with:
        - core_philosophy: str
        - axioms: list[str]  (the 4 universal axioms)
        - frameworks: list[str]
        - lexical_rules: dict (optional)
    - persona: str
    - persona_version: str

    Build a system prompt that:
    1. Identifies the persona and version
    2. States the core philosophy
    3. Lists the axioms as numbered rules
    4. Lists the active frameworks
    5. Includes lexical rules (avoid/prefer) if present

    Returns a well-structured string prompt.
    """
    persona = str(blueprint_row.get("persona", ""))
    persona_version = str(blueprint_row.get("persona_version", ""))
    cognitive_state = blueprint_row.get("cognitive_state", {})

    if not isinstance(cognitive_state, dict):
        cognitive_state = {}

    core_philosophy = str(cognitive_state.get("core_philosophy", ""))
    axioms = cognitive_state.get("axioms", [])
    frameworks = cognitive_state.get("frameworks", [])
    lexical_rules = cognitive_state.get("lexical_rules", {})

    if not isinstance(axioms, list):
        axioms = []
    if not isinstance(frameworks, list):
        frameworks = []
    if not isinstance(lexical_rules, dict):
        lexical_rules = {}

    lines: list[str] = []

    # 1. Persona identification
    lines.append(f"# Persona: {persona} (v{persona_version})")
    lines.append("")

    # 2. Core philosophy
    if core_philosophy:
        lines.append("## Core Philosophy")
        lines.append(core_philosophy)
        lines.append("")

    # 3. Axioms as numbered rules
    if axioms:
        lines.append("## Axioms")
        for i, axiom in enumerate(axioms, start=1):
            lines.append(f"{i}. {axiom}")
        lines.append("")

    # 4. Active frameworks
    if frameworks:
        lines.append("## Active Frameworks")
        for framework in frameworks:
            lines.append(f"- {framework}")
        lines.append("")

    # 5. Lexical rules (avoid/prefer) if present
    avoid: object = lexical_rules.get("avoid", [])
    prefer: object = lexical_rules.get("prefer", [])

    if (isinstance(avoid, list) and avoid) or (isinstance(prefer, list) and prefer):
        lines.append("## Lexical Rules")
        if isinstance(avoid, list) and avoid:
            lines.append("### Avoid")
            for term in avoid:
                lines.append(f"- {term}")
        if isinstance(prefer, list) and prefer:
            lines.append("### Prefer")
            for term in prefer:
                lines.append(f"- {term}")
        lines.append("")

    return "\n".join(lines).rstrip()
