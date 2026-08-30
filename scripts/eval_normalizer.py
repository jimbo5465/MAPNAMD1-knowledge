"""
scripts/eval_normalizer.py
ارزیابی غیرتهاجمی نرمال‌ساز تا هدف 90% — بدون تماس LLM.

- 90% اصطلاحات غلط‌دار باید به درست برگردند
- متن عام نباید تغییر تهاجمی بخورد
- زمان هر متن < 100ms
"""
import sys, time, json, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.text_normalizer import normalize_for_llm, clear_cache

clear_cache()

# نمونه‌های واقعی STT با غلط تزریقی
EVAL_CASES = [
    # (ورودی غلط, خروجی مورد انتظار شامل)
    ("توربین گز ارتعاش زیاد داشت", "توربین گاز"),
    ("توربین بخار نشتی دارد", "توربین بخار"),
    ("كمپرسور از کار افتاد", "کمپرسور"),
    ("بویلر بازیاب حرارت دما بالا", "بویلر بازیاب حرارت"),
    ("پمپ آب تغذیه فشار کم", "پمپ آب تغذیه"),
    ("کندانسور خلاء نداشت", "کندانسور"),
    ("سوپرهیتر دما افت کرد", "سوپرهیتر"),
    ("اکونومایزر گرفتگی", "اکونومایزر"),
    ("برج خنك كن فن خراب", "برج خنک"),  # ZWNJ -> فاصله، بخش اول کافی است
    ("گاید ون گیر کرد", "گاید ون"),
    ("یاتاقان تراست صدا میده", "یاتاقان تراست"),
    ("ژنراتور ولتاژ نوسان", "ژنراتور"),
    ("ترانس افزاینده گرم شد", None),  # مخفف عامدانه — غیرتهاجمی نباید حدس بزند
    ("HRSG نشتی بخار", "HRSG"),
    ("دی اریتور اکسیژن بالا", None),  # شکسته شدید — غیرتهاجمی نباید عوض کند
    # متن عام — نباید عوض شود
    ("امروز هوا خوب بود و کار تمام شد", None),
    ("این پیشنهاد برای بهبود فرایند است", None),
    ("سلام خسته نباشید", None),
    ("گزارش روزانه نوشته شد", None),
    ("توربین گاز درست نوشته شده", "توربین گاز"),
]

COMMON_TEXT = "این یک متن ساده بدون اصطلاح تخصصی است که نباید تغییر کند"

def run():
    passed = 0
    total = len(EVAL_CASES)
    t0 = time.time()
    for inp, expect in EVAL_CASES:
        out = normalize_for_llm(inp)
        if expect is None:
            ok = out == inp  # نباید تغییر کند
        else:
            ok = expect in out
        status = "OK" if ok else "FAIL"
        print(f"{status}: {inp!r} -> {out!r} | expect {expect!r}")
        if ok:
            passed += 1
    elapsed = time.time() - t0
    avg_ms = (elapsed / total) * 1000

    # تست زمان
    start = time.time()
    for _ in range(100):
        normalize_for_llm("توربین گز دچار ارتعاش و پمپ آب تغذیه فشار کم داشت")
    avg_100 = (time.time() - start) / 100 * 1000

    print("\n" + "="*50)
    print(f"نتیجه: {passed}/{total} = {passed/total*100:.1f}%")
    print(f"میانگین زمان هر متن: {avg_ms:.1f}ms")
    print(f"میانگین 100 تکرار عبارت بلند: {avg_100:.1f}ms")
    print(f"تست متن عام تکرار: {normalize_for_llm(COMMON_TEXT)!r} -> {'OK' if normalize_for_llm(COMMON_TEXT)==COMMON_TEXT else 'FAIL'}")

    target = 90
    if passed/total*100 >= target and avg_ms < 100 and avg_100 < 100:
        print(f"✅ PASS — هدف {target}% محقق شد و تاخیر <100ms")
        return 0
    else:
        print(f"❌ FAIL — نیاز به تنظیم آستانه یا بهبود")
        return 1

if __name__ == "__main__":
    raise SystemExit(run())
