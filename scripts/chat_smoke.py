"""Smoke test of the Week page's chat against REAL Claude Code (not in pytest: it costs a few cents and needs
Sam's login). Run it after any change to the chat (`week_chat.py`, the panel in `week_live.py`):

    python scripts/chat_smoke.py                  # haiku, low effort: cheap
    python scripts/chat_smoke.py --model opus --effort high

It drives the chat's own code (`ChatManager` + `SubprocessRunner`, exactly what `dfs week serve` runs) through
three turns of one chat and prints PASS or FAIL for each:

1. one turn: a short reply, with text, at least one turn and a cost;
2. a follow-up (`--resume`): it must remember turn 1 and come back with NEW text and a NEW cost (Week 9's
   "is it done?" came back with 0 turns, no text and the old cost);
3. a turn that runs a 30-second command: it must run in the FOREGROUND (the chat's Claude Code has no
   background commands), finish, and the turn must end only after it, with text.

It runs in a scratch directory (no repo files, no sheet, no `data/`), keeps its chat out of this week's list,
and approves only the one command step 3 asks for (anything else is denied). Exit code 0 when every step
passes.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dfs import week_chat  # noqa: E402

COMMAND = "sleep 30 && echo smoke-ok"


def run_turn(chat, text, settings, chat_id, *, allow=None, timeout=240.0):
    """One turn through the real chat code: (turn, its events, seconds it took)."""
    started = time.monotonic()
    turn = chat.send(text, settings, chat_id)
    events = []
    for event in chat.stream(turn, wait=2.0):
        if time.monotonic() - started > timeout:
            chat.stop()
            events.append({"k": "error", "text": f"smoke: no end after {timeout:.0f}s, stopped"})
            timeout = float("inf")
        if event is None:
            continue
        events.append(event)
        if event["k"] == "approval":
            ok = allow is not None and allow(event)
            chat.approve(turn, event["id"], ok, "" if ok else "The smoke test only approves its own command.")
    return turn, events, time.monotonic() - started


def text_of(events):
    return "".join(e["delta"] for e in events if e["k"] == "text").strip()


def done_of(events):
    return next((e for e in reversed(events) if e["k"] == "done"), {})


def check(name, ok, detail):
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--model", default="haiku", choices=week_chat.MODELS)
    parser.add_argument("--effort", default="low", choices=week_chat.EFFORTS)
    opts = parser.parse_args()
    settings = week_chat.Settings(opts.model, opts.effort)
    results = []
    with tempfile.TemporaryDirectory(prefix="chat-smoke-") as tmp:
        cwd, store = Path(tmp) / "cwd", Path(tmp) / "chats"
        cwd.mkdir()
        chat = week_chat.ChatManager(week_chat.SubprocessRunner(), cwd=cwd, store_dir=store)
        print(f"chat smoke: claude {opts.model} / {opts.effort}, scratch dir {cwd}", flush=True)

        # 1. one turn
        turn, events, secs = run_turn(chat, "Reply with exactly the one word: marmalade", settings, None)
        done1, text1 = done_of(events), text_of(events)
        results.append(
            check(
                "1 one turn",
                bool(turn.chat_id)
                and "marmalade" in text1.lower()
                and done1.get("ok") is True
                and (done1.get("turns") or 0) >= 1
                and "problem" not in done1,
                f"text={text1[:60]!r} turns={done1.get('turns')} cost={done1.get('cost')} "
                f"problem={done1.get('problem')} ({secs:.0f}s)",
            )
        )
        chat_id = turn.chat_id

        # 2. a follow-up on the same chat
        turn, events, secs = run_turn(
            chat, "Which word did you just reply with? Answer with that word only.", settings, chat_id
        )
        done2, text2 = done_of(events), text_of(events)
        new_cost = (done2.get("cost") or 0) > (done1.get("cost") or 0)
        results.append(
            check(
                "2 follow-up",
                turn.chat_id == chat_id
                and "marmalade" in text2.lower()
                and done2.get("ok") is True
                and (done2.get("turns") or 0) >= 1
                and new_cost
                and "problem" not in done2,
                f"text={text2[:60]!r} turns={done2.get('turns')} cost={done1.get('cost')} -> "
                f"{done2.get('cost')} problem={done2.get('problem')} ({secs:.0f}s)",
            )
        )

        # 3. a 30-second foreground command
        turn, events, secs = run_turn(
            chat,
            f"Use the Bash tool to run exactly this command: {COMMAND}\n"
            "Then reply with one short sentence saying what it printed.",
            settings,
            chat_id,
            allow=lambda ev: (
                COMMAND in ev.get("input", "") and "run_in_background" not in ev.get("input", "")
            ),
        )
        done3, text3 = done_of(events), text_of(events)
        tools = [e for e in events if e["k"] == "tool" and COMMAND in (e.get("summary") or "")]
        outputs = [e["text"] for e in events if e["k"] == "result"]
        background = any("background" in t.lower() for t in outputs)
        printed = any("smoke-ok" in t for t in outputs)
        results.append(
            check(
                "3 30s foreground command",
                bool(tools)
                and printed
                and not background
                and secs >= 30
                and done3.get("ok") is True
                and bool(text3)
                and "problem" not in done3,
                f"ran={bool(tools)} printed smoke-ok={printed} background={background} text={text3[:60]!r} "
                f"turns={done3.get('turns')} problem={done3.get('problem')} ({secs:.0f}s)",
            )
        )
    passed = sum(results)
    print(f"chat smoke: {passed}/{len(results)} passed", flush=True)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
