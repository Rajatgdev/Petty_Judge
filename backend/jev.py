"""
Jev (TypeSafe System One) client for Petty Judge.

Everything that talks to the Jev API lives here. The rest of the app calls
one function: judge_case(plaintiff, defendant). If TypeSafe ever changes the
request/response shape, this is the only file you touch.

Contract (from docs.typesafe.ai/api, confirmed 2026-09-22):
  POST https://api.typesafe.ai/v1/systemone
  Authorization: Bearer <key>
  body: { "state": <text|obj>, "model": "jev-latest", "questions": { id: {...} } }

  Answers come back keyed by our question IDs:
    noul   -> {"type":"noul","noul": 0..1}                (NO confidence field)
    choice -> {"type":"choice","choice": str, "probabilities": {...}, "confidence": 0..1}
    score  -> {"type":"score","score": float, "legend": {...}, "probabilities": {...}, "confidence": 0..1}

Set MOCK=true in the environment to run the whole app with fake verdicts and
no API key (useful for UI work / offline). Otherwise set TYPESAFE_API_KEY.
"""

import os
import random
import httpx

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = os.environ.get("JEV_MODEL", "jev-latest")
API_KEY = os.environ.get("TYPESAFE_API_KEY", "")
MOCK = os.environ.get("MOCK", "").lower() in ("1", "true", "yes")

# The charges. Each is a Noul (yes/no probability). A charge "sticks" only if
# its probability clears CHARGE_THRESHOLD, so every case files a different sheet.
# id -> (question text, courtroom charge label)
CHARGES = {
    "passive_aggression": (
        "Is this person being passive-aggressive rather than saying what they mean?",
        "Passive Aggression in the First Degree",
    ),
    "grudge": (
        "Is this person holding onto an old grievance instead of the matter at hand?",
        "Unlawful Possession of a Grudge",
    ),
    "weaponized_incompetence": (
        "Is this person pretending to be bad at something to avoid doing it?",
        "Weaponized Incompetence",
    ),
    "revisionist_history": (
        "Is this person misremembering or rewriting what actually happened in their favor?",
        "Tampering with the Historical Record",
    ),
    "overreaction": (
        "Is this person's reaction much bigger than the actual problem warrants?",
        "Reckless Overreaction",
    ),
    "petty_scorekeeping": (
        "Is this person keeping a running tally of the other's past faults?",
        "Operating an Unlicensed Scoreboard",
    ),
}

CHARGE_THRESHOLD = 0.55

# Short labels for the verdict card, one per pettiness level (indices match the
# Score criteria order below). The model never sees these — they're display only.
PETTINESS_LABELS = ["Fair", "A little petty", "Quite petty", "World-class petty"]

# The absurd-but-fair sentences. Jev picks one via a Choice.
SENTENCES = {
    "dishes_week": "The guilty party shall do the dishes, unassisted, for seven days.",
    "mutual_snack": "Both parties owe each other one apology and one snack of the other's choosing.",
    "dismissed_exhausting": "Case dismissed. You are both exhausting and the court needs a lie-down.",
    "public_apology": "The guilty party shall issue an apology in the group chat, no emojis permitted.",
    "chore_swap": "The guilty party inherits the other's most-hated chore for two weeks.",
}


