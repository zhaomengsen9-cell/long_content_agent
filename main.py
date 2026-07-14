import argparse
from pathlib import Path

try:
    from .agent.core import Agent
except ImportError:
    from agent.core import Agent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run long-content QA agent.")
    parser.add_argument(
        "--contexts",
        type=Path,
        default=None,
        help="Run one retrieved contexts JSONL. If omitted, scan --retrieval-dir.",
    )
    parser.add_argument(
        "--retrieval-dir",
        type=Path,
        default=None,
        help="Directory containing retrieved context JSONL files.",
    )
    parser.add_argument("--output", type=Path, default=None, help="Answer output CSV.")
    parser.add_argument("--reason-output", type=Path, default=None, help="Reasoning output JSONL.")
    parser.add_argument("--qid", default=None, help="Only answer one question id.")
    parser.add_argument(
        "--show-reasoning",
        action="store_true",
        help="Print model reasoning_content when the backend returns it.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Continue from an existing answer CSV and skip answered qids.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    agent = Agent()
    agent.run(
        contexts_path=args.contexts,
        output_path=args.output,
        retrieval_dir=args.retrieval_dir,
        reason_path=args.reason_output,
        qid=args.qid,
        show_reasoning=args.show_reasoning,
        resume=args.resume,
    )


if __name__ == "__main__":
    main()
