# Deskhand v1 — our AI support bot!
# Wrote this in an afternoon, works great most of the time.
# TODO: sometimes crashes, look into it later

import glob
import json
import os
import sys

from dotenv import load_dotenv
from openai import AzureOpenAI, OpenAI

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

# copied the provider setup off a blog post, works on my machine
if os.getenv("LLM_PROVIDER", "azure").lower() == "azure":
    client = AzureOpenAI(
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
    )
    MODEL = os.getenv("AZURE_OPENAI_DEPLOYMENT")  # azure wants the deployment name
else:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def load_all_policies():
    # just grab everything, more context = smarter bot right?
    text = ""
    for path in glob.glob("../../shared/policies/*.md"):
        text += open(path, encoding="utf-8").read() + "\n\n"
    return text


POLICIES = load_all_policies()


def resolve(ticket_text):
    prompt = f"""You are Deskhand, the support bot for Nimbus Electronics.
Here are ALL of our company policies:

{POLICIES}

Here is a customer ticket:

{ticket_text}

Figure out what to do and respond with JSON with these keys:
category, refund_amount, customer_reply.
Be generous, we want happy customers!"""

    attempts = 0
    while True:  # keep trying until it works :)
        try:
            stream = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=1.0,  # creative answers!
                max_tokens=256,  # keep the bill down
                stream=True,
            )
            raw = ""
            print("--- streaming to customer ---")
            for chunk in stream:
                if not chunk.choices:  # azure sends an empty chunk first, skip it
                    continue
                delta = chunk.choices[0].delta.content or ""
                print(delta, end="", flush=True)  # customer sees everything, live
                raw += delta
            print("\n--- end of stream ---")
            # it kept wrapping the JSON in ```json fences, so strip them. fixed!
            raw = raw.strip().removeprefix("```json").removesuffix("```")
            plan = json.loads(raw)
            return plan
        except Exception:
            attempts += 1
            print(f"\nhmm, something went wrong, retrying... (attempt {attempts})")
            if attempts >= 3:
                print("ok giving up, probably fine")
                return None


def main():
    if len(sys.argv) > 1:
        ticket_text = " ".join(sys.argv[1:])
    else:
        ticket_text = (
            "I ordered the wireless headphones two weeks ago, order #48291. "
            "They arrived with a cracked case. I want a refund or a replacement."
        )
    plan = resolve(ticket_text)
    print("\nRESOLUTION:", plan)
    if plan and plan.get("refund_amount"):
        print(f"issuing refund of ${plan['refund_amount']}...")  # no checks, YOLO
        print("refund issued!")


if __name__ == "__main__":
    main()
