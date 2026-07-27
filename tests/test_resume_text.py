from kageha.context.assembler import ContextAssembler
from kageha.loop.resume_text import (
    build_followup_prompt,
    is_resume_wrapper,
    unwrap_objective,
)
from kageha.models.base import ChatMessage, ToolSpec


def test_unwrap_nested_resume_objective():
    nested = (
        "Continue in this existing session workspace (run_id=abc).\n"
        "Original task: Continue in this existing session workspace (run_id=abc).\n"
        "Original task: Create beautiful presentation slides from inputs/slides.md\n"
        "Current goals:\n# Goal: ...\n"
        "User follow-up:\n??\n"
    )
    assert is_resume_wrapper(nested)
    obj = unwrap_objective(nested)
    assert "Create beautiful presentation slides" in obj
    assert "Continue in this" not in obj


def test_followup_prompt_stays_compact():
    huge = (
        "Continue in this existing session workspace (run_id=abc).\n"
        + ("Original task: Continue in this existing session workspace (run_id=abc).\n" * 80)
        + "Original task: Make a dog image\n"
        + "User follow-up:\n??\n"
    )
    prompt = build_followup_prompt(
        run_id="abc",
        message="create an image of a dog dancing in rain",
        original=huge,
        state_projection="Objective: Make a dog image",
        goal_md="# Goal\n- [ ] g1 done",
    )
    assert len(prompt) < 6000
    assert "dog dancing in rain" in prompt
    assert "Make a dog image" in prompt
    assert "Continue in this existing session" not in prompt.split("Original objective:", 1)[-1]


def test_assembler_keeps_truncated_user_message():
    asm = ContextAssembler()
    bloated = "Continue in this session\n" + ("x" * 200_000)
    out = asm.build(
        history=[ChatMessage(role="user", content=bloated)],
        tools=[ToolSpec(name="bash", description="run", parameters={})],
    )
    assert out.stats["history_messages"] == 1
    user_msgs = [m for m in out.messages if m.role == "user"]
    assert user_msgs
    assert len(user_msgs[0].content) < 100_000
