"""Plain-English explanations for findings.

Every finding already carries a human-written explanation from the check
functions. This module optionally asks an LLM to rewrite that explanation
in a shorter, friendlier form — but only when an OpenAI API key is set.
The monitor works fully without it; this is a convenience, not a
dependency.
"""

from __future__ import annotations

import json
import os
import urllib.request

from .checks import Finding

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def enhance_explanation(finding: Finding) -> str:
    """Return a friendlier version of ``finding.explanation`` if possible.

    Returns the original explanation unchanged when no API key is set,
    when the request fails, or when the ``openai`` package is not needed —
    this uses only the standard library, so nothing extra is installed.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return finding.explanation

    prompt = (
        "Rewrite this data-pipeline alert for a non-technical manager. "
        "Keep it to two short sentences, keep every number, and do not "
        "add facts that are not in the text.\n\n"
        f"Alert: {finding.summary}\n{finding.explanation}"
    )
    payload = json.dumps(
        {
            "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 200,
        }
    ).encode("utf-8")

    try:
        request = urllib.request.Request(
            _OPENAI_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode("utf-8"))
        text = body["choices"][0]["message"]["content"].strip()
        return text or finding.explanation
    except Exception:
        # Any failure (no network, bad key, API error) falls back silently.
        return finding.explanation
