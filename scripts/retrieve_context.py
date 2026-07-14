import argparse
import html
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_QUESTIONS_PATH = (
    PROJECT_DIR
    / "dataset"
    / "public_dataset_upload"
    / "questions"
    / "group_a"
    / "research_questions.json"
)
DEFAULT_CHUNKS_PATH = (
    PROJECT_DIR
    / "dataset"
    / "chunks"
    / "research"
    / "mineru_chunks.jsonl"
)
DEFAULT_OUTPUT_PATH = (
    PROJECT_DIR
    / "dataset"
    / "retrieval"
    / "research_contexts.jsonl"
)


def normalize_html_table(text: str) -> str:
    text = re.sub(r"<\s*/\s*(tr|p|div|br)\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*/\s*td\s*>", " | ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def tokenize(text: str) -> list[str]:
    text = normalize_html_table(text).lower()
    tokens = re.findall(
        r"[a-zA-Z]+(?:[-_][a-zA-Z0-9]+)*|"
        r"\d+(?:\.\d+)?%?|"
        r"[\u4e00-\u9fff]{1,4}",
        text,
    )
    return [token for token in tokens if token.strip()]


def extract_terms(text: str) -> set[str]:
    normalized = normalize_html_table(text)
    terms = set(re.findall(r"\d+(?:\.\d+)?%?", normalized))
    terms.update(re.findall(r"[A-Z]{2,}|[A-Z]+\d+|\d{4,}", normalized))
    terms.update(re.findall(r"[\u4e00-\u9fff]{2,12}", normalized))
    return {term for term in terms if term.strip()}


def question_to_query(question: dict[str, Any]) -> str:
    parts = [question.get("question", "")]
    options = question.get("options") or {}
    for key in sorted(options):
        parts.append(f"{key}. {options[key]}")
    return "\n".join(parts)


def load_questions(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_chunks(path: Path) -> list[dict[str, Any]]:
    chunks = []
    with path.open("r", encoding="utf-8") as reader:
        for line in reader:
            if not line.strip():
                continue
            chunks.append(json.loads(line))
    return chunks


class BM25Index:
    def __init__(self, chunks: list[dict[str, Any]], k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self.doc_tokens = []
        self.doc_freq = Counter()
        self.doc_len = []

        for chunk in chunks:
            text = f"{chunk.get('heading', '')}\n{chunk.get('content', '')}"
            tokens = tokenize(text)
            counts = Counter(tokens)
            self.doc_tokens.append(counts)
            self.doc_len.append(sum(counts.values()))
            for token in counts:
                self.doc_freq[token] += 1

        self.avgdl = sum(self.doc_len) / len(self.doc_len) if self.doc_len else 0.0

    def score(self, query: str, index: int) -> float:
        if not self.chunks or self.avgdl <= 0:
            return 0.0

        query_counts = Counter(tokenize(query))
        doc_counts = self.doc_tokens[index]
        dl = self.doc_len[index]
        total_docs = len(self.chunks)
        score = 0.0

        for token, qtf in query_counts.items():
            tf = doc_counts.get(token, 0)
            if tf <= 0:
                continue
            df = self.doc_freq.get(token, 0)
            idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
            denom = tf + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
            score += idf * (tf * (self.k1 + 1) / denom) * min(2, qtf)
        return score


def rule_bonus(query: str, chunk: dict[str, Any]) -> float:
    text = normalize_html_table(f"{chunk.get('heading', '')}\n{chunk.get('content', '')}")
    query_terms = extract_terms(query)
    if not query_terms:
        return 0.0

    bonus = 0.0
    for term in query_terms:
        if term in text:
            if re.fullmatch(r"\d+(?:\.\d+)?%?", term):
                bonus += 2.0
            elif re.fullmatch(r"[A-Z]{2,}|[A-Z]+\d+|\d{4,}", term):
                bonus += 1.5
            else:
                bonus += 0.25

    if chunk.get("chunk_type") == "table" and re.search(
        r"金额|比例|资产负债率|代码|证券|简称|评级|收入|净利润|现金流|财务|数据",
        query,
    ):
        bonus += 2.0

    heading = str(chunk.get("heading", ""))
    for term in query_terms:
        if len(term) >= 2 and term in heading:
            bonus += 0.5
    return bonus


def retrieve_for_question(
    question: dict[str, Any],
    chunks_by_doc: dict[str, list[dict[str, Any]]],
    per_doc_top_k: int,
    final_top_k: int,
    min_doc_top_k: int,
) -> list[dict[str, Any]]:
    query = question_to_query(question)
    doc_ids = question.get("doc_ids") or []
    hits_by_doc: dict[str, list[dict[str, Any]]] = {}

    for doc_id in doc_ids:
        doc_chunks = chunks_by_doc.get(doc_id, [])
        if not doc_chunks:
            continue

        index = BM25Index(doc_chunks)
        scored = []
        for idx, chunk in enumerate(doc_chunks):
            bm25 = index.score(query, idx)
            bonus = rule_bonus(query, chunk)
            score = bm25 + bonus
            if score > 0:
                scored.append((score, bm25, bonus, chunk))

        scored.sort(key=lambda item: item[0], reverse=True)
        for rank, (score, bm25, bonus, chunk) in enumerate(scored[:per_doc_top_k], start=1):
            hit = dict(chunk)
            hit["_retrieval"] = {
                "rank_in_doc": rank,
                "score": round(score, 4),
                "bm25": round(bm25, 4),
                "bonus": round(bonus, 4),
            }
            hits_by_doc.setdefault(doc_id, []).append(hit)

    kept_ids = set()
    balanced_hits = []
    if min_doc_top_k > 0:
        for doc_id in doc_ids:
            for hit in hits_by_doc.get(doc_id, [])[:min_doc_top_k]:
                chunk_id = hit.get("chunk_id")
                kept_ids.add(chunk_id)
                balanced_hits.append(hit)

    all_hits = [
        hit
        for doc_hits in hits_by_doc.values()
        for hit in doc_hits
        if hit.get("chunk_id") not in kept_ids
    ]
    all_hits.sort(key=lambda item: item["_retrieval"]["score"], reverse=True)
    remaining = max(0, final_top_k - len(balanced_hits))
    merged_hits = balanced_hits + all_hits[:remaining]
    merged_hits.sort(
        key=lambda item: (str(item.get("doc_id", "")), item["_retrieval"]["rank_in_doc"])
    )
    return merged_hits


def format_context(question: dict[str, Any], hits: list[dict[str, Any]]) -> str:
    grouped = defaultdict(list)
    for hit in hits:
        grouped[hit.get("doc_id", "")].append(hit)

    lines = [
        "请仅根据以下检索上下文回答问题。",
        "如果某个选项缺少证据，请不要臆测。",
        "",
        f"问题：{question.get('question', '')}",
    ]
    options = question.get("options") or {}
    if options:
        lines.append("选项：")
        for key in sorted(options):
            lines.append(f"{key}. {options[key]}")
    lines.append("")

    for doc_id in question.get("doc_ids") or []:
        lines.append(f"【文档 {doc_id}】")
        doc_hits = grouped.get(doc_id, [])
        if not doc_hits:
            lines.append("未检索到相关 chunk。")
            lines.append("")
            continue
        for hit in doc_hits:
            pages = hit.get("pages", [])
            page_text = ",".join(str(page) for page in pages) if pages else "N/A"
            retrieval = hit.get("_retrieval", {})
            lines.append(
                f"[chunk_id={hit.get('chunk_id')} "
                f"score={retrieval.get('score')} "
                f"type={hit.get('chunk_type')} "
                f"pages={page_text}]"
            )
            lines.append(f"heading: {hit.get('heading', '')}")
            lines.append("content:")
            lines.append(str(hit.get("content", "")))
            lines.append("")
    return "\n".join(lines).strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Retrieve financial contract chunks and build LLM-ready contexts."
    )
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS_PATH)
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--qid", default=None, help="Only process one qid.")
    parser.add_argument("--per-doc-top-k", type=int, default=8)
    parser.add_argument("--final-top-k", type=int, default=16)
    parser.add_argument(
        "--min-doc-top-k",
        type=int,
        default=4,
        help="Keep at least this many chunks for each requested doc_id when available.",
    )
    parser.add_argument("--print-context", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    questions = load_questions(args.questions)
    if args.qid:
        questions = [question for question in questions if question.get("qid") == args.qid]
    chunks = load_chunks(args.chunks)

    chunks_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        doc_id = chunk.get("doc_id")
        if isinstance(doc_id, str):
            chunks_by_doc[doc_id].append(chunk)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as writer:
        for question in questions:
            hits = retrieve_for_question(
                question=question,
                chunks_by_doc=chunks_by_doc,
                per_doc_top_k=args.per_doc_top_k,
                final_top_k=args.final_top_k,
                min_doc_top_k=args.min_doc_top_k,
            )
            context = format_context(question, hits)
            record = {
                "qid": question.get("qid"),
                "domain": question.get("domain"),
                "doc_ids": question.get("doc_ids", []),
                "question": question.get("question"),
                "options": question.get("options"),
                "retrieved_chunk_ids": [hit.get("chunk_id") for hit in hits],
                "context": context,
            }
            writer.write(json.dumps(record, ensure_ascii=False) + "\n")

            if args.print_context:
                print(context)
                print("\n" + "=" * 80 + "\n")

    print(f"Questions: {len(questions)}")
    print(f"Chunks: {len(chunks)}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
