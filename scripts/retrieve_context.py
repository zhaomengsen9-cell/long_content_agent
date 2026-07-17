import argparse
import html
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    import jieba
except ImportError:
    jieba = None


PROJECT_DIR = Path(__file__).resolve().parents[1]
if jieba is not None:
    jieba_cache_dir = PROJECT_DIR / ".cache" / "jieba"
    jieba_cache_dir.mkdir(parents=True, exist_ok=True)
    jieba.dt.tmp_dir = str(jieba_cache_dir)
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

KEY_PHRASES = [
    "主体信用评级",
    "债项信用评级",
    "发行金额",
    "发行金额上限",
    "发行规模",
    "募集规模",
    "募集资金",
    "增信措施",
    "信用评级结果",
    "评级展望",
    "受托管理人",
    "主承销商",
    "簿记管理人",
    "证券代码",
    "股票代码",
    "证券简称",
    "转股价格",
    "转股价值",
    "转股后的资产负债率",
    "资产负债率",
    "流动比率",
    "速动比率",
    "毛利率",
    "净利率",
    "净资产收益率",
    "营业收入",
    "营业成本",
    "利润总额",
    "净利润",
    "归属于上市公司股东的净利润",
    "扣除非经常性损益",
    "基本每股收益",
    "稀释每股收益",
    "研发投入",
    "研发费用",
    "现金流量",
    "经营活动产生的现金流量净额",
    "经营活动",
    "投资活动",
    "筹资活动",
    "保险责任",
    "责任免除",
    "保险金额",
    "保险期间",
    "身故保险金",
    "重大疾病保险金",
    "养老保险金",
    "给付比例",
    "等待期",
    "犹豫期",
    "现金价值",
    "保单账户价值",
    "累计已交保险费",
    "监管规则",
    "管理办法",
    "适用指引",
    "注册工作规程",
    "客户尽职调查",
    "受益所有人",
    "反洗钱",
    "数据安全",
    "银行卡清算机构",
]


