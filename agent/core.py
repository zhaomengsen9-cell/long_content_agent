import csv
import json
import os
import re
import time
from collections.abc import Iterable
from pathlib import Path
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

    def load_context_records(self, contexts_path: Path | None = None) -> list[dict[str, Any]]:
        return self.load_context_records_from_file(contexts_path)

    def load_context_records_from_file(
        self, contexts_path: Path | None = None
    ) -> list[dict[str, Any]]:
        path = contexts_path or self.config.data.contexts_path
        if path is None:
            raise RuntimeError("Missing contexts path. Please pass --contexts <contexts.jsonl>.")
        records = []
        with path.open("r", encoding="utf-8") as reader:
            for line in reader:
                if line.strip():
                    record = json.loads(line)
                    record["_context_file"] = str(path)
                    records.append(record)
        return records

    def load_context_records_from_dir(self, retrieval_dir: Path | None = None) -> list[dict[str, Any]]:
        directory = retrieval_dir or self.config.data.retrieval_dir
        paths = sorted(directory.glob("*.jsonl"))
        if not paths:
            raise RuntimeError(f"No context JSONL files found in: {directory}")

        records = []
        seen_qids = set()
        for path in paths:
            for record in self.load_context_records_from_file(path):
                qid = record.get("qid")
                if qid in seen_qids:
                    continue
                seen_qids.add(qid)
                records.append(record)
        return records

    def build_messages(self, record: dict[str, Any]) -> list[dict[str, str]]:
        user_prompt = f"""下面是已经检索好的上下文，请回答其中的问题。

题目ID：{record.get("qid", "")}
题目类型：{record.get("type", "")}
指定文档：{record.get("doc_ids", [])}

{record.get("context", "")}
"""
        return [
            {"role": "system", "content": self.config.prompt.system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def chat_stream(self, messages: list[dict[str, str]]) -> Iterable[Any]:
        model_config = self.config.model
        return self.client.chat.completions.create(
            model=model_config.model,
            messages=messages,
            temperature=model_config.temperature,
            max_tokens=model_config.max_tokens,
            extra_body={"enable_thinking": model_config.enable_thinking},
            stream=model_config.stream,
            stream_options={"include_usage": True},
        )

    def parse_answer(
        self, model_output: str, allowed_options: set[str] | None = None
    ) -> str:
        text = model_output.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        parsed: Any = None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except json.JSONDecodeError:
                    parsed = None

        if isinstance(parsed, dict) and "answer" in parsed:
            raw_answer = parsed.get("answer")
        else:
            answer_match = re.search(
                r'"answer"\s*:\s*"([^"]*)"', text, flags=re.IGNORECASE
            )
            raw_answer = answer_match.group(1) if answer_match else text

        if isinstance(raw_answer, list):
            letters = [str(item).strip().upper() for item in raw_answer]
        else:
            letters = re.findall(r"[A-Z]", str(raw_answer).upper())

        allowed = allowed_options or set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        valid_letters = sorted({letter for letter in letters if letter in allowed})
        return "".join(valid_letters)

    def ask(
        self, record: dict[str, Any], show_reasoning: bool = False
    ) -> tuple[str, str, dict[str, int]]:
        completion = self.chat_stream(self.build_messages(record))
        answer_parts = []
        reasoning_parts = []
        is_answering = False
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        for chunk in completion:
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage:
                usage = {
                    "prompt_tokens": int(getattr(chunk_usage, "prompt_tokens", 0) or 0),
                    "completion_tokens": int(
                        getattr(chunk_usage, "completion_tokens", 0) or 0
                    ),
                    "total_tokens": int(getattr(chunk_usage, "total_tokens", 0) or 0),
                }

            if not getattr(chunk, "choices", None):
                continue

            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            content = getattr(delta, "content", None)

            if reasoning and not is_answering:
                reasoning_parts.append(reasoning)
                if show_reasoning:
                    print(reasoning, end="", flush=True)

            if content:
                if show_reasoning and not is_answering:
                    print("\n" + "=" * 20 + "完整回复" + "=" * 20)
                is_answering = True
                answer_parts.append(content)
                print(content, end="", flush=True)

        if answer_parts:
            print()
        return "".join(answer_parts).strip(), "".join(reasoning_parts).strip(), usage

    def ask_with_retry(
        self, record: dict[str, Any], show_reasoning: bool = False
    ) -> tuple[str, str, dict[str, int]]:
        max_retries = max(1, self.config.model.max_retries)
        delay = self.config.model.retry_initial_delay_seconds
        last_error: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                if attempt > 1:
                    print(f"[retry {attempt}/{max_retries}] answering {record.get('qid')}")
                return self.ask(record, show_reasoning=show_reasoning)
            except Exception as exc:
                last_error = exc
                if attempt >= max_retries:
                    break
                print(
                    f"[warn] {record.get('qid')} failed on attempt {attempt}/{max_retries}: "
                    f"{type(exc).__name__}: {exc}. retry after {delay:.1f}s"
                )
                time.sleep(delay)
                delay *= 2

        assert last_error is not None
        raise last_error

    def load_answer_rows(self, output: Path) -> list[dict[str, Any]]:
        if not output.exists():
            return []

        rows = []
        with output.open("r", encoding="utf-8", newline="") as reader:
            for row in csv.DictReader(reader):
                if row.get("qid") == "summary":
                    continue
                rows.append(
                    {
                        "qid": row.get("qid", ""),
                        "answer": row.get("answer", ""),
                        "prompt_tokens": int(row.get("prompt_tokens") or 0),
                        "completion_tokens": int(row.get("completion_tokens") or 0),
                        "total_tokens": int(row.get("total_tokens") or 0),
                    }
                )
        return rows

    def write_answer_csv(self, output: Path, rows: list[dict[str, Any]]) -> None:
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for row in rows:
            for key in total_usage:
                total_usage[key] += int(row.get(key) or 0)

        with output.open("w", encoding="utf-8", newline="") as writer:
            fieldnames = [
                "qid",
                "answer",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
            ]
            csv_writer = csv.DictWriter(writer, fieldnames=fieldnames)
            csv_writer.writeheader()
            csv_writer.writerow(
                {
                    "qid": "summary",
                    "answer": "",
                    "prompt_tokens": total_usage["prompt_tokens"],
                    "completion_tokens": total_usage["completion_tokens"],
                    "total_tokens": total_usage["total_tokens"],
                }
            )
            csv_writer.writerows(rows)

    def append_reason_record(
        self,
        reason_path: Path,
        record: dict[str, Any],
        reasoning: str,
        model_output: str,
    ) -> None:
        reason_path.parent.mkdir(parents=True, exist_ok=True)
        with reason_path.open("a", encoding="utf-8") as writer:
            writer.write(
                json.dumps(
                    {
                        "qid": record.get("qid"),
                        "domain": record.get("domain"),
                        "context_file": record.get("_context_file"),
                        "question": record.get("question"),
                        "options": record.get("options"),
                        "reasoning_content": reasoning,
                        "model_output": model_output,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    def append_failed_record(
        self,
        failed_path: Path,
        record: dict[str, Any],
        error: Exception,
    ) -> None:
        failed_path.parent.mkdir(parents=True, exist_ok=True)
        with failed_path.open("a", encoding="utf-8") as writer:
            writer.write(
                json.dumps(
                    {
                        "qid": record.get("qid"),
                        "domain": record.get("domain"),
                        "context_file": record.get("_context_file"),
                        "question": record.get("question"),
                        "error_type": type(error).__name__,
                        "error": str(error),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    def run(
        self,
        contexts_path: Path | None = None,
        output_path: Path | None = None,
        retrieval_dir: Path | None = None,
        reason_path: Path | None = None,
        qid: str | None = None,
        show_reasoning: bool = False,
        resume: bool = False,
    ) -> None:
        if contexts_path:
            records = self.load_context_records_from_file(contexts_path)
        else:
            records = self.load_context_records_from_dir(retrieval_dir)
        if qid:
            records = [record for record in records if record.get("qid") == qid]
        if not records:
            raise RuntimeError(f"No context records found for qid={qid!r}")

        output = output_path or self.config.data.answers_path
        reason_output = reason_path or self.config.data.reasons_path
        failed_output = self.config.data.failed_path
        output.parent.mkdir(parents=True, exist_ok=True)
        if not resume:
            reason_output.parent.mkdir(parents=True, exist_ok=True)
            reason_output.write_text("", encoding="utf-8")
            failed_output.parent.mkdir(parents=True, exist_ok=True)
            failed_output.write_text("", encoding="utf-8")

        rows = self.load_answer_rows(output) if resume else []
        answered_qids = {row["qid"] for row in rows}
        if resume:
            before_count = len(records)
            records = [record for record in records if record.get("qid") not in answered_qids]
            print(
                f"Resume mode: loaded {len(rows)} existing answers, "
                f"skip {before_count - len(records)} answered questions."
            )

        if not records:
            self.write_answer_csv(output, rows)
            print("No new questions to answer.")
            print(f"Output: {output}")
            print(f"Reason output: {reason_output}")
            return

        for idx, record in enumerate(records, start=1):
            print(f"[{idx}/{len(records)}] answering {record.get('qid')}")
            try:
                model_output, reasoning, usage = self.ask_with_retry(
                    record, show_reasoning=show_reasoning
                )
            except Exception as exc:
                print(
                    f"[error] {record.get('qid')} failed after "
                    f"{self.config.model.max_retries} attempts: {type(exc).__name__}: {exc}"
                )
                self.append_failed_record(failed_output, record, exc)
                continue
            allowed_options = {
                str(key).strip().upper()
                for key in (record.get("options") or {}).keys()
                if str(key).strip()
            }
            answer = self.parse_answer(model_output, allowed_options=allowed_options)
            self.append_reason_record(reason_output, record, reasoning, model_output)
            rows.append(
                {
                    "qid": record.get("qid"),
                    "answer": answer,
                    "prompt_tokens": usage["prompt_tokens"],
                    "completion_tokens": usage["completion_tokens"],
                    "total_tokens": usage["total_tokens"],
                }
            )
            self.write_answer_csv(output, rows)

        self.write_answer_csv(output, rows)
        print(f"Output: {output}")
        print(f"Reason output: {reason_output}")
        print(f"Failed output: {failed_output}")
