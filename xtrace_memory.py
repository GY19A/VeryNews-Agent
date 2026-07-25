"""
xtrace_memory.py
A minimal XTrace Memory client for VeryNews.

This is a deliberately small reference implementation: four HTTP calls that map
to the four stages of the XTrace Agent Loop. It shows how the memory layer is
wired into a verification pipeline without the tuning, filtering and cost
controls that a production deployment needs.

    recall()   -> POST /v1/memories/search    recall before acting
    lessons()  -> POST /v1/memories/trigger   act with context (quota-free)
    remember() -> POST /v1/memories           save what changed
    usage()    -> GET  /v1/usage              observe what accumulated

Design note: every method degrades to a neutral value on failure. Memory
enriches a verification; it must never be able to break one.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

BASE_URL = "https://api.production.xtrace.ai"

# Scopes the lessons/procedures this app learns.
NAMESPACE = "verynews/factcheck"


class XTraceMemory:
    """Thin client over the XTrace Memory HTTP API.

    There is no official Python SDK, so this wraps ``requests`` directly.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        user_id: str = "verynews",
        timeout: int = 45,
    ) -> None:
        self.api_key = (api_key or os.environ.get("XTRACE", "")).strip().strip('"')
        self.user_id = user_id
        self.timeout = timeout
        self.enabled = bool(self.api_key)

    # ------------------------------------------------------------------ #

    def _post(self, path: str, payload: dict, params: Optional[dict] = None) -> dict:
        response = requests.post(
            f"{BASE_URL}{path}",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------ #
    # 1. Recall before acting
    # ------------------------------------------------------------------ #

    def recall(self, claim: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search prior findings related to this claim.

        ``mode="retrieve"`` returns the raw ranked rows. The alternative,
        ``compose``, costs an extra server-side LLM round-trip to assemble a
        prompt block; here the context is rendered locally instead.

        Note that vector search always returns its nearest neighbours, however
        far away they are. A production caller should filter these by score
        before letting them near a prompt.
        """
        if not self.enabled or not claim.strip():
            return []
        try:
            data = self._post(
                "/v1/memories/search",
                {
                    "query": claim[:4000],
                    "user_id": self.user_id,
                    "limit": limit,
                    "mode": "retrieve",
                },
            )
        except requests.RequestException:
            return []

        return [
            {
                "text": row.get("text", ""),
                "type": row.get("type"),
                "score": row.get("score"),
            }
            for row in (data.get("data") or [])
            if row.get("text")
        ]

    # ------------------------------------------------------------------ #
    # 2. Act with context
    # ------------------------------------------------------------------ #

    def lessons(self, task: str, entities: Optional[List[str]] = None) -> List[str]:
        """Recall procedural lessons before running retrieval.

        Uses the pre-tool-call ``trigger`` hook, which fires on symbol
        tripwires rather than vector similarity — and is exempt from the
        monthly quota, so it is cheap to call on every run.
        """
        if not self.enabled:
            return []
        payload: Dict[str, Any] = {
            "user_id": self.user_id,
            "task": task[:2000],
            "namespace": NAMESPACE,
        }
        if entities:
            payload["entities"] = entities[:20]
        try:
            data = self._post("/v1/memories/trigger", payload)
        except requests.RequestException:
            return []
        return [row["text"] for row in (data.get("data") or []) if row.get("text")]

    # ------------------------------------------------------------------ #
    # 3. Save what changed
    # ------------------------------------------------------------------ #

    def remember(self, claim: str, verdict: str, reason: str) -> Dict[str, Any]:
        """Persist the outcome of a verification.

        The result is written as a user/assistant exchange so the extraction
        pipeline sees the claim and its adjudication together. ``wait=true``
        holds the connection until extraction finishes (up to ~30s), which
        keeps a demo synchronous; production callers poll the job instead.
        """
        if not self.enabled:
            return {"stored": False}
        try:
            data = self._post(
                "/v1/memories",
                {
                    "messages": [
                        {"role": "user", "content": f"Fact-check this claim: {claim[:3000]}"},
                        {
                            "role": "assistant",
                            "content": f"Verdict: {verdict}. {reason[:1500]}",
                        },
                    ],
                    "user_id": self.user_id,
                    "conv_id": f"verynews_{abs(hash(claim)) % 10**10}",
                    "namespace": NAMESPACE,
                },
                params={"wait": "true"},
            )
        except requests.RequestException:
            return {"stored": False}

        result = data.get("result") or {}
        return {
            "stored": data.get("status") == "succeeded",
            "created": len(result.get("memories_created") or []),
            # Contradicting an existing fact retires the old one; this is how
            # the store stays correct as a story develops.
            "superseded": len(result.get("memories_superseded_by") or {}),
        }

    # ------------------------------------------------------------------ #
    # 4. Observe what accumulated
    # ------------------------------------------------------------------ #

    def usage(self) -> Dict[str, Any]:
        """Current period totals — makes compounding measurable."""
        if not self.enabled:
            return {}
        try:
            response = requests.get(
                f"{BASE_URL}/v1/usage",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException:
            return {}
        return {
            "memories_active": (data.get("storage") or {}).get("memories_active"),
            "messages_ingested": (data.get("operations") or {}).get("messages_ingested"),
            "searches": (data.get("operations") or {}).get("searches"),
        }


def render_context(memories: List[Dict[str, Any]]) -> str:
    """Format recalled memories for prompt injection.

    Scores are shown deliberately, and the framing is explicit: these are prior
    findings to corroborate against, not freshly retrieved evidence. Without
    that instruction a model will happily treat a remembered claim as proof.
    """
    if not memories:
        return ""
    lines = [
        "## Prior verified knowledge (XTrace memory)",
        "",
        "These were established by earlier runs of this system. Treat them as "
        "corroboration to cross-check, not as new evidence. Where they conflict "
        "with today's sources, prefer today's sources and say so.",
        "",
    ]
    for item in memories:
        score = item.get("score")
        tag = f"[match {score:.2f}] " if isinstance(score, (int, float)) else ""
        lines.append(f"- {tag}{item['text']}")
    return "\n".join(lines)
