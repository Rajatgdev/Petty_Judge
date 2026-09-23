"""
Complaint summariser (OpenAI) for Petty Judge.

The ONLY place an LLM is used. Jev handles every decision; this just turns the
plaintiff's complaint into a short brief so the defendant knows what they're
accused of before they respond. Quarantined here so the Jev path stays pure and
a summary failure can never block a verdict.

Returns a dict: {"topic": str, "accusation": str}
  topic      — a 2-5 word case title, e.g. "The Missing Leftovers"
  accusation — one neutral sentence telling the defendant what they're accused of

Set OPENAI_API_KEY in the backend env. If it's missing or the call fails, we
fall back to neutral text — the app keeps working.
"""

import json
import os
import httpx

OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
SUMMARY_MODEL = os.environ.get("SUMMARY_MODEL", "gpt-4o-mini")

FALLBACK = {
    "topic": "A Petty Dispute",
    "accusation": "You've been accused in a petty dispute. Read the room and state your side.",
}


async def summarise_complaint(plaintiff_name: str, complaint: str) -> dict:
    """Topic + accusation for the defendant. Never raises — returns FALLBACK on any problem."""
    text = (complaint or "").strip()
    if not text or not OPENAI_KEY:
        return dict(FALLBACK)

    system = (
        "You brief the accused in a playful small-claims court. Given a complaint, "
        "return STRICT JSON with two keys and nothing else:\n"
        '  "topic": a 2-5 word case title in title case (e.g. "The Missing Leftovers")\n'
        '  "accusation": ONE short neutral sentence (max 25 words) telling the accused '
        "what they're accused of, addressed as 'You'. Do not take sides or judge.\n"
        "Return only the JSON object."
    )
    user = f"Complaint from {plaintiff_name}:\n\n{text[:1200]}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": SUMMARY_MODEL,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "max_tokens": 120,
                    "temperature": 0.5,
                    "response_format": {"type": "json_object"},
                },
            )
        if r.status_code != 200:
            return dict(FALLBACK)
        raw = r.json()["choices"][0]["message"]["content"].strip()
        data = json.loads(raw)
        topic = str(data.get("topic", "")).strip() or FALLBACK["topic"]
        accusation = str(data.get("accusation", "")).strip() or FALLBACK["accusation"]
        return {"topic": topic[:60], "accusation": accusation[:300]}
    except Exception:
        return dict(FALLBACK)