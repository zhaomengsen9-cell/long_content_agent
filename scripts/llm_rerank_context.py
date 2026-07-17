import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = PROJECT_DIR.parent
for import_path in (str(REPO_DIR), str(PROJECT_DIR)):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

try:
    from long_content.agent.core import Agent
    from long_content.scripts.retrieve_context import (
        format_context,
        load_chunks,
        load_questions,
        retrieve_for_question,
    )
except ImportError:
    from agent.core import Agent
    from scripts.retrieve_context import (
        format_context,
        load_chunks,
        load_questions,
        retrieve_for_question,
    )


DEFAULT_QUESTIONS_DIR = PROJECT_DIR / "dataset" / "public_dataset_upload" / "questions" / "group_a"
DEFAULT_CHUNKS_DIR = PROJECT_DIR / "dataset" / "chunks"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "dataset" / "retrieval_llm_rerank_new"

DOMAIN_FILES = {
    "financial_contracts": "financial_contracts_questions.json",
    "financial_reports": "financial_reports_questions.json",
    "insurance": "insurance_questions.json",
    "regulatory": "regulatory_questions.json",
    "research": "research_questions.json",
}


def compact_text(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(text)).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def build_doc_alias_lines(question: dict[str, Any]) -> list[str]:
    doc_ids = [str(doc_id) for doc_id in question.get("doc_ids", []) if doc_id]
    if not doc_ids:
        return []

    aliases = []
    for doc_id in doc_ids:
        match = re.fullmatch(r"text(\d+)", doc_id)
        if match:
            aliases.append(f"fc_text_{int(match.group(1)):03d}={doc_id}")
            aliases.append(f"fr_text_{int(match.group(1)):03d}={doc_id}")
            aliases.append(f"ins_text_{int(match.group(1)):03d}={doc_id}")
        else:
            aliases.append(doc_id)

    return [
        f"指定文档 doc_ids：{', '.join(doc_ids)}",
        f"可能出现的文档别名：{', '.join(aliases)}",
        "如果题目或选项中出现 fc_text_XXX、fr_text_XXX、ins_text_XXX 这类别名，请优先选择对应 doc_id 的 chunk。",
        "对于每个指定 doc_id，只要候选 chunks 中有相关证据，就不要把该文档完全留空。",
    ]


def build_candidate_prompt(question: dict[str, Any], hits: list[dict[str, Any]], preview_chars: int) -> str:
    lines = [
        "你是证据筛选器。请从候选 chunks 中选择最可能支持或反驳每个选项的 chunk_id。",
        "只返回 JSON，不要输出 Markdown，不要解释。",
        "每个选项最多选择 3 个 chunk_id；如果没有相关证据，返回空数组。",
        "优先选择包含原始数值、代码、表格字段、主体名称、条款原文的 chunk。",
        *build_doc_alias_lines(question),
        "",
        f"问题：{question.get('question', '')}",
        "选项：",
    ]
    options = question.get("options") or {}
    for key in sorted(options):
        lines.append(f"{key}. {options[key]}")

    lines.append("")
    lines.append("候选 chunks：")
    for hit in hits:
        retrieval = hit.get("_retrieval", {})
        pages = ",".join(str(page) for page in hit.get("pages", [])) or "N/A"
        lines.append(
            f"[chunk_id={hit.get('chunk_id')} doc_id={hit.get('doc_id')} "
            f"type={hit.get('chunk_type')} pages={pages} "
            f"score={retrieval.get('score')} query={retrieval.get('query')}]"
        )
        lines.append(f"heading: {compact_text(hit.get('heading', ''), 180)}")
        lines.append(f"content_preview: {compact_text(hit.get('content', ''), preview_chars)}")
        lines.append("")

    option_keys = sorted(options) or ["A", "B", "C", "D"]
    skeleton = {key: [] for key in option_keys}
    lines.append("输出 JSON 格式示例：")
    lines.append(json.dumps(skeleton, ensure_ascii=False))
    return "\n".join(lines)


def parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}


def create_llm_client() -> Agent:
    return Agent()


