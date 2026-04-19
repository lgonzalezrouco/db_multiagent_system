"""Live LiteLLM proxy check (needs ``LLM_SERVICE_URL`` / ``LLM_MODEL`` in env)."""

from __future__ import annotations

import os

import pytest


@pytest.mark.asyncio
@pytest.mark.litellm_integration
async def test_live_litellm_sql_generation() -> None:
    """Calls the configured proxy; skips only if required ``LLM_*`` env is missing."""
    required_env_vars = ("LLM_SERVICE_URL", "LLM_MODEL")
    missing_env_vars = [
        name for name in required_env_vars if not os.environ.get(name, "").strip()
    ]
    if missing_env_vars:
        pytest.skip(
            "Live LiteLLM test requires non-empty environment variables: "
            + ", ".join(missing_env_vars),
        )

    if "LLM_API_KEY" in os.environ and not os.environ.get("LLM_API_KEY", "").strip():
        pytest.skip(
            "Live LiteLLM test requires LLM_API_KEY to be non-empty when set.",
        )
    from langchain_core.messages import HumanMessage, SystemMessage

    from agents.schemas.query_outputs import SqlGenerationOutput
    from llm.factory import create_chat_llm

    llm = create_chat_llm()
    structured = llm.with_structured_output(SqlGenerationOutput)
    messages = [
        SystemMessage(
            content=(
                "You output structured data. PostgreSQL dvdrental; "
                "SELECT only with LIMIT."
            ),
        ),
        HumanMessage(
            content="Return one SELECT that counts rows in public.actor with LIMIT 5.",
        ),
    ]
    out = await structured.ainvoke(messages)
    assert isinstance(out, SqlGenerationOutput)
    assert "LIMIT" in out.sql.upper()
