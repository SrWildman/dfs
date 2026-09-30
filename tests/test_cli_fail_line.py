from io import StringIO

from rich.console import Console

from dfs.cli import _fail_line


def _render(line: str) -> str:
    out = StringIO()
    Console(file=out, force_terminal=False, width=200).print(line)
    return out.getvalue().strip()


def test_the_check_tag_survives_rich_markup():
    assert _render(_fail_line("formula-ranges", "'Results' column G: row(s) 5 have no formula")) == (
        "FAIL [formula-ranges] 'Results' column G: row(s) 5 have no formula"
    )


def test_brackets_inside_the_detail_are_shown_literally_too():
    assert (
        _render(_fail_line("Board", "header [red] was not found"))
        == "FAIL [Board] header [red] was not found"
    )