def _build_questions(plaintiff_name, defendant_name):
    """Assemble all questions for ONE batched call. Every question sees the
    same state and is evaluated in parallel — this is the whole point of Jev."""
    q = {}

    # --- Safety guard (same call, ~free). If either trips, we dismiss. ---
    q["hazard_abusive"] = {
        "type": "noul",
        "instructions": "Does either statement contain slurs, threats, sexual content, or targeted harassment?",
        "criteria": {
            "true": "Contains abusive, hateful, threatening, or sexual content",
            "false": "Ordinary interpersonal dispute, even if heated or rude",
        },
    }
    q["hazard_not_a_dispute"] = {
        "type": "noul",
        "instructions": "Are these two statements NOT a genuine interpersonal dispute between two people (e.g. spam, gibberish, empty, or a test)?",
        "criteria": {
            "true": "Not a real dispute: gibberish, spam, empty, or nonsense",
            "false": "A real disagreement between two people",
        },
    }

    # --- The verdict: who is more in the wrong? (Choice) ---
    q["verdict"] = {
        "type": "choice",
        "instructions": (
            f"Two people are in a petty dispute. '{plaintiff_name}' is the plaintiff, "
            f"'{defendant_name}' is the defendant. Weighing both statements, who is more in the wrong?"
        ),
        "criteria": {
            "plaintiff": f"{plaintiff_name} (the plaintiff) is more in the wrong",
            "defendant": f"{defendant_name} (the defendant) is more in the wrong",
            "both_equally": "Both are equally in the wrong",
        },
    }

    # --- Pettiness, scored per person independently (two Scores) ---
    for who, name in (("plaintiff", plaintiff_name), ("defendant", defendant_name)):
        # Levels describe concrete SITUATIONS, not degrees. The model judges
        # each level independently against the text, so "a little petty" gives
        # it nothing to match; a described situation does. (per docs.typesafe.ai
        # /primitives/score: "Describe situations, not degrees.")
        q[f"pettiness_{who}"] = {
            "type": "score",
            "instructions": f"Rate how petty {name} is being, based only on their statement.",
            "criteria": [
                "Raises a fair, proportionate concern and sticks to the actual issue",
                "Mostly reasonable but takes a small dig or exaggerates a little",
                "Fixates on a minor slight, keeps score, or makes it personal",
                "Escalates a trivial matter into a grievance far bigger than it warrants",
            ],
        }

    # --- Charges, per person (batch of Nouls) ---
    for who, name in (("plaintiff", plaintiff_name), ("defendant", defendant_name)):
        for charge_id, (question, _label) in CHARGES.items():
            q[f"charge_{who}_{charge_id}"] = {
                "type": "noul",
                "instructions": f"Regarding {name}: {question}",
            }

    # --- The sentence (Choice) ---
    q["sentence"] = {
        "type": "choice",
        "instructions": "Given this dispute, which ruling is the most fitting and fair punishment?",
        "criteria": {k: v for k, v in SENTENCES.items()},
    }

    return q


def _transcript_text(transcript, plaintiff_name, defendant_name):
    """Render the running transcript as plain text for Jev's state."""
    lines = []
    for m in transcript:
        who = plaintiff_name if m["seat"] == "plaintiff" else defendant_name
        lines.append(f"{who}: {m['text']}")
    return "\n".join(lines)


async def score_round(transcript, plaintiff_name, defendant_name):
    """
    Lightweight 'who is winning right now' after each message. ONE Choice, cheap,
    called every turn to drive the live jury bar. Returns:
      {"leaning": "plaintiff"|"defendant"|"even", "plaintiff_pct": int, "defendant_pct": int}
    plaintiff_pct + defendant_pct = 100. Never raises; on failure returns an even split.
    """
    state = {
        "dispute": _transcript_text(transcript, plaintiff_name, defendant_name),
        "plaintiff": plaintiff_name,
        "defendant": defendant_name,
    }
    questions = {
        "leaning": {
            "type": "choice",
            "instructions": (
                f"Based on the argument so far, which side is currently more convincing "
                f"and sympathetic? '{plaintiff_name}' is the plaintiff, '{defendant_name}' "
                f"is the defendant."
            ),
            "criteria": {
                "plaintiff": f"{plaintiff_name} is currently more convincing",
                "defendant": f"{defendant_name} is currently more convincing",
                "even": "It is roughly even between them",
            },
        },
    }
    try:
        answers = _mock_answers(questions) if MOCK else await _call_jev(state, questions)
        c = answers["leaning"]
        probs = c.get("probabilities", {})
        # Map the three-way distribution to a two-sided bar. 'even' mass splits evenly.
        p = probs.get("plaintiff", 0.0) + probs.get("even", 0.0) / 2
        d = probs.get("defendant", 0.0) + probs.get("even", 0.0) / 2
        total = p + d or 1.0
        p_pct = round(100 * p / total)
        return {
            "leaning": c["choice"],
            "plaintiff_pct": p_pct,
            "defendant_pct": 100 - p_pct,
        }
    except Exception:
        return {"leaning": "even", "plaintiff_pct": 50, "defendant_pct": 50}


async def _call_jev(state, questions):
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"state": state, "model": MODEL, "questions": questions}
    # Retry transient failures (429 / 529 / 5xx) with capped backoff + jitter.
    last_exc = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(API_URL, json=payload, headers=headers)
            if r.status_code == 200:
                return r.json()["answers"]
            if r.status_code in (429, 529) or r.status_code >= 500:
                last_exc = RuntimeError(f"Jev transient {r.status_code}: {r.text[:200]}")
            else:
                # 4xx we don't retry — it won't fix itself.
                raise RuntimeError(f"Jev error {r.status_code}: {r.text[:300]}")
        except httpx.RequestError as e:
            last_exc = e
        await _sleep_backoff(attempt)
    raise last_exc or RuntimeError("Jev call failed")


async def _sleep_backoff(attempt):
    import asyncio
    await asyncio.sleep((0.4 * (2 ** attempt)) + random.uniform(0, 0.3))