def normalize_html_table(text: str) -> str:
    text = re.sub(r"<\s*/\s*(tr|p|div|br)\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*/\s*td\s*>", " | ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def tokenize(text: str) -> list[str]:
    normalized_text = normalize_html_table(text)
    text = normalized_text.lower()
    tokens = re.findall(
        r"\d+(?:\.\d+)?%?|"
        r"[a-zA-Z]+(?:[-_+.][a-zA-Z0-9]+)*|"
        r"[a-zA-Z]*\d+[a-zA-Z0-9._+-]*",
        text,
    )
    expanded = []
    for token in tokens:
        if not token.strip():
            continue
        expanded.append(token)

    for chinese_span in re.findall(r"[\u4e00-\u9fff]+", normalized_text):
        for size in (2, 3):
            if len(chinese_span) >= size:
                expanded.extend(
                    chinese_span[i : i + size]
                    for i in range(len(chinese_span) - size + 1)
                )

    if jieba is not None:
        for token in jieba.lcut(normalized_text):
            token = token.strip().lower()
            if len(token) >= 2:
                expanded.append(token)
    return expanded


def extract_terms(text: str) -> set[str]:
    normalized = normalize_html_table(text)
    terms = set(re.findall(r"\d+(?:\.\d+)?%?", normalized))
    terms.update(re.findall(r"[A-Z]{2,}|[A-Z]+\d+|\d{4,}", normalized))
    terms.update(re.findall(r"[\u4e00-\u9fff]{2,12}", normalized))
    return {term for term in terms if term.strip()}


def extract_must_terms(query: str) -> list[str]:
    normalized = normalize_html_table(query)
    terms = []
    terms.extend(re.findall(r"\d+(?:\.\d+)?%?", normalized))
    terms.extend(re.findall(r"\d{4,}(?:\.[A-Z]{2})?", normalized))
    terms.extend(re.findall(r"[A-Z]{2,}\+?|[A-Z]+\d+", normalized))
    terms.extend(phrase for phrase in KEY_PHRASES if phrase in normalized)
    seen = set()
    result = []
    for term in terms:
        if term and term not in seen:
            seen.add(term)
            result.append(term)
    return result


def question_to_query(question: dict[str, Any]) -> str:
    parts = [question.get("question", "")]
    options = question.get("options") or {}
    for key in sorted(options):
        parts.append(f"{key}. {options[key]}")
    return "\n".join(parts)


def build_retrieval_queries(question: dict[str, Any]) -> list[tuple[str, str]]:
    queries = [("question", question.get("question", ""))]
    options = question.get("options") or {}
    for key in sorted(options):
        queries.append((f"option_{key}", f"{question.get('question', '')}\n{key}. {options[key]}"))
    queries.append(("all_options", question_to_query(question)))
    return [(label, query) for label, query in queries if query.strip()]


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


def chunk_sort_key(chunk: dict[str, Any]) -> tuple[int, str]:
    chunk_id = str(chunk.get("chunk_id", ""))
    match = re.search(r"chunk_(\d+)$", chunk_id)
    index = int(match.group(1)) if match else 0
    return index, chunk_id


def is_low_value_chunk(chunk: dict[str, Any]) -> bool:
    content = normalize_html_table(str(chunk.get("content", "")))
    heading = normalize_html_table(str(chunk.get("heading", "")))
    if not content:
        return True
    if chunk.get("chunk_type") != "table" and heading == content and len(content) <= 80:
        return True
    if len(content) < 30 and not re.search(r"\d|%|[A-Z]{2,}|AAA|AA\+?", content):
        return True
    if re.fullmatch(r"本页无正文|目录|声明|释义|单位[:：]? ?[^\n]{0,20}", content):
        return True
    return False


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
    numeric_terms = [
        term for term in query_terms if re.fullmatch(r"\d+(?:\.\d+)?%?", term)
    ]
    code_terms = [
        term for term in query_terms if re.fullmatch(r"[A-Z]{2,}|[A-Z]+\d+|\d{4,}", term)
    ]

    for term in query_terms:
        if term in text:
            if re.fullmatch(r"\d+(?:\.\d+)?%?", term):
                bonus += 4.0
            elif re.fullmatch(r"[A-Z]{2,}|[A-Z]+\d+|\d{4,}", term):
                bonus += 3.0
            else:
                bonus += 0.25

    if numeric_terms and all(term in text for term in numeric_terms):
        bonus += 6.0 + len(numeric_terms)
    if len(numeric_terms) >= 2 and sum(term in text for term in numeric_terms) >= 2:
        bonus += 4.0
    if code_terms and all(term in text for term in code_terms):
        bonus += 4.0

    for phrase in KEY_PHRASES:
        if phrase in query and phrase in text:
            bonus += 3.0

    if chunk.get("chunk_type") == "table" and re.search(
        r"金额|比例|资产负债率|代码|证券|简称|评级|收入|净利润|现金流|财务|数据",
        query,
    ):
        bonus += 6.0

    heading = str(chunk.get("heading", ""))
    for term in query_terms:
        if len(term) >= 2 and term in heading:
            bonus += 0.5
    return bonus


def make_hit(
    chunk: dict[str, Any],
    rank: int,
    score: float,
    bm25: float,
    bonus: float,
    query_label: str,
) -> dict[str, Any]:
    hit = dict(chunk)
    hit["_retrieval"] = {
        "rank_in_doc": rank,
        "score": round(score, 4),
        "bm25": round(bm25, 4),
        "bonus": round(bonus, 4),
        "query": query_label,
    }
    return hit


def must_recall_hits(
    query_label: str,
    query: str,
    chunks: list[dict[str, Any]],
    limit: int = 3,
) -> list[dict[str, Any]]:
    must_terms = extract_must_terms(query)
    if not must_terms:
        return []

    numeric_terms = [term for term in must_terms if re.fullmatch(r"\d+(?:\.\d+)?%?", term)]
    phrase_terms = [term for term in must_terms if re.search(r"[\u4e00-\u9fff]", term)]
    code_terms = [term for term in must_terms if term not in numeric_terms and term not in phrase_terms]

    scored = []
    for chunk in chunks:
        text = normalize_html_table(f"{chunk.get('heading', '')}\n{chunk.get('content', '')}")
        matched_numeric = [term for term in numeric_terms if term in text]
        matched_phrases = [term for term in phrase_terms if term in text]
        matched_codes = [term for term in code_terms if term in text]
        matched_count = len(matched_numeric) + len(matched_phrases) + len(matched_codes)

        if not matched_count:
            continue

        should_recall = False
        if len(numeric_terms) >= 2 and len(matched_numeric) >= 2:
            should_recall = True
        elif numeric_terms and matched_numeric and matched_phrases:
            should_recall = True
        elif len(matched_phrases) >= 2:
            should_recall = True
        elif matched_codes and matched_phrases:
            should_recall = True

        if not should_recall:
            continue

        score = (
            len(matched_numeric) * 12.0
            + len(matched_codes) * 8.0
            + len(matched_phrases) * 5.0
        )
        if chunk.get("chunk_type") == "table":
            score += 8.0
        scored.append((score, chunk, matched_numeric + matched_codes + matched_phrases))

    scored.sort(key=lambda item: item[0], reverse=True)
    hits = []
    for rank, (score, chunk, matched_terms) in enumerate(scored[:limit], start=1):
        hit = make_hit(
            chunk=chunk,
            rank=rank,
            score=score,
            bm25=0.0,
            bonus=score,
            query_label=f"{query_label}_must",
        )
        hit["_retrieval"]["matched_terms"] = matched_terms
        hits.append(hit)
    return hits


def retrieve_for_question(
    question: dict[str, Any],
    chunks_by_doc: dict[str, list[dict[str, Any]]],
    per_doc_top_k: int,
    final_top_k: int,
    min_doc_top_k: int,
) -> list[dict[str, Any]]:
    queries = build_retrieval_queries(question)
    doc_ids = question.get("doc_ids") or []
    hits_by_doc: dict[str, list[dict[str, Any]]] = {}

    for doc_id in doc_ids:
        raw_doc_chunks = chunks_by_doc.get(doc_id, [])
        doc_chunks = [chunk for chunk in raw_doc_chunks if not is_low_value_chunk(chunk)]
        if not doc_chunks:
            continue

        index = BM25Index(doc_chunks)
        candidate_hits: dict[str, dict[str, Any]] = {}

        for query_label, query in queries:
            for hit in must_recall_hits(query_label, query, doc_chunks):
                chunk_id = hit.get("chunk_id")
                existing = candidate_hits.get(chunk_id)
                if existing is None or hit["_retrieval"]["score"] > existing["_retrieval"]["score"]:
                    candidate_hits[chunk_id] = hit

            if query_label == "all_options":
                per_query_top_k = 2
            elif query_label == "question":
                per_query_top_k = max(2, min(3, per_doc_top_k))
            else:
                per_query_top_k = max(2, min(4, per_doc_top_k))
            scored = []
            for idx, chunk in enumerate(doc_chunks):
                bm25 = index.score(query, idx)
                bonus = rule_bonus(query, chunk)
                score = bm25 + bonus
                if score > 0:
                    scored.append((score, bm25, bonus, idx, chunk))

            scored.sort(key=lambda item: item[0], reverse=True)
            for rank, (score, bm25, bonus, idx, chunk) in enumerate(
                scored[:per_query_top_k], start=1
            ):
                chunk_id = chunk.get("chunk_id")
                existing = candidate_hits.get(chunk_id)
                if existing is None or score > existing["_retrieval"]["score"]:
                    candidate_hits[chunk_id] = make_hit(
                        chunk=chunk,
                        rank=rank,
                        score=score,
                        bm25=bm25,
                        bonus=bonus,
                        query_label=query_label,
                    )

                for neighbor_idx in (idx - 1, idx + 1):
                    if neighbor_idx < 0 or neighbor_idx >= len(doc_chunks):
                        continue
                    neighbor = doc_chunks[neighbor_idx]
                    if is_low_value_chunk(neighbor):
                        continue
                    neighbor_id = neighbor.get("chunk_id")
                    if neighbor_id in candidate_hits:
                        continue
                    neighbor_hit = make_hit(
                        chunk=neighbor,
                        rank=rank + 1000,
                        score=score * 0.45,
                        bm25=0.0,
                        bonus=0.0,
                        query_label=f"{query_label}_neighbor",
                    )
                    candidate_hits[neighbor_id] = neighbor_hit

        doc_hits = sorted(
            candidate_hits.values(),
            key=lambda item: item["_retrieval"]["score"],
            reverse=True,
        )
        for rank, hit in enumerate(doc_hits[:per_doc_top_k], start=1):
            hit["_retrieval"]["rank_in_doc"] = rank
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
        "请优先根据以下检索上下文回答问题。",
        "如果某个选项在上下文中缺少直接证据，不要直接当作错误；请结合题目、选项和已检索到的信息做最佳判断，并说明证据是否充分。",
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
                f"query={retrieval.get('query')} "
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
    parser.add_argument("--per-doc-top-k", type=int, default=16)
    parser.add_argument("--final-top-k", type=int, default=32)
    parser.add_argument(
        "--min-doc-top-k",
        type=int,
        default=8,
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
