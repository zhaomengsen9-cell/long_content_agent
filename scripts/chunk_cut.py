import argparse
import json
from pathlib import Path
from typing import Any


SKIP_TYPES = {"header", "footer", "page_number"}
PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = PROJECT_DIR / "dataset" / "mineru_pipeline_output" / "research" 
DEFAULT_OUTPUT_PATH = PROJECT_DIR / "dataset" / "chunks" / "research" / "mineru_chunks.jsonl"


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(_as_text(item.get("text") or item.get("content")))
            else:
                parts.append(_as_text(item))
        return "\n".join(part for part in parts if part).strip()
    return str(value).strip()


def _extract_image_text(block: dict[str, Any]) -> str:
    caption = _as_text(block.get("image_caption"))
    footnote = _as_text(block.get("image_footnote"))
    parts = []
    if caption:
        parts.append(f"[图片说明]\n{caption}")
    if footnote:
        parts.append(f"[图片脚注]\n{footnote}")
    return "\n".join(parts).strip()


def _extract_table_text(block: dict[str, Any]) -> str:
    caption = _as_text(block.get("table_caption"))
    body = _as_text(block.get("table_body"))
    footnote = _as_text(block.get("table_footnote"))
    parts = []
    if caption:
        parts.append(f"[表格标题]\n{caption}")
    if body:
        parts.append(f"[表格]\n{body}")
    if footnote:
        parts.append(f"[表格脚注]\n{footnote}")
    return "\n".join(parts).strip()


def _extract_block_text(block: dict[str, Any]) -> str:
    block_type = block.get("type")
    if block_type == "text":
        return _as_text(block.get("text"))
    if block_type == "table":
        return _extract_table_text(block)
    if block_type == "seal":
        text = _as_text(block.get("text"))
        return f"[印章] {text}" if text else ""
    if block_type == "image":
        return _extract_image_text(block)
    return ""


def _append_metadata(metadata: dict[str, Any], block: dict[str, Any]) -> None:
    page_idx = block.get("page_idx")
    if isinstance(page_idx, int) and page_idx >= 0:
        metadata["pages"].add(page_idx)

    bbox = block.get("bbox")
    if isinstance(bbox, list) and bbox:
        metadata["bboxes"].append({"page_idx": page_idx, "bbox": bbox})

    img_path = block.get("img_path")
    if isinstance(img_path, str) and img_path.strip():
        metadata["images"].append(img_path.strip())

    block_type = block.get("type")
    if isinstance(block_type, str):
        metadata["source_types"].add(block_type)


def _new_metadata() -> dict[str, Any]:
    return {
        "pages": set(),
        "bboxes": [],
        "images": [],
        "source_types": set(),
    }


def _finalize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "pages": sorted(metadata["pages"]),
        "bboxes": metadata["bboxes"],
        "images": sorted(set(metadata["images"])),
        "source_types": sorted(metadata["source_types"]),
    }


def chunk_mineru_json(json_blocks, doc_id, max_chunk_chars=800):
    """
    Chunk MinerU content_list.json blocks while preserving useful metadata.

    Processing policy:
    - text: normal body text; text_level marks headings.
    - table: separate chunk using table_body plus caption/footnote.
    - seal: keep OCR text, image path in metadata.
    - image: keep caption/footnote text if present, image path in metadata.
    - header/footer/page_number: skip as repeated noise.
    """
    chunks = []
    current_heading = "文档开头"
    current_content = []
    current_metadata = _new_metadata()

    def save_chunk(heading, content_list, metadata, chunk_type="text"):
        if not content_list:
            return

        chunk_text = "\n".join(content_list).strip()
        if not chunk_text:
            return

        finalized_metadata = _finalize_metadata(metadata)
        chunks.append(
            {
                "doc_id": doc_id,
                "heading": heading,
                "content": chunk_text,
                "chunk_type": chunk_type,
                "pages": finalized_metadata["pages"],
                "metadata": finalized_metadata,
            }
        )

    for block in json_blocks:
        block_type = block.get("type")
        if block_type in SKIP_TYPES:
            continue

        block_text = _extract_block_text(block)
        if not block_text:
            continue

        if block_type == "text" and "text_level" in block:
            save_chunk(current_heading, current_content, current_metadata)

            current_heading = block_text
            current_content = [block_text]
            current_metadata = _new_metadata()
            _append_metadata(current_metadata, block)
            continue

        if block_type == "table":
            save_chunk(current_heading, current_content, current_metadata)
            current_content = []
            current_metadata = _new_metadata()

            table_metadata = _new_metadata()
            _append_metadata(table_metadata, block)
            save_chunk(
                f"{current_heading} (表格)",
                [block_text],
                table_metadata,
                chunk_type="table",
            )
            continue

        current_content.append(block_text)
        _append_metadata(current_metadata, block)

        current_length = sum(len(text) for text in current_content)
        if current_length >= max_chunk_chars:
            save_chunk(current_heading, current_content, current_metadata)
            current_content = []
            current_metadata = _new_metadata()

    save_chunk(current_heading, current_content, current_metadata)
    return chunks


def find_content_list_files(input_dir: Path) -> list[Path]:
    files = []
    for path in sorted(input_dir.rglob("*_content_list.json")):
        if path.name.endswith("_content_list_v2.json"):
            continue
        files.append(path)
    return files


def doc_id_from_content_list_path(path: Path) -> str:
    suffix = "_content_list.json"
    if path.name.endswith(suffix):
        return path.name[: -len(suffix)]
    return path.stem


def chunk_content_list_file(
    path: Path,
    input_dir: Path,
    max_chunk_chars: int,
) -> list[dict[str, Any]]:
    doc_id = doc_id_from_content_list_path(path)
    blocks = json.loads(path.read_text(encoding="utf-8"))
    chunks = chunk_mineru_json(blocks, doc_id=doc_id, max_chunk_chars=max_chunk_chars)
    source_relpath = str(path.relative_to(input_dir))

    for index, chunk in enumerate(chunks, start=1):
        chunk["chunk_id"] = f"{doc_id}::chunk_{index:06d}"
        chunk["source_content_list"] = source_relpath
        chunk["metadata"]["source_content_list"] = source_relpath
    return chunks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch chunk MinerU *_content_list.json files."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Directory containing MinerU output files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Output JSONL path for chunks.",
    )
    parser.add_argument(
        "--max-chunk-chars",
        type=int,
        default=800,
        help="Maximum accumulated characters per text chunk.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N content_list files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only list matched content_list files.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    output_path = args.output.resolve()

    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")
    if args.max_chunk_chars < 1:
        raise ValueError("--max-chunk-chars must be greater than or equal to 1")

    files = find_content_list_files(input_dir)
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit must be greater than or equal to 1")
        files = files[: args.limit]

    print(f"Input: {input_dir}")
    print(f"Matched content_list files: {len(files)}")
    print(f"Output: {output_path}")

    if args.dry_run:
        for path in files:
            print(path)
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    total_chunks = 0
    with output_path.open("w", encoding="utf-8") as writer:
        for file_index, path in enumerate(files, start=1):
            chunks = chunk_content_list_file(path, input_dir, args.max_chunk_chars)
            for chunk in chunks:
                writer.write(json.dumps(chunk, ensure_ascii=False) + "\n")
            total_chunks += len(chunks)
            print(f"[{file_index}/{len(files)}] {path} -> {len(chunks)} chunks")

    print(f"Done. files={len(files)}, chunks={total_chunks}")


if __name__ == "__main__":
    main()
