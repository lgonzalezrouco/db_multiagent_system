from __future__ import annotations

from typing import Any

from graph.state import QueryGraphState


def _fallback_off_topic(preferred_language: str) -> str:
    lang = (preferred_language or "en").lower()
    if lang.startswith("es"):
        return (
            "Puedo ayudarte con preguntas sobre la base de datos DVD Rental. "
            "Intenta una consulta sobre peliculas, actores, clientes o alquileres."
        )
    return (
        "I can help with questions about the DVD Rental database. "
        "Try asking about films, actors, customers, or rentals."
    )


async def off_topic_node(state: QueryGraphState) -> dict[str, Any]:
    reason = state.query.guardrail_reason or "Question is outside DVD Rental scope."
    prefs = (
        state.memory.preferences if isinstance(state.memory.preferences, dict) else {}
    )
    preferred_language = str(prefs.get("preferred_language") or "en")
    canned = _fallback_off_topic(preferred_language)

    return {
        "steps": ["off_topic_node"],
        "query": {"outcome": "off_topic"},
        "last_error": None,
        "last_result": {
            "kind": "off_topic",
            "message": canned,
            "reason": reason,
        },
    }
