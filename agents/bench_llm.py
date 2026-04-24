#!/usr/bin/env python3
"""
Benchmark Ollama models for KBZ agent decision quality and latency.

Usage:
    python -m agents.bench_llm [--model qwen3:8b] [--runs 5]

Tests both think=False and think=True (if the model supports it).
Prints a table with latency and JSON-output quality scores.
"""
import argparse
import asyncio
import json
import re
import time

# ── Minimal sample prompt (representative KBZ decision context) ─────────────
SAMPLE_PROMPT = """\
You are Leila Hassan, Educator in the AI Kibbutz community.
Background: Believes in evidence-based governance and collaborative learning.
Traits: very open to new ideas, highly cooperative, proactive, socially active.

Community state:
- Round 7, 3 members, 2 active proposals, pulse threshold 50%
- Proposals OutThere:
  [P-1] AddStatement: "All members must attend weekly standups" — 1 support
  [P-2] CreateArtifact (title: "Morning Stand-Up Procedure") in container [C-1] — 0 support
- Pulse supporters so far: 0/3

Your recent actions: [observe, observe]

IMPORTANT: Respond ONLY with a JSON array of 1-3 action objects. No prose.

Available actions: create_proposal, support_proposal, support_pulse, add_comment, do_nothing

JSON format:
[{"action": "...", "reason": "...", "eagerness": 1-10, "eager_front": "observe|propose|pulse|comment|support|produce"}]
"""

# ── Scoring helpers ──────────────────────────────────────────────────────────

def score_response(raw: str) -> dict:
    """Parse and score an LLM response for JSON quality."""
    # Strip think tags
    clean = re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    # Strip markdown fences
    clean = clean.strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    clean = clean.strip()
    if clean.startswith("json"):
        clean = clean[4:].strip()

    think_chars = len(raw) - len(re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", raw, flags=re.DOTALL | re.IGNORECASE))

    result = {
        "raw_chars": len(raw),
        "think_chars": think_chars,
        "parseable": False,
        "is_array": False,
        "n_actions": 0,
        "valid_actions": 0,
        "errors": [],
    }

    try:
        data = json.loads(clean)
    except json.JSONDecodeError as e:
        # Try to salvage array
        arr_start = clean.find("[")
        if arr_start >= 0:
            try:
                data = json.loads(clean[arr_start:clean.rfind("]") + 1])
            except Exception:
                result["errors"].append(f"JSON parse error: {e}")
                return result
        else:
            result["errors"].append(f"JSON parse error: {e}")
            return result

    result["parseable"] = True
    if isinstance(data, list):
        result["is_array"] = True
        result["n_actions"] = len(data)
        valid = 0
        for item in data:
            if isinstance(item, dict) and "action" in item and "reason" in item:
                valid += 1
            else:
                result["errors"].append(f"Missing fields in: {item}")
        result["valid_actions"] = valid
    elif isinstance(data, dict):
        result["is_array"] = False
        result["n_actions"] = 1
        if "action" in data and "reason" in data:
            result["valid_actions"] = 1
        else:
            result["errors"].append("Single object missing 'action' or 'reason'")
    return result


# ── Benchmark runner ─────────────────────────────────────────────────────────

async def run_bench(model: str, think: bool, runs: int, num_ctx: int, num_predict: int, timeout: float) -> list[dict]:
    import httpx
    import ollama

    client = ollama.AsyncClient(timeout=httpx.Timeout(timeout, connect=30.0))
    think_label = "think=ON " if think else "think=OFF"
    print(f"\n{'─'*60}")
    print(f"  Model: {model}  |  {think_label}  |  {runs} runs")
    print(f"{'─'*60}")

    results = []
    for i in range(runs):
        t0 = time.perf_counter()
        try:
            response = await client.chat(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a JSON-only decision engine. Output ONLY a JSON array."},
                    {"role": "user", "content": SAMPLE_PROMPT},
                ],
                think=think,
                options={
                    "num_ctx": num_ctx,
                    "temperature": 0.7,
                    "num_predict": num_predict,
                },
            )
            elapsed = time.perf_counter() - t0
            # With think=True: content = final answer, thinking = reasoning trace
            # With think=False: content = final answer, thinking = None/""
            content = response.message.content or ""
            thinking_text = getattr(response.message, "thinking", "") or ""
            # Fall back to thinking field only if content is empty (shouldn't happen with adequate num_predict)
            raw = content if content.strip() else thinking_text
            score = score_response(raw)
            score["latency_s"] = round(elapsed, 2)
            score["think_chars"] = len(thinking_text)  # overwrite with actual thinking field size
            score["run"] = i + 1
            score["error"] = None
        except Exception as e:
            elapsed = time.perf_counter() - t0
            score = {
                "run": i + 1, "latency_s": round(elapsed, 2),
                "raw_chars": 0, "think_chars": 0,
                "parseable": False, "is_array": False,
                "n_actions": 0, "valid_actions": 0,
                "errors": [str(e)], "error": str(e),
            }

        status = "✓" if score["parseable"] and score["valid_actions"] > 0 else "✗"
        think_info = f" (think: {score.get('think_chars', 0):,} chars)" if think and score.get("think_chars") else ""
        print(f"  Run {i+1:2d}: {score['latency_s']:6.1f}s  {status}  "
              f"{score['n_actions']} actions  {score['raw_chars']:,} chars{think_info}"
              + (f"  ⚠ {score['errors']}" if score["errors"] else ""))
        results.append(score)

    return results


