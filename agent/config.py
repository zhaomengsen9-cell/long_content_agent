from dataclasses import dataclass, field


@dataclass
class ModelConfig:
    provider: str = "dashscope"
    model: str = "qwen3.7-plus"
    api_key_env: str = "sk-ws-H.EMPPXLM.v2f4.MEQCIHMif8p7DOoaYs8UUyKQZggUo0xx6fygyzfQWG8zVbeaAiBq8TmKMIReDLU0Px4U1xqGLIgPbZxnaYcbgEr1VTHsog"
    base_url: str = "https://llm-cfmyrw3vesq6bnwj.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    temperature: float = 0.0
    max_tokens: int = 4096
    timeout_seconds: int = 60
    enable_thinking: bool = True
    stream: bool = True


@dataclass
class AgentConfig:
    name: str = "long-content-agent"
    model: ModelConfig = field(default_factory=ModelConfig)
