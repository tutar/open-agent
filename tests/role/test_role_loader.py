from pathlib import Path

from openagent.role import load_default_role_definition, load_role_definition


def test_load_role_definition_reads_frontmatter_user_and_memory_paths(tmp_path: Path) -> None:
    role_root = tmp_path / "roles" / "research"
    (role_root / "memory").mkdir(parents=True)
    (role_root / "ROLE.md").write_text(
        "---\n"
        "role_id: research\n"
        "recommended_models: [gpt-5.4, claude-sonnet]\n"
        "skills:\n"
        "  - summarize\n"
        "mcps:\n"
        "  - docs\n"
        "---\n"
        "Role metadata only.\n",
        encoding="utf-8",
    )
    (role_root / "USER.md").write_text("You are the research role.\n", encoding="utf-8")

    role = load_role_definition(str(tmp_path), "research")

    assert role.role_id == "research"
    assert role.user_markdown_body == "You are the research role.\n"
    assert role.capabilities.recommended_models == ["gpt-5.4", "claude-sonnet"]
    assert [item.skill_id for item in role.capabilities.skills] == ["summarize"]
    assert [item.server_id for item in role.capabilities.mcps] == ["docs"]
    assert role.memory is not None
    assert Path(role.memory.records_root) == (role_root / "memory" / "records")


def test_default_role_falls_back_when_role_assets_are_absent(tmp_path: Path) -> None:
    role = load_default_role_definition(str(tmp_path))

    assert role.role_id == "default"
    assert role.user_markdown_body == ""
    assert role.memory is not None
    assert Path(role.memory.records_root) == (
        tmp_path / "roles" / "default" / "memory" / "records"
    )
