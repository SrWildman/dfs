"""`docs/COMMANDS.md` is generated from the CLI: current, complete, and leading with the standard week."""

from dfs import commands_doc


def test_the_checked_in_reference_matches_what_the_cli_generates():
    # Failure means a command or option changed: run `python -m dfs.commands_doc` and commit the result.
    assert commands_doc.COMMANDS_DOC.read_text() == commands_doc.render()


def test_every_visible_command_is_placed_in_a_section_and_no_section_names_a_missing_command():
    commands = commands_doc.all_commands()
    assert commands_doc.unassigned(commands) == [], "new command: add it to commands_doc.SECTIONS"
    assert commands_doc.stale_entries(commands) == [], "renamed/removed command still listed in SECTIONS"


def test_the_standard_week_only_names_commands_that_exist():
    commands = commands_doc.all_commands()
    named = commands_doc.standard_week_commands()
    assert named, "the standard week must name real commands"
    assert [n for n in named if n not in commands] == []
    # The six steps people actually run, in order.
    assert named[0] == "dfs week new" and "dfs export" in named and named[-1] == "dfs week close"


def test_the_standard_week_comes_before_every_reference_section():
    text = commands_doc.render()
    standard = text.index("## Your standard week")
    assert standard < text.index("## Global options") < text.index("## Also used most weeks")
    for heading in [line for line in text.splitlines() if line.startswith("## Reference")]:
        assert standard < text.index(heading)
    # Reference sections say so in their titles, so nobody reads them first by mistake.
    assert all("only when needed" in t for t, _b, _c in commands_doc.SECTIONS[1:])


def test_every_data_source_appears_in_the_sources_table():
    from dfs.sources import SOURCES

    text = commands_doc.render()
    table = text[text.index("## Data sources") :]
    assert all(f"`{name}`" in table for name in SOURCES)
    assert "`usage`" in table and "`pbp`" in table
