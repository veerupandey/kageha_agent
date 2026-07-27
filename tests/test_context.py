from kageha.context.assembler import ContextAssembler
from kageha.context.budget import estimate_tokens, truncate_to_tokens
from kageha.models.base import ChatMessage, ToolSpec


def test_stable_prefix_order():
    asm = ContextAssembler(
        skill_catalog="- web_research: research",
        kb_pins="manuals",
        working_notes="note",
    )
    tools = [ToolSpec(name="bash", description="run shell", parameters={"type": "object"})]
    hist = [ChatMessage(role="user", content="hi")]
    out = asm.build(history=hist, tools=tools)
    system = out.messages[0].content
    assert system.index("## Tools") < system.index("## Skills catalog")
    assert system.index("## Skills catalog") < system.index("## Knowledge bases")
    assert out.prefix_tokens > 0


def test_working_notes_trail_history_not_system():
    """Working memory must not mutate the cacheable system prefix."""
    asm = ContextAssembler(
        skill_catalog="- web_research: research",
        kb_pins="manuals",
        working_notes="TaskState: step 3\n# Goal\n- [ ] ship it",
    )
    tools = [ToolSpec(name="bash", description="run shell", parameters={"type": "object"})]
    hist = [
        ChatMessage(role="user", content="hi"),
        ChatMessage(role="assistant", content="ok"),
    ]
    out = asm.build(history=hist, tools=tools)

    system = out.messages[0]
    assert system.role == "system"
    assert "## Working memory" not in (system.content or "")
    assert "TaskState: step 3" not in (system.content or "")

    # Order: system → history → working
    assert out.messages[1].role == "user"
    assert out.messages[1].content == "hi"
    assert out.messages[2].role == "assistant"
    working = out.messages[-1]
    assert working.role == "user"
    assert working.content.startswith("## Working memory\n")
    assert "TaskState: step 3" in working.content
    assert out.stats["working_tokens"] > 0

    # Prefix must stay identical when only working notes change
    asm.working_notes = "TaskState: step 4\n# Goal\n- [x] ship it"
    out2 = asm.build(history=hist, tools=tools)
    assert out2.messages[0].content == system.content
    assert out2.prefix_tokens == out.prefix_tokens
    assert "step 4" in out2.messages[-1].content


def test_working_notes_respect_budget():
    asm = ContextAssembler(working_notes="x" * 50_000)
    asm.budget.working = 50
    out = asm.build(
        history=[ChatMessage(role="user", content="hi")],
        tools=[ToolSpec(name="bash", description="run", parameters={})],
    )
    working = out.messages[-1].content
    assert estimate_tokens(working) <= 80
    assert "[compacted]" in working


def test_truncate():
    text = "a" * 1000
    out = truncate_to_tokens(text, 50)
    assert estimate_tokens(out) <= 60
    assert "[compacted]" in out
