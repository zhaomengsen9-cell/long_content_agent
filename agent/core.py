import os
from collections.abc import Iterable
from typing import Any

from openai import OpenAI

from .config import AgentConfig


class Agent:
    def __init__(self, config: AgentConfig | None = None) -> None:
        self.config = config or AgentConfig()
        self.client = self.create_client()

    def create_client(self) -> OpenAI:
        model_config = self.config.model
        api_key = os.getenv(model_config.api_key_env)
        if not api_key:
            raise RuntimeError(f"Missing API key environment variable: {model_config.api_key_env}")

        return OpenAI(
            api_key=api_key,
            base_url=model_config.base_url,
            timeout=model_config.timeout_seconds,
        )

    def chat_stream(self, messages: list[dict[str, str]]) -> Iterable[Any]:
        model_config = self.config.model
        return self.client.chat.completions.create(
            model=model_config.model,
            messages=messages,
            temperature=model_config.temperature,
            max_tokens=model_config.max_tokens,
            extra_body={"enable_thinking": model_config.enable_thinking},
            stream=model_config.stream,
        )

    def run(self) -> None:
        pass
