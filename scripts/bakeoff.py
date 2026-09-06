import argparse
import datetime
import json
import logging
import pathlib
import re
import statistics
import subprocess
import threading
import time

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

SNIPPETS = [
    {
        "text": "Lockheed Martin was awarded a $15M contract to supply 20 next-generation Sniper Advanced Targeting Pods for the US Air Force F-16 fleet. The pods feature enhanced MWIR sensors and edge AI processing.",
        "expected_domain": "airborne_pods"
    },
    {
        "text": "Anduril demonstrated its new Anvil C-UAS drone at Yuma Proving Ground, successfully intercepting a Group 3 surrogate target. The system uses kinetic defeat.",
        "expected_domain": "c_uas"
    },
    {
        "text": "Raytheon completed a captive carry test of a new cooled MWIR seeker for the AIM-9X Block II, demonstrating extended acquisition range.",
        "expected_domain": "air_defense"
    },
    {
        "text": "The Royal Navy has signed an agreement to procure Sea-Eagle EO/IR directors for its Type 31 frigates to improve passive target tracking capabilities.",
        "expected_domain": "naval_surveillance"
    },
    {
        "text": "The Ministry of Defense issued a tender for autonomous border surveillance towers equipped with long-range thermal imagers, ground surveillance radar, and laser rangefinders.",
        "expected_domain": "land_surveillance"
    },
    {
        "text": "We propose a novel thermal Automatic Target Recognition (ATR) architecture based on YOLOv8. Our model achieves 95% mAP on the DSIAC dataset while operating at 60 FPS on edge TPU hardware.",
        "expected_domain": "computer_vision"
    },
    {
        "text": "Defense conglomerate Elbit Systems announced the acquisition of drone-detection startup Fortem Technologies for $200 million, aiming to bolster its multi-layered air defense portfolio.",
        "expected_domain": "c_uas"
    },
    {
        "text": "Unconfirmed reports suggest a Middle Eastern country is secretly negotiating to purchase a fleet of retired MQ-9 Reapers equipped with Gorgon Stare pods, though government officials deny the claims.",
        "expected_domain": "secondary"
    }
]

SCHEMA_CLASSIFY = {
    "type": "object",
    "properties": {
        "domain": {
            "type": "string",
            "enum": ["airborne_pods", "land_surveillance", "naval_surveillance", "air_defense", "c_uas", "computer_vision", "secondary"]
        },
        "report_kind": {
            "type": "string",
            "enum": ["verified_report", "company_pr", "rumor_speculation", "academic", "tender"]
        },
        "entities": {
            "type": "array",
            "items": {"type": "string"}
        },
        "score_1_10": {
            "type": "integer"
        },
        "level": {
            "type": "string",
            "enum": ["red", "orange", "yellow", "archive"]
        },
        "reason_he": {
            "type": "string"
        }
    },
    "required": ["domain", "report_kind", "entities", "score_1_10", "level", "reason_he"]
}

HEBREW_CLUNKY = (
    "המערכת של המכ\"ם היא עושה גילוי של המטרות באוויר ואז היא שולחת את הנתונים למערכת אש. "
    "המערכת הזאת היא מאד טובה כי יש לה טווח של 100 קילומטר והיא עובדת גם בלילה. "
    "החברה אומרת שזה מיועד לשימוש נגד רחפנים."
)

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search the web for information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "lang": {"type": "string", "description": "Language code (e.g., 'en')"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fetch",
            "description": "Fetch content of a URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Provide the final answer in Hebrew and cite sources.",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer_he": {"type": "string", "description": "Final answer in Hebrew"},
                    "sources": {"type": "array", "items": {"type": "string"}, "description": "List of source URLs used"}
                },
                "required": ["answer_he", "sources"]
            }
        }
    }
]

def check_cpu_offload(client: httpx.Client, model_name: str) -> str:
    try:
        r = client.get("http://127.0.0.1:11434/api/ps", timeout=10)
        r.raise_for_status()
        models = r.json().get('models', [])
        for m in models:
            if m.get('name') == model_name:
                size = m.get('size', 0)
                size_vram = m.get('size_vram', 0)
                return "Yes" if size_vram < size else "No"
    except Exception as e:
        logging.warning(f"Failed to check api/ps: {e}")
    return "Unknown"

