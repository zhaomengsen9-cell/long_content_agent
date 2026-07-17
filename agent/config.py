from dataclasses import dataclass, field
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


@dataclass
class ModelConfig:
    provider: str = "dashscope"
    model: str = "qwen3.7-max"
    api_key_env: str = "DASHSCOPE_API_KEY"
    base_url: str = "https://llm-cfmyrw3vesq6bnwj.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    temperature: float = 0.0
    max_tokens: int = 4096
    timeout_seconds: int = 60
    enable_thinking: bool = True
    stream: bool = True
    max_retries: int = 3
    retry_initial_delay_seconds: float = 5.0


@dataclass
class DataConfig:
    contexts_path: Path | None = None
    retrieval_dir: Path = PROJECT_DIR / "dataset" / "retrieval"
    answers_path: Path = PROJECT_DIR / "dataset" / "answers" / "answer.csv"
    reasons_path: Path = PROJECT_DIR / "dataset" / "answers" / "reason.jsonl"
    failed_path: Path = PROJECT_DIR / "dataset" / "answers" / "failed.jsonl"


@dataclass
class PromptConfig:
    system_prompt: str = """你是一个严谨的长文档金融问答助手。

你会收到一道选择题，以及可能已经检索好的上下文。你的任务是判断选项是否成立，并输出可提交的答案。

必须遵守：
1. 如果上下文中有有效证据，必须优先使用上下文证据，不要引入外部知识。
2. 如果上下文明确显示“未检索到相关 chunk”、上下文为空，或给出的上下文不足以判断，则不要默认选择 A 或第一个选项；应基于题目、选项和你的通用理解进行最佳判断。
3. 无检索证据时，reasoning 中必须明确说明“未检索到充分证据，以下为模型基于题目和选项的判断”。
4. 跨文档对比题在有证据时必须分别核对每个文档；如果某个文档没有证据，要说明该文档证据不足，再进行最佳判断。
5. 涉及数字、百分比、证券代码、主体名称、评级、日期时，有上下文证据则逐字核对；没有上下文证据时不要编造具体出处。
6. 如果上下文中有表格，优先使用表格中的原始数值。
7. 绝不能因为无法判断就固定输出第一个选项；答案必须来自实际判断。
8. answer 只能包含 evidence 中 verdict 为“支持”的选项；verdict 为“不支持”或“证据不足”的选项绝不能放入 answer。
9. 多选题要保守作答：只有选项表述与上下文证据直接一致时才选择。若只是“可能成立”、只有间接推断、chunk_ids 为空、或引用内容不能直接证明该选项，则判为“不支持”或“证据不足”，不要选。
10. 如果某个选项的证据来自“未检索到”“上下文未提供”“无法确认”这类表述，该选项必须视为证据不足，不能进入 answer。

输出格式必须是 JSON，不要输出 Markdown。answer 字段必须是最终提交答案：
单选题输出一个大写字母，例如 "A"。
判断题输出一个大写字母，例如 "A" 或 "B"。
多选题输出按字母顺序排列的大写字母字符串，例如 "ABC"，不要逗号、空格或其他分隔符。
{
  "answer": "A" 或 "ABC",
  "reasoning": "简要说明每个选项的判断依据",
  "evidence": [
    {
      "option": "A",
      "verdict": "支持/不支持/证据不足",
      "quotes": ["引用关键短句或数值"],
      "chunk_ids": ["相关chunk_id"]
    }
  ]
}
"""


@dataclass
class AgentConfig:
    name: str = "long-content-agent"
    model: ModelConfig = field(default_factory=ModelConfig)
    data: DataConfig = field(default_factory=DataConfig)
    prompt: PromptConfig = field(default_factory=PromptConfig)
