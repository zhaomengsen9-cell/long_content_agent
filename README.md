# Long Content Agent

This directory is a minimal scaffold for a future agent.

## Layout

- `agent/`: agent package
- `agent/core.py`: main agent class placeholder
- `agent/config.py`: configuration placeholder
- `main.py`: runnable entry point

## Run

```bash
python long_content/main.py
```

## Model Config

Model settings live in `agent/config.py`.

Defaults:

- provider: `dashscope`
- model: `qwen3.7-plus`
- api key env: `DASHSCOPE_API_KEY`
- base url: `https://llm-cfmyrw3vesq6bnwj.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`
- temperature: `0.0`
- max tokens: `4096`
- enable thinking: `True`

## MinerU Pipeline Processing

Process all MinerU-supported files under `dataset/public_dataset_upload` with
the pipeline backend:

```bash
python long_content/scripts/run_mineru_pipeline.py --workers 4
```

Outputs are written to:

```text
long_content/dataset/public_dataset_upload/mineru_pipeline_output
```

The script records every file in `manifest.jsonl`. Files unsupported by MinerU
CLI, such as `html`, `txt`, and `json`, are recorded as `skipped_unsupported`.
