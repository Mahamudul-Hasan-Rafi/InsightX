"""
knowledge_base/few_shot.py
───────────────────────────
Self-improving few-shot example retrieval.

How it works
────────────
Past queries that the user explicitly marked correct (is_correct=True in
nl_query_history) are surfaced as few-shot examples for future SQL generation.
The system improves automatically: every thumbs-up the analyst gives becomes
a training signal for subsequent queries on the same datasource/schema.

Retrieval strategy
──────────────────
1. Fetch the 20 most recent correct queries for this datasource + schema.
2. Embed each historical question using OllamaEmbeddings.
3. Score against the current question by cosine similarity.
4. Return the top-3 most similar as formatted prompt examples.

Fallback: if there are no correct queries yet, or if the embedding call fails,
return an empty string — the prompt works without few-shot examples.

Formatted output (injected before the main SQL prompt)
──────────────────────────────────────────────────────
  ### Reference Examples (verified correct queries for this schema)
  -- Question: total deposits by branch last month
  SELECT branch_code, SUM(amount) AS total
  FROM EKYC.transactions
  WHERE trx_date >= ADD_MONTHS(TRUNC(SYSDATE,'MM'),-1)
    AND trx_type = 'DEPOSIT'
  GROUP BY branch_code;

  -- Question: how many accounts were opened this week
  SELECT COUNT(*) AS new_accounts
  FROM EKYC.account
  WHERE open_date >= TRUNC(SYSDATE,'IW');
"""

from __future__ import annotations

import logging
import math
import uuid
from typing import Any

from langchain_ollama import OllamaEmbeddings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

logger = logging.getLogger(__name__)

_CANDIDATE_POOL  = 20   # fetch this many correct queries
_TOP_K_EXAMPLES  = 3    # inject this many into the prompt


async def fetch_few_shot_examples(
    datasource_id: str,
    schema_name:   str,
    tenant_id:     str,
    question:      str,
    db:            AsyncSession,
) -> str:
    """
    Return a formatted few-shot block (empty string if no examples available).

    Pulls correct past queries from nl_query_history, ranks them by semantic
    similarity to the current question, and formats the top-K as SQL comments.
    """
    ds_uuid = str(uuid.UUID(datasource_id))

    # ── Fetch candidate pool ──────────────────────────────────────────────────
    result = await db.execute(
        text("""
            SELECT question, executed_sql
            FROM nl_query_history
            WHERE datasource_id = :ds_id
              AND tenant_id     = :tid
              AND schema_name   = :schema
              AND is_correct    = TRUE
              AND executed_sql IS NOT NULL
              AND question     IS NOT NULL
            ORDER BY created_at DESC
            LIMIT :pool
        """),
        {
            "ds_id":  ds_uuid,
            "tid":    tenant_id,
            "schema": schema_name,
            "pool":   _CANDIDATE_POOL,
        },
    )
    candidates = result.mappings().all()

    if not candidates:
        return ""   # no examples yet — that's fine

    if len(candidates) == 1:
        # Single example — skip embedding, just use it
        return _format_examples([dict(candidates[0])])

    # ── Semantic ranking ──────────────────────────────────────────────────────
    try:
        embedder = OllamaEmbeddings(
            model=settings.ollama_embed_model,
            base_url=settings.ollama_base_url,
        )

        # Embed current question and all candidate questions in parallel
        all_texts  = [question] + [c["question"] for c in candidates]
        all_vecs   = await embedder.aembed_documents(all_texts)
        q_vec      = all_vecs[0]
        cand_vecs  = all_vecs[1:]

        scored = [
            (candidates[i], _cosine_sim(q_vec, cand_vecs[i]))
            for i in range(len(candidates))
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        top_k = [item[0] for item in scored[:_TOP_K_EXAMPLES]]
        return _format_examples([dict(r) for r in top_k])

    except Exception as exc:
        logger.warning("Few-shot embedding failed (%s) — using recency fallback.", exc)
        return _format_examples([dict(c) for c in candidates[:_TOP_K_EXAMPLES]])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _format_examples(examples: list[dict[str, Any]]) -> str:
    if not examples:
        return ""
    lines = ["### Reference Examples (verified correct queries for this schema)"]
    for ex in examples:
        q   = ex.get("question", "").strip()
        sql = ex.get("executed_sql", "").strip()
        if q and sql:
            lines.append(f"-- Question: {q}")
            lines.append(sql)
            lines.append("")
    return "\n".join(lines)