class GPUPoller(threading.Thread):
    def __init__(self):
        super().__init__()
        self.running = False
        self.peak_mem = 0
        self.max_temp = 0

    def run(self):
        self.running = True
        while self.running:
            try:
                res = subprocess.check_output(
                    ['nvidia-smi', '--query-gpu=memory.used,utilization.gpu,temperature.gpu', '--format=csv,noheader,nounits'],
                    text=True, stderr=subprocess.STDOUT
                )
                for line in res.strip().split('\n'):
                    parts = line.split(',')
                    if len(parts) >= 3:
                        try:
                            mem = int(parts[0].strip())
                            temp = int(parts[2].strip())
                            if mem > self.peak_mem:
                                self.peak_mem = mem
                            if temp > self.max_temp:
                                self.max_temp = temp
                        except ValueError:
                            pass
            except Exception:
                pass
            time.sleep(1)

    def stop(self):
        self.running = False
        self.join()

def get_tok_s(data):
    if data.get("eval_duration", 0) > 0 and data.get("eval_count", 0) > 0:
        return data["eval_count"] / (data["eval_duration"] / 1e9)
    return 0.0

def task_classify(client, model, num_ctx):
    results = []
    for snippet in SNIPPETS:
        payload = {
            "model": model,
            "stream": False,
            "think": False,
            "messages": [
                {"role": "system", "content": "You are a defense-OSINT analyst. Analyze the following article snippet and output JSON matching the schema."},
                {"role": "user", "content": snippet["text"]}
            ],
            "format": SCHEMA_CLASSIFY,
            "options": {"temperature": 0.1, "num_ctx": num_ctx, "num_predict": 1500}
        }
        start = time.perf_counter()
        try:
            r = client.post("http://127.0.0.1:11434/api/chat", json=payload, timeout=300)
            r.raise_for_status()
            data = r.json()
            dur = time.perf_counter() - start
            msg = data.get("message", {}).get("content", "")
            tok_s = get_tok_s(data)

            is_valid_json = False
            has_schema = False
            domain_match = False

            try:
                parsed = json.loads(msg)
                is_valid_json = True
                has_schema = all(k in parsed for k in SCHEMA_CLASSIFY["required"])
                if parsed.get("domain") == snippet["expected_domain"]:
                    domain_match = True
            except Exception:
                pass

            results.append({
                "latency": dur,
                "tok_s": tok_s,
                "json_valid": is_valid_json,
                "schema_valid": has_schema,
                "domain_match": domain_match,
                "raw": msg
            })
        except Exception as e:
            logging.error(f"Classify error for {model}: {e}")

    if not results:
        return None

    return {
        "json_valid_rate": statistics.mean([1 if r["json_valid"] else 0 for r in results]),
        "schema_valid_rate": statistics.mean([1 if r["schema_valid"] else 0 for r in results]),
        "domain_acc": statistics.mean([1 if r["domain_match"] else 0 for r in results]),
        "mean_tok_s": statistics.mean([r["tok_s"] for r in results]),
        "mean_latency": statistics.mean([r["latency"] for r in results]),
        "raw_outputs": [r["raw"] for r in results]
    }

