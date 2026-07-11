"""
api/app/modules/nl_query/llm_client.py
───────────────────────────────────────
Thin async HTTP client for the local Ollama API.

Ollama exposes a REST API at http://localhost:11434 (configurable).
We use httpx (already in project requirements) — no extra package needed.

Models used:
  Embeddings:  nomic-embed-text  (768-dim, best local embedding model)
               → pulled with:  ollama pull nomic-embed-text
  SQL gen:     sqlcoder:7b       (fine-tuned for text-to-SQL)
               → pulled with:  ollama pull sqlcoder
               Fallback:       codellama:13b-instruct
               → pulled with:  ollama pull codellama:13b-instruct
  Narrative:   llama3.1:8b       (general instruction following)
               → pulled with:  ollama pull llama3.1:8b

All model names are configurable via environment variables (see config.py).
"""

import logging
import re
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ollama API endpoints
# ---------------------------------------------------------------------------

# POST /api/embeddings  →  { embedding: float[] }
_EMBED_URL       = f"{settings.ollama_base_url}/api/embeddings"

# POST /api/generate   →  { response: str, ... }
_GENERATE_URL    = f"{settings.ollama_base_url}/api/generate"

# GET  /api/tags       →  { models: [{ name, ... }] }
_TAGS_URL        = f"{settings.ollama_base_url}/api/tags"


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

async def embed_text(text: str) -> list[float]:
    """
    Embed a single text string via Ollama (nomic-embed-text by default).

    Returns a list of 768 floats.  Raises RuntimeError on failure.

    nomic-embed-text is the recommended local embedding model because:
      - 768-dim output (compact but expressive)
      - Specifically trained for retrieval tasks
      - Runs on CPU in ~50ms per text chunk
      - Available: `ollama pull nomic-embed-text`
    """
    payload = {
        "model":  settings.ollama_embed_model,
        "prompt": text,
    }
    async with httpx.AsyncClient(timeout=settings.ollama_timeout_seconds) as client:
        try:
            resp = await client.post(_EMBED_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["embedding"]
        except httpx.ConnectError:
            raise RuntimeError(
                f"Cannot reach Ollama at {settings.ollama_base_url}. "
                "Start it with: `ollama serve`"
            )
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Ollama embedding API error {e.response.status_code}: {e.response.text}")
        except KeyError:
            raise RuntimeError("Ollama returned no embedding field. Is nomic-embed-text pulled?")


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed multiple texts sequentially.

    Ollama's /api/embeddings takes one text at a time.  For large batches,
    consider parallelising with asyncio.gather — but sequential is fine for
    the table indexing use case (run once, not on every query).
    """
    results = []
    for text in texts:
        vec = await embed_text(text)
        results.append(vec)
    return results


async def generate_sql(prompt: str, model: Optional[str] = None) -> str:
    """
    Call Ollama to generate a SQL query from the assembled prompt.

    Uses sqlcoder:7b by default — this model was specifically fine-tuned on
    text-to-SQL tasks (Spider, WikiSQL, BIRD benchmarks) and consistently
    outperforms general-purpose models of the same size on SQL generation.

    Fallback:
      If sqlcoder is unavailable, try codellama:13b-instruct, then
      llama3.1:8b.  All are supported via the same /api/generate endpoint.

    Returns the raw text output from the model; sql_validator.py extracts
    and cleans the actual SQL statement.
    """
    model_name = model or settings.ollama_sql_model

    payload = {
        "model":  model_name,
        "prompt": prompt,
        "stream": False,       # wait for complete output — no partial SQL
        "options": {
            "temperature":  0.0,    # deterministic — SQL should not be creative
            "num_predict":  1024,   # max tokens; SQL rarely exceeds 512
            "stop": ["###", "---", "\n\n\n"],  # common section delimiters
        },
    }

    async with httpx.AsyncClient(timeout=settings.ollama_timeout_seconds) as client:
        try:
            resp = await client.post(_GENERATE_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
            raw = data.get("response", "").strip()
            if not raw:
                raise RuntimeError("Ollama returned empty SQL response.")
            return raw

        except httpx.ConnectError:
            raise RuntimeError(
                f"Cannot reach Ollama at {settings.ollama_base_url}. "
                "Start it with: `ollama serve`"
            )
        except httpx.HTTPStatusError as e:
            body = e.response.text
            # If the model is not pulled, Ollama returns 404 with "model not found"
            if e.response.status_code == 404 and "model" in body.lower():
                raise RuntimeError(
                    f"Ollama model '{model_name}' not found. "
                    f"Pull it with: `ollama pull {model_name}`"
                )
            raise RuntimeError(f"Ollama SQL generation error {e.response.status_code}: {body}")


async def generate_narrative(prompt: str, model: Optional[str] = None) -> str:
    """
    Generate a plain-English explanation of query results.

    Uses llama3.1:8b by default — good instruction following,
    runs on consumer hardware.

    Available: `ollama pull llama3.1:8b`
    """
    model_name = model or settings.ollama_narrative_model

    payload = {
        "model":  model_name,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature":  0.3,   # slight creativity for natural-sounding prose
            "num_predict":  512,
        },
    }

    async with httpx.AsyncClient(timeout=settings.ollama_timeout_seconds) as client:
        try:
            resp = await client.post(_GENERATE_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data.get("response", "").strip()
        except (httpx.ConnectError, httpx.HTTPStatusError) as e:
            # Narrative is non-critical — return a generic fallback rather than
            # surfacing an error to the user.
            logger.warning("Narrative generation failed: %s", e)
            return "Query executed successfully."


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

async def list_available_models() -> Optional[list[str]]:
    """
    Returns a list of models currently available in this Ollama instance.
    Returns None when Ollama cannot be reached, which lets the health endpoint
    distinguish a connectivity failure from a successful response with zero models.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(_TAGS_URL)
            resp.raise_for_status()
            data = resp.json()
            return [m["name"] for m in data.get("models", [])]
        except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError) as exc:
            logger.warning("Ollama health check failed: %s", exc)
            return None
        except (httpx.HTTPStatusError, KeyError, TypeError, ValueError) as exc:
            logger.warning("Ollama health check returned an invalid response: %s", exc)
            return None
