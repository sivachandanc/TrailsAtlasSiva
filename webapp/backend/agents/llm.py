"""The chat model behind the criteria agent."""

from langchain_anthropic import ChatAnthropic

from webapp.backend import config


class LlmNotConfigured(Exception):
    pass


def make_criteria_llm() -> ChatAnthropic:
    if not config.ANTHROPIC_API_KEY_SET:
        raise LlmNotConfigured("The AI pharmacist needs an Anthropic API key. Add ANTHROPIC_API_KEY to the .env file "
                               "and restart the app.")
    # Only pass a base URL when .env sets one, so the default endpoint is otherwise untouched.
    endpoint = {"base_url": config.ANTHROPIC_BASE_URL} if config.ANTHROPIC_BASE_URL else {}
    return ChatAnthropic(
        model=config.CRITERIA_MODEL,
        max_tokens=32_000,  # thinking + a long submit_criteria call; streamed so it can't time out
        streaming=True,
        # Adaptive thinking is always on for Claude Opus 5.5; "updates" returns the model's short
        # progress notes between tool calls so the page can show what it's doing.
        thinking={"type": "adaptive", "display": "updates"},
        output_config={"effort": config.CRITERIA_EFFORT},
        # If a safety classifier declines, the API retries on a fallback model instead of failing.
        betas=["thinking-display-updates-2026-08-18", "server-side-fallback-2026-07-01"],
        model_kwargs={"fallbacks": "default"},
        max_retries=3,
        **endpoint,
    )
