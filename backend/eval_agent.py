"""
Agent Evaluation Harness

Runs a fixed set of customer questions against a client's public chat endpoint
and checks each against expectations, so "is answering good?" is measurable and
re-checkable after every change (no more vibes / whack-a-mole regressions).

Usage:
    python eval_agent.py [slug] [base_url]
    e.g. python eval_agent.py nexus http://localhost:8000

Each case:
    q            : the customer message
    category     : grouping label
    searched     : expected used_retrieval (True/False/None=don't care)
    must_include : substrings the answer SHOULD contain (any-of groups as tuples)
    must_avoid   : substrings the answer should NOT contain
    history      : optional prior turns for multi-turn cases
"""

import sys
import json
import requests

SLUG = sys.argv[1] if len(sys.argv) > 1 else "nexus"
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8000"
URL = f"{BASE}/api/public/{SLUG}/chat"

CASES = [
    # --- small talk / off-topic: should NOT search, should NOT leak plan data ---
    {"q": "hi", "category": "smalltalk", "searched": False,
     "must_avoid": ["$", "GB", "plan id", "P-0"]},
    {"q": "thanks so much!", "category": "smalltalk", "searched": False,
     "must_avoid": ["$", "P-0"]},
    {"q": "will it rain tomorrow?", "category": "offtopic", "searched": False,
     "must_avoid": ["Senior 55", "P-0", "your plan"]},
    {"q": "who won the football match last night?", "category": "offtopic", "searched": False,
     "must_avoid": ["P-0", "your plan"]},

    # --- account-specific: must NOT claim to know the account; direct to app/human ---
    {"q": "what is my current plan?", "category": "account", "searched": None,
     "must_include": [("app", "human", "account", "can't", "cannot", "don't have")],
     "must_avoid": ["you're on the Senior", "your plan is", "P-008"]},
    {"q": "how much do I owe on my bill?", "category": "account", "searched": None,
     "must_include": [("app", "human", "account", "can't", "cannot", "don't have")],
     "must_avoid": ["you owe $", "your balance is $", "you owe a total"]},

    # --- factual (grounded): should search + contain the right facts ---
    {"q": "how much is the unlimited pro plan?", "category": "plans", "searched": True,
     "must_include": ["45"]},
    {"q": "do you have a plan for students?", "category": "plans", "searched": True,
     "must_include": [("student", "20")]},
    {"q": "how do I set up an eSIM?", "category": "sim", "searched": True,
     "must_include": [("qr", "app", "settings")]},
    {"q": "how do I replace a lost SIM?", "category": "sim", "searched": True,
     "must_include": [("id", "store", "fee", "5")]},
    {"q": "how does roaming work when I travel to Europe?", "category": "roaming", "searched": True,
     "must_include": [("day pass", "pass", "10")]},
    {"q": "what is the late payment fee?", "category": "billing", "searched": True,
     "must_include": ["7"]},
    {"q": "do you offer home internet?", "category": "network", "searched": True,
     "must_include": [("internet", "50", "broadband")]},
    {"q": "how do I block spam calls?", "category": "services", "searched": True,
     "must_include": [("call guard", "block", "spam")]},
    {"q": "what is your fair usage policy?", "category": "policy", "searched": True,
     "must_include": [("deprioritiz", "throttle", "50gb", "fair usage")]},
    {"q": "how do I cancel my service and is there a fee?", "category": "account", "searched": True,
     "must_include": [("cancel", "termination", "app", "611")]},
    {"q": "how much does device insurance cost?", "category": "device", "searched": True,
     "must_include": ["8"]},
    {"q": "how can I check how much data I have left?", "category": "services", "searched": True,
     "must_include": [("*111", "app", "usage")]},
    {"q": "what should I do if my phone is stolen?", "category": "account", "searched": True,
     "must_include": [("suspend", "line", "611", "app")]},
    {"q": "can I keep my number if I switch to Nexus?", "category": "account", "searched": True,
     "must_include": [("port", "keep", "transfer")]},
    {"q": "do you have 5G coverage?", "category": "network", "searched": True,
     "must_include": [("5g", "coverage", "map")]},

    # --- vague: should ask for clarification, not guess ---
    {"q": "I have a problem", "category": "vague", "searched": None,
     "must_include": [("?", "let me know", "which", "are you", "can you tell", "what specifically", "sorry to hear")]},
    {"q": "it's not working", "category": "vague", "searched": None,
     "must_include": [("?", "let me know", "which", "are you", "can you tell", "what specifically", "sorry to hear")]},

    # --- multi-turn context ---
    {"q": "what's the early termination fee?", "category": "multiturn", "searched": None,
     "history": [
         {"role": "user", "content": "tell me about unlimited pro"},
         {"role": "assistant", "content": "Unlimited Pro is $45/month on a 12-month contract with unlimited 5G and streaming."},
     ],
     "must_include": [("50", "termination", "$2")]},
]


def contains_any(text, group):
    return any(sub.lower() in text for sub in group)


def check(case, resp_text, searched):
    text = (resp_text or "").lower()
    problems = []

    exp = case.get("searched")
    if exp is not None and searched != exp:
        problems.append(f"expected searched={exp}, got {searched}")

    for req in case.get("must_include", []):
        group = req if isinstance(req, tuple) else (req,)
        if not contains_any(text, group):
            problems.append(f"missing any of {group}")

    for bad in case.get("must_avoid", []):
        if bad.lower() in text:
            problems.append(f"should not contain '{bad}'")

    return problems


def main():
    print(f"Evaluating '{SLUG}' at {URL}\n" + "=" * 70)
    passed = 0
    failed = []
    for i, case in enumerate(CASES, 1):
        payload = {"message": case["q"], "history": case.get("history", [])}
        try:
            r = requests.post(URL, json=payload, timeout=60)
            data = r.json()
        except Exception as e:
            print(f"[{i:02}] ERROR  {case['q'][:45]!r}: {e}")
            failed.append(case["q"])
            continue

        resp = data.get("response", data.get("detail", ""))
        searched = data.get("used_retrieval")
        problems = check(case, resp, searched)
        status = "PASS" if not problems else "FAIL"
        if not problems:
            passed += 1
        else:
            failed.append(case["q"])
        print(f"[{i:02}] {status} ({case['category']}) {case['q'][:50]!r}")
        if problems:
            for p in problems:
                print(f"        - {p}")
            print(f"        answer: {resp[:150]!r}")

    print("=" * 70)
    print(f"SCORE: {passed}/{len(CASES)} passed")
    if failed:
        print("Failed:", ", ".join(repr(q[:40]) for q in failed))


if __name__ == "__main__":
    main()
