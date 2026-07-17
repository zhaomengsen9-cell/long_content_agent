import json

jsonl_file = "/opt/mengsen/long_content/dataset/retrieval_llm_rerank/financial_contracts_contexts.jsonl"
json_file = "/opt/mengsen/long_content/dataset/retrieval_llm_rerank_json/financial_contracts_contexts.json"

data = []

with open(jsonl_file, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:  # 跳过空行
            data.append(json.loads(line))

with open(json_file, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=4)

print(f"转换完成，共 {len(data)} 条数据")