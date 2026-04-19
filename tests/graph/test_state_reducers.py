"""Tests for GraphState sub-models and reducers (Spec 11)."""

from __future__ import annotations

from src.graph.state import (
    ConversationTurn,
    GraphState,
    MemoryState,
    QueryPipelineState,
    SchemaPipelineState,
    append_steps,
    merge_submodel,
)

# ---------------------------------------------------------------------------
# append_steps reducer
# ---------------------------------------------------------------------------


class TestAppendSteps:
    def test_extends_existing_list(self):
        result = append_steps(["a", "b"], ["c"])
        assert result == ["a", "b", "c"]

    def test_empty_update_returns_current(self):
        result = append_steps(["a"], [])
        assert result == ["a"]

    def test_none_update_returns_current(self):
        result = append_steps(["a"], None)  # type: ignore[arg-type]
        assert result == ["a"]

    def test_empty_current_with_update(self):
        result = append_steps([], ["step1"])
        assert result == ["step1"]

    def test_multiple_steps_in_single_update(self):
        result = append_steps(["a"], ["b", "c", "d"])
        assert result == ["a", "b", "c", "d"]

    def test_does_not_mutate_original(self):
        original = ["a", "b"]
        append_steps(original, ["c"])
        assert original == ["a", "b"]


# ---------------------------------------------------------------------------
# merge_submodel reducer
# ---------------------------------------------------------------------------


class TestMergeSubmodel:
    def test_none_update_returns_current_unchanged(self):
        current = QueryPipelineState(generated_sql="SELECT 1")
        result = merge_submodel(current, None)
        assert result is current

    def test_empty_dict_returns_current_unchanged(self):
        current = QueryPipelineState(generated_sql="SELECT 1")
        result = merge_submodel(current, {})
        assert result.generated_sql == "SELECT 1"

    def test_dict_update_overwrites_specified_fields(self):
        current = QueryPipelineState(generated_sql="SELECT 1", refinement_count=0)
        result = merge_submodel(
            current, {"generated_sql": "SELECT 2", "refinement_count": 1}
        )
        assert result.generated_sql == "SELECT 2"
        assert result.refinement_count == 1

    def test_dict_update_preserves_unspecified_fields(self):
        current = QueryPipelineState(
            generated_sql="SELECT 1",
            plan={"intent": "explore"},
            refinement_count=2,
        )
        result = merge_submodel(current, {"generated_sql": "SELECT 2"})
        assert result.plan == {"intent": "explore"}
        assert result.refinement_count == 2

    def test_basemodel_update_only_sets_explicitly_set_fields(self):
        current = QueryPipelineState(generated_sql="SELECT 1", refinement_count=3)
        update = QueryPipelineState(generated_sql="SELECT 2")
        result = merge_submodel(current, update)
        assert result.generated_sql == "SELECT 2"
        # refinement_count was not set in the update model — should preserve current
        assert result.refinement_count == 3

    def test_basemodel_update_with_none_value_clears_field(self):
        current = QueryPipelineState(generated_sql="SELECT 1")
        update = QueryPipelineState(generated_sql=None)
        result = merge_submodel(current, update)
        assert result.generated_sql is None

    def test_partial_schema_merge_preserves_unset(self):
        current = SchemaPipelineState(ready=True, metadata={"tables": []})
        result = merge_submodel(current, {"persist_error": "DB down"})
        assert result.ready is True
        assert result.metadata == {"tables": []}
        assert result.persist_error == "DB down"

    def test_memory_merge_preserves_history(self):
        turn = ConversationTurn(user_input="hello", sql="SELECT 1")
        current = MemoryState(conversation_history=[turn], preferences={"lang": "en"})
        result = merge_submodel(current, {"warning": "low memory"})
        assert len(result.conversation_history) == 1
        assert result.preferences == {"lang": "en"}
        assert result.warning == "low memory"


# ---------------------------------------------------------------------------
# Default construction
# ---------------------------------------------------------------------------


class TestDefaultConstruction:
    def test_graph_state_default_user_input(self):
        state = GraphState()
        assert state.user_input == ""

    def test_graph_state_default_steps_empty(self):
        state = GraphState()
        assert state.steps == []

    def test_graph_state_sub_models_instantiated(self):
        state = GraphState()
        assert isinstance(state.schema, SchemaPipelineState)
        assert isinstance(state.query, QueryPipelineState)
        assert isinstance(state.memory, MemoryState)

    def test_schema_pipeline_defaults(self):
        s = SchemaPipelineState()
        assert s.ready is False
        assert s.metadata is None
        assert s.draft is None
        assert s.approved is None
        assert s.hitl_prompt is None
        assert s.persist_error is None

    def test_query_pipeline_defaults(self):
        q = QueryPipelineState()
        assert q.docs_context is None
        assert q.docs_warning is None
        assert q.plan is None
        assert q.generated_sql is None
        assert q.critic_status is None
        assert q.critic_feedback is None
        assert q.refinement_count == 0
        assert q.execution_result is None
        assert q.explanation is None

    def test_memory_state_defaults(self):
        m = MemoryState()
        assert m.preferences is None
        assert m.preferences_dirty is False
        assert m.conversation_history == []
        assert m.warning is None

    def test_conversation_turn_requires_user_input(self):
        t = ConversationTurn(user_input="test")
        assert t.user_input == "test"
        assert t.sql is None
        assert t.row_count is None
        assert t.rows_preview == []
        assert t.explanation is None


# ---------------------------------------------------------------------------
# Unset-field preservation through merge
# ---------------------------------------------------------------------------


class TestUnsetFieldPreservation:
    def test_merge_does_not_reset_refinement_count_when_not_in_update(self):
        """A partial update to query state must not reset refinement_count to 0."""
        current = QueryPipelineState(refinement_count=2, generated_sql="SELECT 1")
        updated = merge_submodel(current, {"critic_status": "rejected"})
        assert updated.refinement_count == 2

    def test_merge_memory_state_does_not_clear_preferences(self):
        current = MemoryState(
            preferences={"row_limit_hint": 10}, preferences_dirty=True
        )
        updated = merge_submodel(current, {"warning": "test"})
        assert updated.preferences == {"row_limit_hint": 10}
        assert updated.preferences_dirty is True

    def test_graph_state_field_assignment(self):
        state = GraphState(user_input="hello", user_id="user1")
        assert state.user_input == "hello"
        assert state.user_id == "user1"
        assert state.gate_decision is None
