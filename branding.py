"""هوية القروب: الصورة والاسم والوصف. يتشغل يدويًا من GitHub Actions (Set Telegram branding)."""
import os
import requests

TITLE = "فساتين 👗"
DESCRIPTION = (
    "👗 فساتين سهرة ومناسبات مختارة بعناية\n"
    "💍 أعراس • خطوبة • حفلات • تخرج\n"
    "✨ تصاميم فخمة وألوان راقية بأسعار تناسبك\n"
    "🛍️ AliExpress | SHEIN\n"
    "🔗 roretashoping-netizen.github.io/deals-bot"
)

api = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/"
chat = os.environ["TELEGRAM_CHANNEL"]


def call(method, **kw):
    r = requests.post(api + method, timeout=60, **kw).json()
    print(method, "OK" if r.get("ok") else f"فشل: {r.get('description')}")
    # لو القيمة نفسها ما تغيرت، نعتبرها نجاح
    return r.get("ok") or "not modified" in str(r.get("description", ""))


with open("data/image.png", "rb") as f:
    ok1 = call("setChatPhoto", data={"chat_id": chat}, files={"photo": ("logo.png", f, "image/png")})
ok2 = call("setChatTitle", data={"chat_id": chat, "title": TITLE})
ok3 = call("setChatDescription", data={"chat_id": chat, "description": DESCRIPTION})
print("طول الوصف:", len(DESCRIPTION))
if not (ok1 and ok2 and ok3):
    raise SystemExit(1)
