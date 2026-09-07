"""Format retrieved curriculum concepts into LLM-ready context text.

The formatter is deterministic and does not invent content.
It returns an empty string when no concepts are provided.
"""

from __future__ import annotations

from primus.curriculum.models import CurriculumConcept


def format_curriculum_context(concepts: list[CurriculumConcept]) -> str:
    """Convert a list of concepts into structured text block for the LLM.

    Returns ``""`` when *concepts* is empty.
    """
    if not concepts:
        return ""

    parts: list[str] = []
    for concept in concepts:
        lines: list[str] = []
        lines.append(f"Grade: {concept.grade}")
        lines.append(f"Topic: {concept.topic.replace('_', ' ').title()}")
        lines.append(f"Concept: {concept.concept}")
        lines.append("")
        lines.append("Definition:")
        lines.append(concept.definition)

        if concept.formulas:
            lines.append("")
            lines.append("Formula:")
            for f in concept.formulas:
                lines.append(f"  {f}")

        if concept.examples:
            lines.append("")
            lines.append("Example:")
            for ex in concept.examples:
                lines.append(f"  - {ex}")

        if concept.common_misconceptions:
            lines.append("")
            lines.append("Common misconceptions:")
            for m in concept.common_misconceptions:
                lines.append(f"  - {m}")

        parts.append("\n".join(lines))

    body = "\n---\n".join(parts)
    return f"[PHYSICS CURRICULUM CONTEXT]\n{body}\n[/PHYSICS CURRICULUM CONTEXT]"