def task_summarize_he(client, model, num_ctx):
    results = []
    for snippet in SNIPPETS[:4]:
        prompt = (
            "Please write a 3-sentence summary in Hebrew of the following article, plus one 'מה זה אומר' "
            "(so-what) sentence. Technical terms should be kept in English in parentheses. Do not invent any facts.\n\n"
            f"Article:\n{snippet['text']}"
        )
        payload = {
            "model": model,
            "stream": False,
            "think": False,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0.1, "num_ctx": num_ctx, "num_predict": 1500}
        }
        start = time.perf_counter()
        try:
            r = client.post("http://127.0.0.1:11434/api/chat", json=payload, timeout=300)
            r.raise_for_status()
            data = r.json()
            dur = time.perf_counter() - start
            msg = data.get("message", {}).get("content", "")
            tok_s = get_tok_s(data)

            heb_chars = sum(1 for c in msg if '\u0590' <= c <= '\u05FF')
            letters = sum(1 for c in msg if c.isalpha())
            heb_ratio = heb_chars / letters if letters > 0 else 0.0

            nums = re.findall(r'\b\d+(?:[.,]\d+)?\b', snippet['text'])
            nums_preserved = 1.0
            if nums:
                preserved = sum(1 for n in nums if n in msg)
                nums_preserved = preserved / len(nums)

            results.append({
                "latency": dur,
                "tok_s": tok_s,
                "heb_ratio": heb_ratio,
                "nums_preserved": nums_preserved,
                "raw": msg
            })
        except Exception as e:
            logging.error(f"Summarize error for {model}: {e}")

    if not results:
        return None

    return {
        "mean_tok_s": statistics.mean([r["tok_s"] for r in results]),
        "mean_latency": statistics.mean([r["latency"] for r in results]),
        "mean_heb_ratio": statistics.mean([r["heb_ratio"] for r in results]),
        "mean_nums_preserved": statistics.mean([r["nums_preserved"] for r in results]),
        "raw_outputs": [r["raw"] for r in results]
    }

def run_react_scenario(client, model, num_ctx, query, is_found):
    messages = [{"role": "user", "content": query}]
    latency_total = 0.0
    turns = 0
    react_ok = 0
    honest = 0
    called_search = False
    finished = False

    start = time.perf_counter()
    for _ in range(6):
        turns += 1
        payload = {
            "model": model,
            "stream": False,
            "think": False,
            "messages": messages,
            "tools": TOOLS_SCHEMA,
            "options": {"temperature": 0.1, "num_ctx": num_ctx, "num_predict": 1500}
        }
        try:
            r = client.post("http://127.0.0.1:11434/api/chat", json=payload, timeout=300)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logging.error(f"ReAct error for {model}: {e}")
            break

        msg = data.get("message", {})
        messages.append(msg)

        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                name = tc.get("function", {}).get("name")
                args_dict = tc.get("function", {}).get("arguments", {})

                tool_msg = {"role": "tool", "name": name}

                if name == "search":
                    called_search = True
                    if is_found:
                        content = json.dumps([{"title": "Army announces 2025 targeting pod winner", "url": "http://defnews.com/army-2025-pod"}])
                    else:
                        content = json.dumps([])
                    tool_msg["content"] = content
                    messages.append(tool_msg)
                elif name == "fetch":
                    content = 'The 2025 US Army next-generation targeting pod contract was awarded to L3Harris Technologies. They beat competing bids from Lockheed Martin and Northrop Grumman.'
                    tool_msg["content"] = content
                    messages.append(tool_msg)
                elif name == "finish":
                    finished = True
                    sources = args_dict.get("sources", [])
                    if is_found:
                        if len(sources) > 0 and called_search:
                            react_ok = 1
                    else:
                        if len(sources) == 0:
                            honest = 1
                    break
            if finished:
                break
        else:
            break

    latency_total = time.perf_counter() - start
    return {
        "turns": turns,
        "react_ok": react_ok,
        "honest": honest,
        "latency_total": latency_total,
        "messages": messages
    }

def task_react_tools(client, model, num_ctx):
    q1 = "Find who won the 2025 US Army next-generation targeting pod contract"
    res1 = run_react_scenario(client, model, num_ctx, q1, True)

    q2 = "Find information about the 2026 Elbit acquisition of Fortem Technologies for $2.1B"
    res2 = run_react_scenario(client, model, num_ctx, q2, False)

    return {
        "react_ok": res1["react_ok"],
        "not_found_honest": res2["honest"],
        "turns_1": res1["turns"],
        "turns_2": res2["turns"],
        "latency_total": res1["latency_total"] + res2["latency_total"],
        "raw_outputs": [res1["messages"], res2["messages"]]
    }

