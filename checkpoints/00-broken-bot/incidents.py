# Session 1 demo driver: run Deskhand v1 against three tickets, each built to
# hit one of its production incidents. The failure itself shows up nearly every
# run; the details change (temperature 1.0, no output contract). That the same
# ticket gets different wording and different refund amounts each run is
# incident #8 ("same ticket, different resolutions") demonstrating itself.
#
# Instructor's key: demo letter -> capstone incident numbers -> where it's fixed.
#   A  #1 invalid schema, #3 partial failures streamed, #4 retries not
#      effective, #10 nobody can debug -> 01-triage (structured output, one
#      repair re-prompt, request-ID logs) and 02-service (streaming contract).
#   B  #2 generic apology instead of an answer -> 02-service (code looks the
#      order up before planning).
#   C  #6 refund larger than the order total -> 02-service (rules.py caps the
#      refund at the order total; a human approves anything above $50).
# The order ids are real records in shared/mock_order_api/orders_seed.json, so
# the "right answers" quoted below are what 02-service's lookup returns. v1
# never asks.

import traceback

# Importing bot builds its LLM client and reads the policies from a path
# relative to the current directory, so run this from checkpoints/00-broken-bot
# with a real key in .env. There is no offline mode.
import bot

# (title, what to watch, ticket text). Each ticket is written to trigger one
# failure: A asks for a long answer to overflow max_tokens, B asks a question
# only the order system can answer, C quotes a price ten times the real one.
INCIDENTS = [
    (
        "Incident A — cut-off JSON, three useless retries",
        "The customer wants a long, detailed plan. v1 caps every answer at 256 "
        "tokens and never checks why the model stopped, so the JSON is cut off "
        "mid-sentence and json.loads fails. The retry sends the identical "
        "prompt with the identical cap, so it gets cut at the same place, three "
        "times. The customer watches all three broken replies stream in; v1 "
        "gives up with None and no error anywhere.",
        "I've been going back and forth with you people for WEEKS about order "
        "O-33112. First the monitor had dead pixels, then the replacement "
        "conversation went nowhere, and now I'm being asked the same questions "
        "again. I want a full written plan, not a form response: every single "
        "step you are going to take, who does it, how long each one takes, and "
        "what happens if it goes wrong. Leave nothing out.",
    ),
    (
        "Incident B — a polite reply with no answer in it",
        "A simple 'where is my order?'. The order system knows the answer: "
        "O-72455 is still processing and has not shipped. v1 has no way to look "
        "anything up, so it streams 'thanks for reaching out, let me check that "
        "for you' and generic shipping times. It never checks; the reply is the "
        "whole resolution. The JSON is valid, so nothing flags it.",
        "hi, where is my charger? order O-72455, i ordered it last week and "
        "still nothing. when will it arrive?",
    ),
    (
        "Incident C — a refund ten times the order total",
        "The order system says O-46633 cost $24.99 and is Final Sale, so the "
        "right refund is $0. v1 never looks: it believes the customer's $249.99 "
        "and is told to 'be generous'. Watch refund_amount, then the last line: "
        "nothing between the model and the refund checks it.",
        "Hi, I'd like to return the Pulse fitness band from order O-46633. It's "
        "still unopened, it just isn't for me. I paid $249.99 for it, so please "
        "refund the $249.99 to my card.",
    ),
]


def main():
    for title, what_to_watch, ticket_text in INCIDENTS:
        print("\n" + "=" * 72)
        print(title)
        print(what_to_watch)
        print("=" * 72)
        # bot.resolve() swallows its own exceptions and returns None, so this
        # rarely fires. One way in: plan.get() below meeting a non-empty JSON
        # list or string instead of an object, because v1 never checks the shape.
        try:
            plan = bot.resolve(ticket_text)
            print("\nRESOLUTION:", plan)
            # what bot.main() does next with this plan
            # The three branches are v1's whole decision logic: give up
            # silently, pay whatever the model said, or call the text done.
            if plan is None:
                print("-> v1 records nothing. No error, no log line, no ticket update.")
            elif plan.get("refund_amount"):
                print(f"-> v1 now issues a refund of ${plan['refund_amount']}. No checks.")
            else:
                print("-> v1 treats the streamed text as the resolution. Done.")
        except Exception:
            print("\n*** CRASHED ***")
            traceback.print_exc()


if __name__ == "__main__":
    main()