def _mock_answers(questions):
    """Fake but well-shaped answers so the app runs with no key."""
    ans = {}
    for qid, spec in questions.items():
        t = spec["type"]
        if t == "noul":
            # hazards low, charges random
            ans[qid] = {"type": "noul", "noul": 0.05 if qid.startswith("hazard") else round(random.random(), 2)}
        elif t == "choice":
            opts = list(spec["criteria"].keys())
            weights = [random.random() for _ in opts]
            s = sum(weights)
            probs = {o: round(w / s, 3) for o, w in zip(opts, weights)}
            pick = max(probs, key=probs.get)
            ans[qid] = {"type": "choice", "choice": pick, "probabilities": probs,
                        "confidence": round(random.uniform(0.55, 0.95), 2)}
        elif t == "score":
            levels = spec["criteria"]
            n = len(levels)
            score = round(random.uniform(0.5, n - 1), 2)
            ans[qid] = {"type": "score", "score": score,
                        "legend": {str(i): l for i, l in enumerate(levels)},
                        "probabilities": {str(i): round(1 / n, 3) for i in range(n)},
                        "confidence": round(random.uniform(0.55, 0.95), 2)}
    return ans


async def judge_case(plaintiff, defendant, transcript=None):
    """
    plaintiff, defendant: dicts with 'name' and 'case' (their opening statement).
    transcript: optional list of {"seat","text"} — the full back-and-forth. When
    present, each side's combined remarks are judged, not just the opener.
    Returns a fully-assembled verdict dict, or a 'dismissed' dict if the guard trips.
    """
    # Fold the whole conversation into each side's statement so the existing
    # question set judges the full argument, not just the opener.
    if transcript:
        p_lines = [m["text"] for m in transcript if m["seat"] == "plaintiff"]
        d_lines = [m["text"] for m in transcript if m["seat"] == "defendant"]
        p_case = "\n".join(p_lines) or plaintiff["case"]
        d_case = "\n".join(d_lines) or defendant["case"]
    else:
        p_case, d_case = plaintiff["case"], defendant["case"]

    questions = _build_questions(plaintiff["name"], defendant["name"])
    state = {
        "plaintiff": {"name": plaintiff["name"], "statement": p_case},
        "defendant": {"name": defendant["name"], "statement": d_case},
    }

    answers = _mock_answers(questions) if MOCK else await _call_jev(state, questions)
    return _assemble_verdict(answers, plaintiff["name"], defendant["name"])


def _assemble_verdict(a, plaintiff_name, defendant_name):
    # Safety gate first.
    if a["hazard_abusive"]["noul"] >= 0.5 or a["hazard_not_a_dispute"]["noul"] >= 0.5:
        return {"dismissed": True,
                "reason": "The court declines to hear this case. Bring a real, civil dispute."}

    verdict_choice = a["verdict"]
    winner = verdict_choice["choice"]  # 'plaintiff' | 'defendant' | 'both_equally'
    jury_confidence = verdict_choice["confidence"]

    def pettiness(who):
        s = a[f"pettiness_{who}"]
        n_levels = len(s["legend"])
        nearest = min(round(s["score"]), n_levels - 1)
        return {
            "score": s["score"],
            "pct": round(100 * s["score"] / (n_levels - 1)),
            "label": PETTINESS_LABELS[nearest],
            "confidence": s["confidence"],
        }

    def charges_for(who):
        out = []
        for charge_id, (_q, label) in CHARGES.items():
            p = a[f"charge_{who}_{charge_id}"]["noul"]
            if p >= CHARGE_THRESHOLD:
                out.append({"label": label, "probability": round(p, 2)})
        out.sort(key=lambda c: c["probability"], reverse=True)
        return out

    p_pet = pettiness("plaintiff")
    d_pet = pettiness("defendant")

    verdict_line = {
        "plaintiff": f"{plaintiff_name} is GUILTY",
        "defendant": f"{defendant_name} is GUILTY",
        "both_equally": "BOTH PARTIES GUILTY",
    }[winner]

    # Petty damages: derived from the pettiness gap, made deliberately silly.
    gap = abs(p_pet["pct"] - d_pet["pct"])
    damages = 5 + (gap // 2)

    return {
        "dismissed": False,
        "verdict_line": verdict_line,
        "winner": winner,
        "jury_confidence": round(jury_confidence * 100),
        "plaintiff": {"name": plaintiff_name, "pettiness": p_pet, "charges": charges_for("plaintiff")},
        "defendant": {"name": defendant_name, "pettiness": d_pet, "charges": charges_for("defendant")},
        "sentence": SENTENCES[a["sentence"]["choice"]],
        "damages": damages,
    }
