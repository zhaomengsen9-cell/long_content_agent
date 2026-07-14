import argparse
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_HTML_DIR = PROJECT_DIR / "dataset" / "public_dataset_upload" / "raw" / "regulatory" / "html"
DEFAULT_TXT_DIR = PROJECT_DIR / "dataset" / "public_dataset_upload" / "raw" / "regulatory" / "txt"
DEFAULT_OUTPUT_PATH = PROJECT_DIR / "dataset" / "chunks" / "regulatory" / "mineru_chunks.jsonl"


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self.skip_depth += 1
        if tag in {"p", "div", "br", "tr", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.skip_depth > 0:
            self.skip_depth -= 1
        if tag in {"p", "div", "tr", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        return normalize_text("\n".join(self.parts))


def normalize_text(text: str) -> str:
    text = text.replace("\ufeff", "")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def extract_tag_text(html: str, pattern: str) -> str:
    match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    parser = TextExtractor()
    parser.feed(match.group(1))
    return parser.text()


def extract_meta(html: str, name: str) -> str:
    pattern = (
        rf'<meta\s+[^>]*name=["\']{re.escape(name)}["\'][^>]*'
        rf'content=(["\'])(.*?)\1[^>]*>'
    )
    match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return normalize_text(match.group(2))


def extract_html_text(path: Path) -> tuple[str, str]:
    html = path.read_text(encoding="utf-8", errors="ignore")
    title = extract_meta(html, "ArticleTitle") or extract_tag_text(html, r"<title[^>]*>(.*?)</title>")

    body = (
        extract_tag_text(html, r'<div[^>]+class=["\'][^"\']*detail-news[^"\']*["\'][^>]*>(.*?)<div[^>]+class=["\'][^"\']*xxgk-down-box')
        or extract_tag_text(html, r'<div[^>]+class=["\'][^"\']*detail-news[^"\']*["\'][^>]*>(.*?)</div>')
        or extract_tag_text(html, r"<body[^>]*>(.*?)</body>")
    )

    if title and not body.startswith(title):
        body = f"{title}\n{body}" if body else title
    return title or path.stem, normalize_text(body)


def extract_txt_text(path: Path) -> tuple[str, str]:
    text = normalize_text(path.read_text(encoding="utf-8", errors="ignore"))
    title = ""
    for line in text.splitlines():
        line = line.strip()
        if line:
            title = line
            break
    return title or path.stem, text


def split_paragraphs(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def is_heading(paragraph: str) -> bool:
    if len(paragraph) > 80:
        return False
    return bool(
        re.match(r"^第[一二三四五六七八九十百]+章", paragraph)
        or re.match(r"^第[一二三四五六七八九十百]+节", paragraph)
        or re.match(r"^[一二三四五六七八九十]+、", paragraph)
        or paragraph.endswith("办法")
        or paragraph.endswith("规定")
        or paragraph.endswith("规则")
    )


def make_chunks(
    doc_id: str,
    title: str,
    text: str,
    source_file: Path,
    source_root: Path,
    source_kind: str,
    max_chunk_chars: int,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    heading = title or "文档开头"
    current: list[str] = []
    source_relpath = str(source_file.relative_to(source_root))

    def save() -> None:
        if not current:
            return
        content = "\n".join(current).strip()
        if not content:
            return
        index = len(chunks) + 1
        metadata = {
            "pages": [],
            "bboxes": [],
            "images": [],
            "source_types": [source_kind],
            "source_file": source_relpath,
        }
        chunks.append(
            {
                "doc_id": doc_id,
                "heading": heading,
                "content": content,
                "chunk_type": "text",
                "pages": [],
                "metadata": metadata,
                "chunk_id": f"{doc_id}::chunk_{index:06d}",
                "source_content_list": source_relpath,
            }
        )

    for paragraph in split_paragraphs(text):
        if is_heading(paragraph) and current:
            save()
            current = []
            heading = paragraph

        current.append(paragraph)
        if sum(len(item) for item in current) >= max_chunk_chars:
            save()
            current = []

    save()
    return chunks


def existing_doc_ids(output_path: Path) -> set[str]:
    if not output_path.exists():
        return set()
    ids = set()
    with output_path.open("r", encoding="utf-8") as reader:
        for line in reader:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            doc_id = item.get("doc_id")
            if isinstance(doc_id, str):
                ids.add(doc_id)
    return ids


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Append regulatory html/txt chunks to regulatory mineru_chunks.jsonl."
    )
    parser.add_argument("--html-dir", type=Path, default=DEFAULT_HTML_DIR)
    parser.add_argument("--txt-dir", type=Path, default=DEFAULT_TXT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--max-chunk-chars", type=int, default=800)
    parser.add_argument(
        "--force-append",
        action="store_true",
        help="Append even if doc_id already exists in output.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    html_dir = args.html_dir.resolve()
    txt_dir = args.txt_dir.resolve()
    output_path = args.output.resolve()

    if args.max_chunk_chars < 1:
        raise ValueError("--max-chunk-chars must be greater than or equal to 1")

    files: list[tuple[Path, Path, str]] = []
    if html_dir.exists():
        files.extend((path, html_dir, "html") for path in sorted(html_dir.glob("*.html")))
    if txt_dir.exists():
        files.extend((path, txt_dir, "txt") for path in sorted(txt_dir.glob("*.txt")))

    seen_doc_ids = set() if args.force_append else existing_doc_ids(output_path)

    print(f"HTML dir: {html_dir}")
    print(f"TXT dir: {txt_dir}")
    print(f"Output: {output_path}")
    print(f"Files found: {len(files)}")
    print(f"Existing doc_ids: {len(seen_doc_ids)}")

    if args.dry_run:
        for path, _, source_kind in files[:20]:
            status = "skip_exists" if path.stem in seen_doc_ids else "append"
            print(f"[{status}] {source_kind}: {path}")
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    appended_files = 0
    appended_chunks = 0
    skipped_files = 0

    with output_path.open("a", encoding="utf-8") as writer:
        for path, source_root, source_kind in files:
            doc_id = path.stem
            if doc_id in seen_doc_ids:
                skipped_files += 1
                continue

            if source_kind == "html":
                title, text = extract_html_text(path)
            else:
                title, text = extract_txt_text(path)

            chunks = make_chunks(
                doc_id=doc_id,
                title=title,
                text=text,
                source_file=path,
                source_root=source_root,
                source_kind=source_kind,
                max_chunk_chars=args.max_chunk_chars,
            )
            for chunk in chunks:
                writer.write(json.dumps(chunk, ensure_ascii=False) + "\n")
            appended_files += 1
            appended_chunks += len(chunks)
            print(f"[append] {source_kind}: {path} -> {len(chunks)} chunks")

    print(
        f"Done. appended_files={appended_files}, "
        f"appended_chunks={appended_chunks}, skipped_files={skipped_files}"
    )


if __name__ == "__main__":
    main()
