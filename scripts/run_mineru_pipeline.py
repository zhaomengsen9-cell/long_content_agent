import argparse
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path


SUPPORTED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg"}
PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = PROJECT_DIR / "dataset" / "public_dataset_upload" / "raw"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "dataset" / "mineru_pipeline_output"
DEFAULT_GPU_IDS = "4,5,6,7"


@dataclass
class MinerUResult:
    source: str
    output_dir: str
    status: str
    elapsed_seconds: float
    assigned_gpu_id: str | None = None
    returncode: int | None = None
    error: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Process public_dataset_upload files with MinerU pipeline backend."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Dataset directory to scan recursively.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where MinerU outputs and manifest are written.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=2,
        help="Number of files to process concurrently.",
    )
    parser.add_argument(
        "--method",
        choices=("auto", "txt", "ocr"),
        default="auto",
        help="MinerU parse method for pipeline backend.",
    )
    parser.add_argument(
        "--lang",
        default="ch",
        help="OCR language hint passed to MinerU.",
    )
    parser.add_argument(
        "--mineru-bin",
        default="mineru",
        help="MinerU CLI executable.",
    )
    parser.add_argument(
        "--gpu-ids",
        default=DEFAULT_GPU_IDS,
        help="GPU ids exposed to MinerU via CUDA_VISIBLE_DEVICES. Use an empty string to disable.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess files even if a success marker exists.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print the files that would be processed.",
    )
    return parser.parse_args()


def iter_supported_files(input_dir: Path, output_dir: Path) -> list[Path]:
    files: list[Path] = []
    output_dir = output_dir.resolve()

    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        if output_dir in path.resolve().parents:
            continue
        if path.suffix.lower() in SUPPORTED_SUFFIXES:
            files.append(path)

    return files


def iter_unsupported_files(input_dir: Path, output_dir: Path) -> list[Path]:
    files: list[Path] = []
    output_dir = output_dir.resolve()

    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        if output_dir in path.resolve().parents:
            continue
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            files.append(path)

    return files


def output_path_for(source: Path, input_dir: Path, output_dir: Path) -> Path:
    relative = source.relative_to(input_dir)
    return output_dir / relative.with_suffix("")


def marker_path(task_output_dir: Path) -> Path:
    return task_output_dir / ".mineru_success"


def log(message: str) -> None:
    print(message, flush=True)


def parse_gpu_ids(gpu_ids: str) -> list[str]:
    return [gpu_id.strip() for gpu_id in gpu_ids.split(",") if gpu_id.strip()]


def run_mineru_file(
    source: Path,
    input_dir: Path,
    output_dir: Path,
    mineru_bin: str,
    method: str,
    lang: str,
    assigned_gpu_id: str | None,
    force: bool,
) -> MinerUResult:
    start = time.monotonic()
    task_output_dir = output_path_for(source, input_dir, output_dir)
    success_marker = marker_path(task_output_dir)

    if success_marker.exists() and not force:
        log(f"[skip] already parsed: {source} -> {task_output_dir}")
        return MinerUResult(
            source=str(source),
            output_dir=str(task_output_dir),
            status="skipped_exists",
            elapsed_seconds=0.0,
            assigned_gpu_id=assigned_gpu_id,
        )

    task_output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        mineru_bin,
        "--path",
        str(source),
        "--output",
        str(task_output_dir),
        "--backend",
        "pipeline",
        "--method",
        method,
        "--lang",
        lang,
    ]

    env = os.environ.copy()
    if assigned_gpu_id:
        env["CUDA_VISIBLE_DEVICES"] = assigned_gpu_id

    gpu_log = assigned_gpu_id if assigned_gpu_id else "not forced"
    log(f"[start] parsing: {source} -> {task_output_dir} | CUDA_VISIBLE_DEVICES={gpu_log}")

    try:
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
    except Exception as exc:
        return MinerUResult(
            source=str(source),
            output_dir=str(task_output_dir),
            status="failed",
            elapsed_seconds=time.monotonic() - start,
            assigned_gpu_id=assigned_gpu_id,
            error=str(exc),
        )

    elapsed = time.monotonic() - start
    log_path = task_output_dir / "mineru.log"
    log_path.write_text(completed.stdout + completed.stderr, encoding="utf-8")

    if completed.returncode == 0:
        success_marker.write_text(str(time.time()), encoding="utf-8")
        status = "success"
        error = None
        log(f"[done] parsed: {source} ({elapsed:.1f}s)")
    else:
        status = "failed"
        error = f"MinerU exited with return code {completed.returncode}; see {log_path}"
        log(f"[failed] parse failed: {source} ({elapsed:.1f}s); see {log_path}")

    return MinerUResult(
        source=str(source),
        output_dir=str(task_output_dir),
        status=status,
        elapsed_seconds=elapsed,
        assigned_gpu_id=assigned_gpu_id,
        returncode=completed.returncode,
        error=error,
    )


def write_manifest(manifest_path: Path, result: MinerUResult) -> None:
    with manifest_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")


def main() -> None:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    gpu_id_list = parse_gpu_ids(args.gpu_ids)

    if args.workers < 1:
        raise ValueError("--workers must be greater than or equal to 1")
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.jsonl"
    files = iter_supported_files(input_dir, output_dir)
    unsupported_files = iter_unsupported_files(input_dir, output_dir)

    log(f"Input: {input_dir}")
    log(f"Output: {output_dir}")
    log(f"Supported files: {len(files)}")
    log(f"Unsupported files: {len(unsupported_files)}")
    log(f"Workers: {args.workers}")
    log(f"GPU assignment: {', '.join(gpu_id_list) if gpu_id_list else 'not forced'}")

    if args.dry_run:
        for index, path in enumerate(files):
            assigned_gpu_id = gpu_id_list[index % len(gpu_id_list)] if gpu_id_list else None
            log(f"[dry-run] task={index + 1} gpu={assigned_gpu_id or 'not forced'} path={path}")
        if unsupported_files:
            log("Unsupported files will be recorded as skipped_unsupported.")
        return

    if manifest_path.exists() and args.force:
        manifest_path.unlink()

    succeeded = 0
    failed = 0
    skipped = 0

    for path in unsupported_files:
        write_manifest(
            manifest_path,
            MinerUResult(
                source=str(path),
                output_dir="",
                status="skipped_unsupported",
                elapsed_seconds=0.0,
                assigned_gpu_id=None,
                error=f"Unsupported suffix for MinerU CLI: {path.suffix}",
            ),
        )
        skipped += 1

    log("Starting MinerU parsing tasks...")

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = []
        for index, source in enumerate(files):
            assigned_gpu_id = gpu_id_list[index % len(gpu_id_list)] if gpu_id_list else None
            futures.append(
                executor.submit(
                    run_mineru_file,
                    source,
                    input_dir,
                    output_dir,
                    args.mineru_bin,
                    args.method,
                    args.lang,
                    assigned_gpu_id,
                    args.force,
                )
            )

        for future in as_completed(futures):
            result = future.result()
            write_manifest(manifest_path, result)

            if result.status == "success":
                succeeded += 1
            elif result.status.startswith("skipped"):
                skipped += 1
            else:
                failed += 1

            log(
                f"[{result.status}] {result.source} -> {result.output_dir} "
                f"| gpu={result.assigned_gpu_id or 'not forced'} "
                f"({result.elapsed_seconds:.1f}s)"
            )

    log(f"Done. success={succeeded}, skipped={skipped}, failed={failed}")
    log(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