def task_hebrew_edit(client, model, num_ctx):
    prompt = (
        "Rewrite the following paragraph into fluent, professional Hebrew suitable for a senior intelligence analyst. "
        "Keep all facts intact, improve the vocabulary, and use passive or professional phrasing where appropriate.\n\n"
        f"Paragraph:\n{HEBREW_CLUNKY}"
    )
    payload = {
        "model": model,
            "stream": False,
            "think": False,
        "messages": [{"role": "user", "content": prompt}],
        "options": {"temperature": 0.1, "num_ctx": num_ctx, "num_predict": 1500}
    }
    start = time.perf_counter()
    try:
        r = client.post("http://127.0.0.1:11434/api/chat", json=payload, timeout=300)
        r.raise_for_status()
        data = r.json()
        dur = time.perf_counter() - start
        msg = data.get("message", {}).get("content", "")
        tok_s = get_tok_s(data)

        len_ratio = len(msg) / len(HEBREW_CLUNKY) if len(HEBREW_CLUNKY) > 0 else 0

        return {
            "tok_s": tok_s,
            "latency": dur,
            "len_ratio": len_ratio,
            "raw": msg
        }
    except Exception as e:
        logging.error(f"Hebrew edit error for {model}: {e}")
        return None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="gemma4:12b,hf.co/dicta-il/DictaLM-3.0-Nemotron-12B-Instruct-GGUF:Q4_K_M")
    parser.add_argument("--out", default="evals/results")
    parser.add_argument("--tasks", default="classify,summarize_he,react_tools,hebrew_edit")
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--keep-alive", type=int, default=0)
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")

    client = httpx.Client()

    full_results = {}

    for model in models:
        logging.info(f"Evaluating model: {model}")
        poller = GPUPoller()
        poller.start()

        cpu_offload = "Unknown"
        model_res = {}

        for task in tasks:
            logging.info(f"Running task: {task} for {model}")
            res = None
            if task == "classify":
                res = task_classify(client, model, args.num_ctx)
            elif task == "summarize_he":
                res = task_summarize_he(client, model, args.num_ctx)
            elif task == "react_tools":
                res = task_react_tools(client, model, args.num_ctx)
            elif task == "hebrew_edit":
                res = task_hebrew_edit(client, model, args.num_ctx)

            model_res[task] = res

            if cpu_offload == "Unknown":
                cpu_offload = check_cpu_offload(client, model)

        poller.stop()
        model_res["gpu"] = {
            "peak_vram_mb": poller.peak_mem,
            "max_temp": poller.max_temp,
            "cpu_offload": cpu_offload
        }
        full_results[model] = model_res

        try:
            logging.info(f"Unloading model {model}")
            client.post("http://127.0.0.1:11434/api/generate", json={"model": model,
            "stream": False,
            "think": False, "keep_alive": args.keep_alive}, timeout=10)
        except Exception as e:
            logging.warning(f"Failed to unload {model}: {e}")

    json_path = out_dir / f"bakeoff_{timestamp}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(full_results, f, indent=2, ensure_ascii=False)

    md_lines = []
    md_lines.append(f"# Ollama Bakeoff Results ({timestamp})")
    md_lines.append("")
    md_lines.append("| model | tok/s classify | JSON valid % | domain acc % | tok/s he | hebrew ratio | react ok | not-found honest | peak VRAM MB | max temp | CPU offload? |")
    md_lines.append("|---|---|---|---|---|---|---|---|---|---|---|")

    for model, res in full_results.items():
        c = res.get("classify") or {}
        s = res.get("summarize_he") or {}
        r = res.get("react_tools") or {}
        g = res.get("gpu") or {}

        c_tok = f"{c.get('mean_tok_s', 0):.1f}"
        c_json = f"{c.get('json_valid_rate', 0)*100:.0f}%"
        c_acc = f"{c.get('domain_acc', 0)*100:.0f}%"

        s_tok = f"{s.get('mean_tok_s', 0):.1f}"
        s_heb = f"{s.get('mean_heb_ratio', 0)*100:.0f}%"

        r_ok = str(r.get('react_ok', 0))
        r_hon = str(r.get('not_found_honest', 0))

        vram = str(g.get('peak_vram_mb', 0))
        temp = str(g.get('max_temp', 0))
        offload = str(g.get('cpu_offload', 'Unknown'))

        row = f"| {model} | {c_tok} | {c_json} | {c_acc} | {s_tok} | {s_heb} | {r_ok} | {r_hon} | {vram} | {temp} | {offload} |"
        md_lines.append(row)

    md_content = "\n".join(md_lines)
    md_path = out_dir / f"bakeoff_{timestamp}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(md_content)

if __name__ == "__main__":
    main()