def summarize(results: list[dict], model: str, think: bool) -> dict:
    latencies = [r["latency_s"] for r in results if r["error"] is None]
    valid = [r for r in results if r["parseable"] and r["valid_actions"] > 0]
    return {
        "model": model,
        "think": think,
        "runs": len(results),
        "success_rate": f"{len(valid)}/{len(results)}",
        "avg_latency_s": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "min_latency_s": round(min(latencies), 1) if latencies else None,
        "max_latency_s": round(max(latencies), 1) if latencies else None,
        "avg_think_chars": round(
            sum(r.get("think_chars", 0) for r in results) / len(results)
        ),
    }


async def main():
    parser = argparse.ArgumentParser(description="Benchmark Ollama models for KBZ agent quality")
    parser.add_argument("--model", default="qwen3:8b", help="Ollama model to test (default: qwen3:8b)")
    parser.add_argument("--runs", type=int, default=5, help="Number of test runs per mode (default: 5)")
    parser.add_argument("--num-ctx", type=int, default=8192, help="Context window (default: 8192)")
    parser.add_argument("--num-predict", type=int, default=512, help="Max output tokens (default: 512)")
    parser.add_argument("--timeout", type=float, default=120.0, help="Request timeout seconds (default: 120)")
    parser.add_argument("--think-only", action="store_true", help="Only test think=True")
    parser.add_argument("--no-think-only", action="store_true", help="Only test think=False")
    args = parser.parse_args()

    summaries = []

    if not args.think_only:
        results_off = await run_bench(args.model, think=False, runs=args.runs,
                                      num_ctx=args.num_ctx, num_predict=args.num_predict,
                                      timeout=args.timeout)
        summaries.append(summarize(results_off, args.model, think=False))

    if not args.no_think_only:
        results_on = await run_bench(args.model, think=True, runs=args.runs,
                                     num_ctx=args.num_ctx, num_predict=args.num_predict,
                                     timeout=args.timeout)
        summaries.append(summarize(results_on, args.model, think=True))

    print(f"\n{'═'*65}")
    print(f"  BENCHMARK SUMMARY — {args.model}")
    print(f"{'═'*65}")
    header = f"  {'Mode':<20} {'Success':>8} {'Avg (s)':>9} {'Min (s)':>9} {'Max (s)':>9} {'AvgThink':>10}"
    print(header)
    print(f"  {'-'*62}")
    for s in summaries:
        mode = f"think={'ON ' if s['think'] else 'OFF'}"
        think_str = f"{s['avg_think_chars']:>8,}" if s["avg_think_chars"] else "        —"
        print(f"  {mode:<20} {s['success_rate']:>8} {str(s['avg_latency_s']):>9} "
              f"{str(s['min_latency_s']):>9} {str(s['max_latency_s']):>9} {think_str}")
    print(f"{'═'*65}\n")


if __name__ == "__main__":
    asyncio.run(main())
