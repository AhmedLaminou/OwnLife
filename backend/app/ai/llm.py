"""Chat models: free OpenRouter models first, the local Ollama model as fallback.

OpenRouter's `models` array gives server-side fallback between the free models;
LangChain's `with_fallbacks` adds the client-side fallback to Ollama, which is
what keeps the assistant working during an internet cut.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import httpx
import openai
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from app.config import Settings
from app.models import Profile


class AIUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelChoice:
    provider: str  # openrouter | ollama
    model: str
    label: str
    is_cloud: bool
    llm: BaseChatModel


def effective_mode(settings: Settings, profile: Profile | None) -> str:
    mode = (profile.ai_settings or {}).get("mode") if profile else None
    return mode or settings.ai_mode


def effective_models(settings: Settings, profile: Profile | None) -> list[str]:
    models = (profile.ai_settings or {}).get("models") if profile else None
    return [m for m in (models or settings.openrouter_model_list) if m]


def chat_models(
    settings: Settings, profile: Profile | None, *, temperature: float = 0.3
) -> list[ModelChoice]:
    mode = effective_mode(settings, profile)
    out: list[ModelChoice] = []
    if mode in ("cloud", "cloud_then_local"):
        key = settings.openrouter_api_key.get_secret_value() if settings.openrouter_api_key else ""
        models = effective_models(settings, profile)
        if key and models:
            extra: dict = {}
            if len(models) > 1:
                extra["models"] = models
            provider: dict = {}
            if settings.openrouter_data_collection:
                provider["data_collection"] = settings.openrouter_data_collection
            if settings.openrouter_zdr:
                provider["zdr"] = True
            if provider:
                extra["provider"] = provider
            llm = ChatOpenAI(
                model=models[0],
                api_key=key,
                base_url=settings.openrouter_base_url,
                temperature=temperature,
                timeout=settings.llm_timeout_seconds,
                max_retries=1,
                use_responses_api=False,
                extra_body=extra or None,
                default_headers={"HTTP-Referer": "http://localhost:8000", "X-Title": "OwnLife"},
            )
            more = f" (+{len(models) - 1} fallback{'s' if len(models) > 2 else ''})" if len(models) > 1 else ""
            out.append(ModelChoice("openrouter", models[0], f"OpenRouter · {models[0]}{more}", True, llm))
    if mode in ("local", "cloud_then_local"):
        out.append(local_model(settings, temperature=temperature))
    return out


def local_model(settings: Settings, *, temperature: float = 0.3) -> ModelChoice:
    """The Ollama model on this machine, whatever the AI mode says."""
    llm = ChatOpenAI(
        model=settings.ollama_chat_model,
        api_key="ollama",  # Ollama ignores it; the client requires one
        base_url=settings.ollama_base_url.rstrip("/") + "/v1",
        temperature=temperature,
        timeout=settings.local_llm_timeout_seconds,
        max_retries=0,
        use_responses_api=False,
    )
    return ModelChoice("ollama", settings.ollama_chat_model, f"Ollama · {settings.ollama_chat_model}", False, llm)


def compose(choices: list[ModelChoice], wrap: Callable[[BaseChatModel], Runnable]) -> Runnable:
    """wrap(llm) — e.g. `lambda m: m.bind_tools(tools)` — is applied to every model,
    then the results are chained: the first that does not raise answers."""
    if not choices:
        raise AIUnavailableError(
            "No model is configured. Put an OpenRouter key in backend/.env "
            "(OPENROUTER_API_KEY) or switch AI mode to local in Settings."
        )
    runnables = [wrap(c.llm) for c in choices]
    primary, *rest = runnables
    return primary.with_fallbacks(rest) if rest else primary


def any_cloud(choices: list[ModelChoice]) -> bool:
    return any(c.is_cloud for c in choices)


def describe_error(e: BaseException) -> str:
    """A message a person can act on, instead of a stack trace."""
    if isinstance(e, AIUnavailableError):
        return str(e)
    if isinstance(e, openai.RateLimitError):
        return (
            "The free models' rate limit was reached (20 requests/minute, 50/day until the "
            "account has bought $10 of credits). Wait, or switch AI mode to local in Settings."
        )
    if isinstance(e, openai.AuthenticationError):
        return "OpenRouter rejected the API key. Check OPENROUTER_API_KEY in backend/.env."
    if isinstance(e, openai.NotFoundError):
        msg = str(e)
        if "data policy" in msg.lower() or "privacy" in msg.lower():
            return (
                "No provider matches your OpenRouter privacy settings. Free endpoints usually "
                "require allowing prompt logging at openrouter.ai/settings/privacy — or set "
                "AI mode to local to keep everything on this machine."
            )
        return f"Model not found on OpenRouter — it may have been retired. ({msg[:160]})"
    if isinstance(e, (openai.APIConnectionError, httpx.ConnectError)):
        return "Could not reach the model: no internet, or Ollama is not running."
    if isinstance(e, (openai.APITimeoutError, httpx.TimeoutException)):
        return "The model took too long to answer. Try again, or use a shorter question."
    if isinstance(e, openai.APIStatusError):
        return f"The model provider returned an error ({e.status_code}): {str(e)[:200]}"
    return f"{e.__class__.__name__}: {str(e)[:300]}"