def llm_select_chunks(
    agent: Agent,
    question: dict[str, Any],
    hits: list[dict[str, Any]],
    preview_chars: int,
) -> tuple[dict[str, list[str]], str]:
    prompt = build_candidate_prompt(question, hits, preview_chars)
    model_config = agent.config.model
    messages = [
        {
            "role": "system",
            "content": "你只负责选择证据 chunk_id。必须输出严格 JSON。",
        },
        {"role": "user", "content": prompt},
    ]

    last_text = ""
    delay = model_config.retry_initial_delay_seconds
    for attempt in range(1, model_config.max_retries + 1):
        try:
            response = agent.client.chat.completions.create(
                model=model_config.model,
                messages=messages,
                temperature=0,
                max_tokens=1200,
                extra_body={"enable_thinking": False},
                stream=False,
            )
            last_text = response.choices[0].message.content or ""
            parsed = parse_json_object(last_text)
            result: dict[str, list[str]] = {}
            for key, value in parsed.items():
                if isinstance(value, list):
                    result[str(key)] = [str(item) for item in value]
            return result, last_text
        except Exception as exc:
            if attempt >= model_config.max_retries:
                raise
            print(
                f"[warn] rerank {question.get('qid')} failed "
                f"{attempt}/{model_config.max_retries}: {type(exc).__name__}: {exc}. "
                f"retry after {delay:.1f}s"
            )
            time.sleep(delay)
            delay *= 2
    return {}, last_text


def select_hits_by_llm(
    question: dict[str, Any],
    hits: list[dict[str, Any]],
    selected: dict[str, list[str]],
    max_selected: int,
    fallback_top_k: int,
    min_doc_k: int,
    min_option_k: int,
) -> list[dict[str, Any]]:
    hit_by_id = {hit.get("chunk_id"): hit for hit in hits}
    doc_ids = [str(doc_id) for doc_id in question.get("doc_ids", []) if doc_id]
    option_keys = sorted((question.get("options") or {}).keys())
    selected_ids: list[str] = []

    def add_chunk_id(chunk_id: Any) -> None:
        if len(selected_ids) >= max_selected:
            return
        if chunk_id in hit_by_id and chunk_id not in selected_ids:
            selected_ids.append(chunk_id)

    def add_doc_minimum() -> None:
        if min_doc_k <= 0:
            return
        counts = defaultdict(int)
        for chunk_id in selected_ids:
            hit = hit_by_id.get(chunk_id)
            if hit is not None:
                counts[str(hit.get("doc_id", ""))] += 1
        for doc_id in doc_ids:
            if len(selected_ids) >= max_selected:
                break
            for hit in hits:
                if len(selected_ids) >= max_selected or counts[doc_id] >= min_doc_k:
                    break
                if str(hit.get("doc_id", "")) != doc_id:
                    continue
                chunk_id = hit.get("chunk_id")
                if chunk_id in selected_ids:
                    continue
                add_chunk_id(chunk_id)
                counts[doc_id] += 1

    def add_option_minimum() -> None:
        if min_option_k <= 0:
            return
        for key in option_keys:
            if len(selected_ids) >= max_selected:
                break
            prefix = f"option_{key}"
            option_count = 0
            for chunk_id in selected.get(key, []):
                if chunk_id in hit_by_id:
                    option_count += 1
            for hit in hits:
                if len(selected_ids) >= max_selected or option_count >= min_option_k:
                    break
                retrieval = hit.get("_retrieval", {})
                query_label = str(retrieval.get("query", ""))
                if not query_label.startswith(prefix):
                    continue
                chunk_id = hit.get("chunk_id")
                if chunk_id in selected_ids:
                    continue
                add_chunk_id(chunk_id)
                option_count += 1

    for key in option_keys:
        for chunk_id in selected.get(key, []):
            add_chunk_id(chunk_id)

    add_option_minimum()
    add_doc_minimum()

    if not selected_ids:
        for hit in hits[:fallback_top_k]:
            add_chunk_id(hit.get("chunk_id"))
        add_option_minimum()
        add_doc_minimum()
    else:
        for hit in hits:
            if len(selected_ids) >= max_selected:
                break
            add_chunk_id(hit.get("chunk_id"))

    return [hit_by_id[chunk_id] for chunk_id in selected_ids[:max_selected] if chunk_id in hit_by_id]


def load_domain_inputs(domain: str, questions_dir: Path, chunks_dir: Path) -> tuple[Path, Path]:
    question_file = questions_dir / DOMAIN_FILES[domain]
    chunk_file = chunks_dir / domain / "mineru_chunks.jsonl"
    return question_file, chunk_file


