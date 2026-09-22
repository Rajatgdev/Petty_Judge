"""
Complaint summariser (OpenAI) for Petty Judge.

The ONLY place an LLM is used. Jev handles every decision; this just turns the
plaintiff's complaint into one plain sentence so the defendant knows what
they're accused of before they respond. Quarantined here so the Jev path stays
pure and a summary failure can never block a verdict.

Set OPENAI_API_KEY in the backend env. If it's missing or the call fails, we
fall back to a neutral line — the app keeps working, the defendant just gets a
generic heads-up instead of a specific one.
"""

import os
import httpx

OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
SUMMARY_MODEL = os.environ.get("SUMMARY_MODEL", "gpt-4o-mini")
FALLBACK = "You've been accused in a petty dispute. Read the room and state your side."


async def summarise_complaint(plaintiff_name: str, complaint: str) -> str:
    """One neutral sentence telling the defendant what the dispute is about.
    Never raises — returns the fallback on any problem."""
    text = (complaint or "").strip()
    if not text or not OPENAI_KEY:
        return FALLBACK

    system = (
        "You summarise a petty interpersonal complaint in ONE short, neutral "
        "sentence (max 25 words) so the accused knows the topic. Do not take "
        "sides, do not judge, do not name a winner. Address the accused as 'You'. "
        "Keep it light and plain. No preamble, just the sentence."
    )
    user = f"The complaint (from {plaintiff_name}):\n\n{text[:1200]}"

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
                    "max_tokens": 60,
                    "temperature": 0.4,
                },
            )
        if r.status_code == 200:
            out = r.json()["choices"][0]["message"]["content"].strip()
            return out or FALLBACK
        return FALLBACK
    except Exception:
        return FALLBACK