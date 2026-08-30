"""
scripts/normalizer_playground.py
محیط تست لوکال تعاملی — همه فیلدها + نرمال‌ساز + LLM

اجرا:
  set PYTHONIOENCODING=utf-8 && python scripts/normalizer_playground.py
  set PYTHONIOENCODING=utf-8 && python scripts/normalizer_playground.py --batch
  set PYTHONIOENCODING=utf-8 && python scripts/normalizer_playground.py --sample 5

دستورات داخل محیط:
  :q / exit / quit -> خروج
  :t 0.85 -> تغییر آستانه
  :type suggestion -> تغییر نوع دانش پیش‌فرض
  :sample 42 -> اجرای نمونه 42 از فایل 100تایی
  :batch -> اجرای همه 100 نمونه و گزارش
"""
import asyncio
import json
import os
import sys
import time

# ریشه پروژه
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
from utils.text_normalizer import normalize_for_llm, clear_cache
from engine.knowledge_ai import build_system_prompt, extract_fields, is_ai_enabled
from engine.knowledge_interview import build_interview_system_prompt, interview_next_turn

SAMPLES_PATH = os.path.join(ROOT, "scripts", "normalizer_test_100.json")

def load_samples():
    if not os.path.isfile(SAMPLES_PATH):
        return []
    with open(SAMPLES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def fmt_dict(d, indent=2):
    return json.dumps(d, ensure_ascii=False, indent=indent)

def diff_words(a: str, b: str) -> str:
    if a == b:
        return "(بدون تغییر)"
    return f"'{a[:120]}' -> '{b[:120]}'"

async def run_one(text: str, threshold: float, ktype: str, show_prompt: bool = False):
    clear_cache()
    t0 = time.time()
    norm = normalize_for_llm(text, threshold=threshold)
    elapsed = (time.time() - t0) * 1000

    print("\n" + "="*70)
    print(f"ورودی   : {text}")
    print(f"نرمال   : {norm}")
    print(f"تغییر   : {diff_words(text, norm)}")
    print(f"آستانه  : {threshold} | زمان: {elapsed:.1f}ms | نرمال‌ساز: {'فعال' if config.ENABLE_TEXT_NORMALIZER else 'غیرفعال'}")
    print(f"نوع دانش: {ktype}")

    # پرامپت‌ها
    if show_prompt:
        print("\n--- system prompt (extract) ---")
        sp = build_system_prompt(ktype)
        print(sp[:1200] + ("..." if len(sp) > 1200 else ""))
        print("\n--- system prompt (interview) ---")
        ip = build_interview_system_prompt(ktype, {"full_name": "تست", "position": "کارشناس", "project_name": "نیروگاه سیکل ترکیبی"})
        print(ip[:1500] + ("..." if len(ip) > 1500 else ""))

    # LLM
    if not is_ai_enabled():
        print("\n⚠️  AI غیرفعال (KNOWLEDGE_AI_API_KEY/MODEL تنظیم نیست) — فقط نرمال‌ساز نمایش داده شد")
        print("   برای دیدن همه فیلدها، متغیرهای محیطی را تنظیم کن و دوباره اجرا کن")
        return

    print("\n--- extract_fields (همه فیلدها) ---")
    t1 = time.time()
    res = await extract_fields(ktype, norm)
    t2 = (time.time() - t1) * 1000
    print(f"title      : {res.get('title')}")
    print(f"fields     : {fmt_dict(res.get('fields', {}))}")
    print(f"hashtags   : {res.get('hashtags')}")
    print(f"impact_type: {res.get('impact_type')}")
    print(f"classification: {fmt_dict(res.get('classification', {}))}")
    print(f"missing    : {res.get('missing')}")
    print(f"زمان LLM   : {t2:.0f}ms")

    print("\n--- interview_next_turn (سؤال بعدی) ---")
    t3 = time.time()
    r2 = await interview_next_turn(ktype, [], norm, {"full_name": "تست", "position": "کارشناس", "project_name": "نیروگاه سیکل ترکیبی"})
    t4 = (time.time() - t3) * 1000
    print(f"ask            : {r2.get('ask')}")
    print(f"extracted      : {fmt_dict(r2.get('extracted') or {})}")
    print(f"switch_to_type : {r2.get('switch_to_type')}")
    print(f"done           : {r2.get('done')}")
    print(f"error          : {r2.get('error')}")
    print(f"زمان LLM       : {t4:.0f}ms")

    # مقایسه بدون نرمال هم (برای قضاوت)
    if text != norm and is_ai_enabled():
        print("\n--- مقایسه بدون نرمال (برای سنجش اثر) ---")
        res_raw = await extract_fields(ktype, text)
        print(f"fields(orig) : {fmt_dict(res_raw.get('fields', {}))}")
        print(f"missing(orig): {res_raw.get('missing')}")
        print(f"نتیجه: {'✅ نرمال بهتر/مساوی' if len(res.get('missing', [])) <= len(res_raw.get('missing', [])) else '⚠️ بدون نرمال بهتر'}")

async def batch_run(threshold: float = 0.90):
    samples = load_samples()
    if not samples:
        print("فایل نمونه یافت نشد")
        return
    ok = 0
    total = len(samples)
    norm_times = []
    for s in samples:
        inp = s["input"]
        expect = s.get("expect")
        t0 = time.time()
        out = normalize_for_llm(inp, threshold=threshold)
        norm_times.append((time.time()-t0)*1000)
        if expect is None:
            passed = out == inp
        else:
            passed = expect in out
        if passed:
            ok += 1
        else:
            print(f"FAIL #{s['id']}: {inp!r} -> {out!r} expect {expect!r} [{s['cat']}]")
    avg = sum(norm_times)/len(norm_times) if norm_times else 0
    print("\n" + "="*70)
    print(f"Batch: {ok}/{total} = {ok/total*100:.1f}% | میانگین {avg:.1f}ms | p95 {sorted(norm_times)[int(len(norm_times)*0.95)]:.1f}ms")
    if is_ai_enabled():
        print("در حال اجرای LLM روی 5 نمونه اول برای نمایش اثر...")
        for s in samples[:5]:
            await run_one(s["input"], threshold, s.get("type","lesson"))

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", action="store_true", help="اجرای 100 نمونه")
    ap.add_argument("--sample", type=int, default=None, help="اجرای یک نمونه با id")
    ap.add_argument("--threshold", type=float, default=0.90)
    ap.add_argument("--type", default="lesson", choices=["lesson","suggestion","explicit"])
    args = ap.parse_args()

    threshold = args.threshold
    ktype = args.type

    if args.batch:
        asyncio.run(batch_run(threshold))
        return
    if args.sample is not None:
        samples = load_samples()
        s = next((x for x in samples if x["id"] == args.sample), None)
        if not s:
            print(f"نمونه {args.sample} یافت نشد")
            return
        asyncio.run(run_one(s["input"], threshold, s.get("type", ktype), show_prompt=True))
        return

    # حالت تعاملی
    print("محیط تست لوکال نرمال‌ساز — همه فیلدها")
    print(f"AI: {'فعال' if is_ai_enabled() else 'غیرفعال'} | آستانه: {threshold} | نوع پیش‌فرض: {ktype}")
    print("دستورات: :q خروج | :t 0.85 تغییر آستانه | :type suggestion | :sample 5 | :batch | :prompt")
    print("-"*70)
    show_prompt = False
    while True:
        try:
            raw = input(f"\n[{ktype} t={threshold}]> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nخروج")
            break
        if not raw:
            continue
        if raw in (":q", "exit", "quit", ":quit"):
            break
        if raw.startswith(":t "):
            try:
                threshold = float(raw.split()[1])
                print(f"آستانه -> {threshold}")
            except:
                print("فرمت: :t 0.85")
            continue
        if raw.startswith(":type "):
            v = raw.split()[1]
            if v in ("lesson","suggestion","explicit"):
                ktype = v
                print(f"نوع -> {ktype}")
            continue
        if raw.startswith(":sample "):
            try:
                sid = int(raw.split()[1])
                samples = load_samples()
                s = next((x for x in samples if x["id"] == sid), None)
                if s:
                    asyncio.run(run_one(s["input"], threshold, s.get("type", ktype), show_prompt))
                else:
                    print("یافت نشد")
            except Exception as e:
                print(e)
            continue
        if raw in (":batch",):
            asyncio.run(batch_run(threshold))
            continue
        if raw in (":prompt", ":p"):
            show_prompt = not show_prompt
            print(f"نمایش پرامپت: {show_prompt}")
            continue
        # متن عادی
        asyncio.run(run_one(raw, threshold, ktype, show_prompt))

if __name__ == "__main__":
    main()