def run_domain(args: argparse.Namespace, domain: str, agent: Agent) -> None:
    questions_path, chunks_path = load_domain_inputs(domain, args.questions_dir, args.chunks_dir)
    questions = load_questions(questions_path)
    if args.qid:
        questions = [question for question in questions if question.get("qid") == args.qid]
    chunks = load_chunks(chunks_path)

    chunks_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        doc_id = chunk.get("doc_id")
        if isinstance(doc_id, str):
            chunks_by_doc[doc_id].append(chunk)

    output_path = args.output_dir / f"{domain}_contexts.jsonl"
    debug_path = args.output_dir / f"{domain}_rerank_debug.jsonl"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as writer, debug_path.open(
        "w", encoding="utf-8"
    ) as debug_writer:
        for idx, question in enumerate(questions, start=1):
            print(f"[{domain} {idx}/{len(questions)}] {question.get('qid')}")
            candidate_hits = retrieve_for_question(
                question=question,
                chunks_by_doc=chunks_by_doc,
                per_doc_top_k=args.candidate_per_doc_top_k,
                final_top_k=args.candidate_top_k,
                min_doc_top_k=args.candidate_min_doc_top_k,
            )
            selected, raw_output = llm_select_chunks(
                agent=agent,
                question=question,
                hits=candidate_hits,
                preview_chars=args.preview_chars,
            )
            final_hits = select_hits_by_llm(
                question=question,
                hits=candidate_hits,
                selected=selected,
                max_selected=args.final_top_k,
                fallback_top_k=args.fallback_top_k,
                min_doc_k=args.final_min_doc_k,
                min_option_k=args.final_min_option_k,
            )
            context = format_context(question, final_hits)
            record = {
                "qid": question.get("qid"),
                "domain": question.get("domain"),
                "doc_ids": question.get("doc_ids", []),
                "question": question.get("question"),
                "options": question.get("options"),
                "retrieved_chunk_ids": [hit.get("chunk_id") for hit in final_hits],
                "context": context,
            }
            writer.write(json.dumps(record, ensure_ascii=False) + "\n")
            debug_writer.write(
                json.dumps(
                    {
                        "qid": question.get("qid"),
                        "candidate_chunk_ids": [hit.get("chunk_id") for hit in candidate_hits],
                        "llm_selected": selected,
                        "final_chunk_ids": record["retrieved_chunk_ids"],
                        "candidate_doc_counts": dict(
                            sorted(
                                {
                                    str(doc_id): sum(
                                        1
                                        for hit in candidate_hits
                                        if str(hit.get("doc_id", "")) == str(doc_id)
                                    )
                                    for doc_id in question.get("doc_ids", [])
                                }.items()
                            )
                        ),
                        "final_doc_counts": dict(
                            sorted(
                                {
                                    str(doc_id): sum(
                                        1
                                        for hit in final_hits
                                        if str(hit.get("doc_id", "")) == str(doc_id)
                                    )
                                    for doc_id in question.get("doc_ids", [])
                                }.items()
                            )
                        ),
                        "llm_raw_output": raw_output,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            writer.flush()
            debug_writer.flush()

    print(f"Output: {output_path}")
    print(f"Debug: {debug_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM rerank BM25 candidate chunks.")
    parser.add_argument("--domain", choices=list(DOMAIN_FILES) + ["all"], default="all")
    parser.add_argument("--qid", default=None)
    parser.add_argument("--questions-dir", type=Path, default=DEFAULT_QUESTIONS_DIR)
    parser.add_argument("--chunks-dir", type=Path, default=DEFAULT_CHUNKS_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--candidate-top-k", type=int, default=120)
    parser.add_argument("--candidate-per-doc-top-k", type=int, default=60)
    parser.add_argument("--candidate-min-doc-top-k", type=int, default=24)
    parser.add_argument("--final-top-k", type=int, default=48)
    parser.add_argument("--fallback-top-k", type=int, default=6)
    parser.add_argument("--final-min-doc-k", type=int, default=6)
    parser.add_argument("--final-min-option-k", type=int, default=3)
    parser.add_argument("--preview-chars", type=int, default=800)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    domains = list(DOMAIN_FILES) if args.domain == "all" else [args.domain]
    agent = create_llm_client()
    for domain in domains:
        run_domain(args, domain, agent)


if __name__ == "__main__":
    main()
