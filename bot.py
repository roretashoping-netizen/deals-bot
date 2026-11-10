#!/usr/bin/env python3
"""
بوت تليجرام لعروض علي إكسبريس (أفلييت)
- يجيب منتجات رائجة من AliExpress Affiliate API
- يفلترها (تقييم، مبيعات، خصم) ويستبعد المكرر
- ينشرها في قناة تليجرام مع رابط الأفلييت
- الحد الافتراضي ٣٠ منشور باليوم موزعة على اليوم

الأوامر:
  python bot.py check            يتأكد من الإعدادات والاتصال
  python bot.py preview          يعرض ٥ منشورات بدون نشر
  python bot.py post [--count N] ينشر N منتج الحين (افتراضي ١) مع احترام الحد اليومي
  python bot.py run              يشتغل بشكل دائم وينشر حسب الجدول
"""
import argparse
import hashlib
import hmac
import html
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "data" / "state.json"
CONFIG_FILE = BASE_DIR / "config.json"
API_URL = "https://api-sg.aliexpress.com/sync"
RIYADH = timezone(timedelta(hours=3))


# ---------------------------------------------------------------- الإعدادات
def load_env():
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config():
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    secrets = {
        "app_key": os.environ.get("ALI_APP_KEY", ""),
        "app_secret": os.environ.get("ALI_APP_SECRET", ""),
        "tracking_id": os.environ.get("ALI_TRACKING_ID", "default"),
        "bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "channel": os.environ.get("TELEGRAM_CHANNEL", ""),
    }
    cfg.update(secrets)
    return cfg


# ---------------------------------------------------------------- الحالة
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"posted": {}, "daily": {}}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    # المنتج ما يتكرر خلال ٢١ يوم، وبعدها يقدر ينعاد
    cutoff = (datetime.now(RIYADH) - timedelta(days=21)).strftime("%Y-%m-%d")
    state["posted"] = {k: v for k, v in state["posted"].items() if v >= cutoff}
    state["daily"] = {k: v for k, v in state["daily"].items() if k >= cutoff}
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def today():
    return datetime.now(RIYADH).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- AliExpress API
def sign(params, secret):
    s = "".join(f"{k}{params[k]}" for k in sorted(params))
    return hmac.new(secret.encode(), s.encode(), hashlib.sha256).hexdigest().upper()


def ali_call(cfg, method, biz):
    params = {
        "app_key": cfg["app_key"],
        "method": method,
        "timestamp": str(int(time.time() * 1000)),
        "sign_method": "sha256",
        "format": "json",
        "v": "2.0",
    }
    params.update({k: str(v) for k, v in biz.items() if v not in (None, "")})
    for attempt in range(5):
        params["timestamp"] = str(int(time.time() * 1000))
        params.pop("sign", None)
        params["sign"] = sign(params, cfg["app_secret"])
        r = requests.post(API_URL, data=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        err = data.get("error_response")
        if err and "ApiCallLimit" in str(err.get("code")) and attempt < 4:
            time.sleep(1.5 + attempt)  # علي إكسبريس يوقف ثانية لو الطلبات سريعة، ننتظر ونعيد
            continue
        if err:
            raise RuntimeError(f"AliExpress API error: {err}")
        return data


def extract_products(data):
    """يطلع قائمة المنتجات من رد الـ API مهما كان اسم الرد."""
    for key, val in data.items():
        if key.endswith("_response"):
            res = val.get("resp_result", {})
            if str(res.get("resp_code")) not in ("200", "None") and res.get("resp_code") is not None:
                return []
            products = (res.get("result") or {}).get("products") or {}
            return products.get("product", []) if isinstance(products, dict) else products
    return []


def fetch_candidates(cfg):
    method = "aliexpress.affiliate.product.query"
    keywords = list(cfg["keywords"])
    random.shuffle(keywords)
    out = []
    for kw in keywords[: cfg.get("keywords_per_run", 4)]:
        biz = {
            "keywords": kw,
            "tracking_id": cfg["tracking_id"],
            "target_currency": cfg["currency"],
            "target_language": cfg["language"],
            "ship_to_country": cfg["ship_to"],
            "sort": "LAST_VOLUME_DESC",
            "page_size": 50,
            "page_no": random.randint(1, 5),
            "min_sale_price": cfg.get("min_price", ""),
            "max_sale_price": cfg.get("max_price", ""),
            "fields": "product_id,product_title,target_sale_price,target_original_price,"
            "target_sale_price_currency,discount,evaluate_rate,lastest_volume,"
            "product_main_image_url,promotion_link,commission_rate,first_level_category_name,"
            "product_detail_url,product_small_image_urls,second_level_category_name,shop_id,shop_name",
        }
        try:
            prods = extract_products(ali_call(cfg, method, biz))
            if not prods and method.endswith("hotproduct.query"):
                prods = extract_products(ali_call(cfg, "aliexpress.affiliate.product.query", biz))
        except Exception as e:  # نكمل بالكلمات الثانية
            print(f"[warn] {kw}: {e}", file=sys.stderr)
            continue
        out.extend(prods)
    return out


def num(x, default=0.0):
    try:
        return float(str(x).replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return default


def title_key(p):
    """أول ٦ كلمات من العنوان بدون أرقام ورموز، عشان نعرف المنتجات المتشابهة من بائعين مختلفين."""
    t = re.sub(r"[^a-z\u0600-\u06ff]+", " ", str(p.get("product_title", "")).lower())
    return " ".join(t.split()[:6])


def category(p):
    return str(p.get("second_level_category_name") or p.get("first_level_category_name") or "")


# ممنوع نهائيًا: المنتجات الجنسية وأدواتها والملابس المثيرة
ADULT_WORDS = [
    "sex", "sexy", "adult", "erotic", "exotic", "lingerie", "vibrat", "dildo", "masturbat", "bdsm",
    "bondage", "fetish", "condom", "lubric", "orgasm", "g-spot", "g spot", "anal", "nipple", "porn",
    "strap-on", "strapon", "penis", "vagin", "clitor", "stripper", "pole dance", "kegel", "love egg",
    "جنس", "جنسي", "مثير", "مثيرة", "اثارة", "إثارة", "هزاز", "شهوة", "لانجري", "قضيب", "مهبل",
    "حلمة", "شرجي", "عازل ذكري", "ملابس داخلية مثيرة", "بالغين", "للكبار",
]
# مستحضرات تجميل توضع على الجسم: علي إكسبريس ما يعطي شهاداتها (CPNP/CE)، فما ننشرها
COSMETIC_WORDS = [
    "lipstick", "lip gloss", "lip tint", "eyeshadow", "eye shadow", "foundation", "concealer", "mascara",
    "eyeliner", "blush", "bronzer", "highlighter", "primer", "setting spray", "nail polish", "gel polish",
    "cream", "serum", "lotion", "moisturiz", "sunscreen", "essential oil", "hair dye", "hair color",
    "perfume", "fragrance", "body wash", "shampoo", "face mask", "toner", "cleanser", "whitening",
    "روج", "أحمر شفاه", "احمر شفاه", "ملمع شفاه", "ظلال", "كريم أساس", "كريم اساس", "خافي عيوب", "ماسكرا",
    "كحل", "أحمر خدود", "احمر خدود", "هايلايتر", "برايمر", "طلاء أظافر", "طلاء اظافر", "مناكير", "جل أظافر",
    "كريم", "سيروم", "لوشن", "مرطب", "واقي شمس", "زيت عطري", "صبغة", "عطر", "شامبو", "ماسك للوجه", "قناع الوجه", "تونر",
    "غسول", "تبييض", "تفتيح",
]
# لو العنوان فيه وحدة من هذي، يكون أداة (فرشاة، منظم...) مو مستحضر
TOOL_WORDS = [
    "brush", "sponge", "puff", "organizer", "mirror", "case", "bag", "box", "holder", "applicator",
    "curler", "tool", "storage", "stand", "rack", "bottle", "atomizer", "dispenser", "lamp", "drill",
    "file", "clipper", "remover tool", "fan", "comb",
    "فرشاة", "فرش", "اسفنجة", "إسفنجة", "منظم", "مرآة", "مراية", "حقيبة", "شنطة", "صندوق", "علبة", "حامل",
    "أداة", "اداة", "أدوات", "ادوات", "مكواة", "مجفف", "رف", "زجاجة", "بخاخ فارغ", "مشط",
]


NOT_COSMETIC_WORDS = [
    "dress", "abaya", "gown", "pajama", "slipper", "handbag", "wallet", "shoes", "kitchen", "cup", "mug",
    "فستان", "عباية", "عبايه", "بيجامة", "شبشب", "شنطة", "حقيبة يد", "محفظة", "حذاء", "مطبخ", "كوب",
]


def has_any(text, words):
    return any(w in text for w in words)


def blocked(p):
    """يرجع سبب المنع لو المنتج ممنوع، وإلا None."""
    text = " ".join(str(p.get(k, "")) for k in
                    ("product_title", "first_level_category_name", "second_level_category_name")).lower()
    if has_any(text, ADULT_WORDS):
        return "adult"
    title = str(p.get("product_title", "")).lower()
    # الملابس والشنط ما تدخل فحص المستحضرات (مثلًا "فستان كريمي" لونه كريمي مو كريم)
    if is_clothing({}, p) or has_any(title, NOT_COSMETIC_WORDS):
        return None
    if has_any(title, COSMETIC_WORDS) and not has_any(title, TOOL_WORDS):
        return "cosmetic"
    return None


def pick(cfg, state, products, n):
    f = cfg["filters"]
    seen, good = set(state["posted"]), []
    recent_keys = set(state.get("recent_keys", []))
    recent_imgs = set(state.get("recent_imgs", []))
    for p in products:
        pid = str(p.get("product_id"))
        if pid in seen or not p.get("promotion_link") or not p.get("product_main_image_url"):
            continue
        if blocked(p):
            continue
        # نفس المنتج من بائع ثاني (نفس العنوان أو نفس الصورة) ما ينعاد
        if title_key(p) in recent_keys or p["product_main_image_url"] in recent_imgs:
            continue
        if num(p.get("evaluate_rate")) < f["min_rating_percent"]:
            continue
        if num(p.get("lastest_volume")) < f["min_orders"]:
            continue
        if num(p.get("discount")) < f["min_discount_percent"]:
            continue
        seen.add(pid)
        recent_keys.add(title_key(p))
        recent_imgs.add(p["product_main_image_url"])
        score = num(p.get("lastest_volume")) ** 0.5 + num(p.get("discount")) + num(p.get("commission_rate")) * 2
        good.append((score, p))
    good.sort(key=lambda t: t[0], reverse=True)
    top = [p for _, p in good[: n * 4]]
    random.shuffle(top)  # تنويع بين أفضل المنتجات
    # ما نحط منتجين ورا بعض من نفس الفئة أو نفس المتجر
    chosen, last_cat, shops = [], state.get("last_cat", ""), set()
    for p in top:
        if len(chosen) >= n:
            break
        if category(p) and category(p) == last_cat:
            continue
        if p.get("shop_id") and p.get("shop_id") in shops:
            continue
        chosen.append(p)
        last_cat = category(p)
        shops.add(p.get("shop_id"))
    for p in top:  # لو ما كفت، نكمّل من الباقي
        if len(chosen) >= n:
            break
        if p not in chosen:
            chosen.append(p)
    return chosen


def short_link(cfg, p):
    """يحوّل رابط المنتج لرابط أفلييت قصير (s.click.aliexpress.com/e/...)."""
    source = p.get("product_detail_url") or p.get("promotion_link")
    try:
        data = ali_call(cfg, "aliexpress.affiliate.link.generate", {
            "promotion_link_type": 0,
            "source_values": source,
            "tracking_id": cfg["tracking_id"],
            "ship_to_country": cfg["ship_to"],
        })
        res = data["aliexpress_affiliate_link_generate_response"]["resp_result"]["result"]
        links = res["promotion_links"]["promotion_link"]
        link = str(links[0]["promotion_link"]).strip() if links else ""
        if link:
            return link
    except Exception as e:
        print(f"[warn] تقصير الرابط فشل: {e}", file=sys.stderr)
    return p["promotion_link"]


def ships_to(cfg, p):
    """يتأكد من الدول اللي يوصلها المنتج: لكل دولة يسأل علي إكسبريس عن المنتج بشحن لها."""
    from concurrent.futures import ThreadPoolExecutor

    def check(code):
        if code == cfg["ship_to"]:  # البحث أصلًا مفلتر على هالدولة
            return True
        try:
            data = ali_call(cfg, "aliexpress.affiliate.productdetail.get", {
                "product_ids": p["product_id"],
                "target_currency": cfg["currency"],
                "target_language": cfg["language"],
                "ship_to_country": code,
                "tracking_id": cfg["tracking_id"],
                "fields": "product_id,target_sale_price",
            })
            prods = extract_products(data)
            return bool(prods and prods[0].get("target_sale_price"))
        except Exception as e:
            print(f"[warn] فحص الشحن {code}: {e}", file=sys.stderr)
            return False

    codes = [c["code"] for c in cfg.get("countries", [])]
    with ThreadPoolExecutor(max_workers=3) as ex:  # كل الدول مع بعض عشان أسرع
        results = list(ex.map(check, codes))
    return [c for c, ok in zip(codes, results) if ok]


CLOTHING_WORDS = ["clothing", "apparel", "dress", "wedding", "events", "sleepwear", "underwear",
                  "matching sets", "tops", "bottoms", "skirt", "jumpsuit", "abaya", "muslim", "hijab",
                  "sweater", "coat", "jacket", "suits", "blouse", "pants", "swimwear"]


def is_clothing(cfg, p):
    cats = (str(p.get("first_level_category_name", "")) + " " + str(p.get("second_level_category_name", ""))).lower()
    return any(w in cats for w in cfg.get("clothing_categories", CLOTHING_WORDS))


def gallery(p):
    imgs = p.get("product_small_image_urls") or []
    if isinstance(imgs, dict):
        imgs = imgs.get("string", [])
    return [u for u in imgs if isinstance(u, str) and u.startswith("http")]


# ---------------------------------------------------------------- تنسيق المنشور
def stars(rate_percent):
    r = round(num(rate_percent) / 20, 1)  # 96% -> 4.8
    return f"{r:g}" if r else ""


def money(x):
    """189.0 -> 189 ، 39.5 -> 39.50"""
    return f"{x:,.0f}" if float(x).is_integer() else f"{x:,.2f}"


def caption(cfg, p):
    title = html.escape(str(p.get("product_title", "")).strip())
    if len(title) > 120:
        title = title[:117].rsplit(" ", 1)[0] + "…"
    cur = cfg.get("currency_label", "ريال")
    price = num(p.get("target_sale_price"))
    old = num(p.get("target_original_price"))
    disc = int(num(p.get("discount")))
    sep = "┈┈┈┈┈┈┈┈┈┈┈┈"
    lines = [random.choice(cfg["headlines"]), "", f"<b>{title}</b>", "", sep]
    # الشطب على الرقم بس (بدون مسافات ولا عملة داخله) عشان يجي على السعر بالضبط في الجوال
    if old > price > 0:
        lines.append(f"💰 السعر الحين: <b>{money(price)}</b> {cur}")
        lines.append(f"🏷️ السعر قبل: <s>{money(old)}</s> {cur}")
        if disc:
            lines.append(f"🔥 خصم {disc}% · توفّر {money(old - price)} {cur}")
    elif price:
        lines.append(f"💰 السعر: <b>{money(price)}</b> {cur}")
    rating = stars(p.get("evaluate_rate"))
    vol = int(num(p.get("lastest_volume")))
    if rating or vol:
        lines.append(sep)
        if rating:
            lines.append(f"⭐ التقييم: {rating} من 5")
        if vol:
            lines.append(f"🛒 الطلبات: أكثر من {vol:,} طلب")
    ships = p.get("_ships")
    if ships:
        names = {c["code"]: f'{c["flag"]} {c["name"]}' for c in cfg.get("countries", [])}
        items = [names[c] for c in ships if c in names]
        lines += [sep, "🚚 الشحن متوفر لـ:"]
        lines += ["   ".join(items[i:i + 3]) for i in range(0, len(items), 3)]
    if p.get("shop_name"):
        lines += [sep, f"🏪 المتجر: {html.escape(str(p['shop_name']))}"]
    link = html.escape(p["promotion_link"], quote=True)
    lines += [sep, "", f'👈 <a href="{link}"><b>اضغط هنا للطلب</b></a>']
    if cfg.get("hashtags"):
        lines += ["", cfg["hashtags"]]
    if is_clothing(cfg, p):
        lines += ["", "📏 <b>تأكدي من مقاسك وفق الجدول الخاص بالبائع</b>"]
    text = "\n".join(lines)
    return text[:1020]


# ---------------------------------------------------------------- تليجرام
def tg(cfg, method, **payload):
    url = f"https://api.telegram.org/bot{cfg['bot_token']}/{method}"
    r = requests.post(url, json=payload, timeout=30)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram error: {data}")
    return data["result"]


def send_product(cfg, p):
    imgs = gallery(p)
    if is_clothing(cfg, p) and imgs:
        # الملابس: ألبوم صور (جدول المقاسات غالبًا يكون من آخر صور البائع)
        photos = [p["product_main_image_url"]] + [u for u in imgs[-3:] if u != p["product_main_image_url"]]
        media = [{"type": "photo", "media": u} for u in photos[:4]]
        media[0].update(caption=caption(cfg, p), parse_mode="HTML")
        tg(cfg, "sendMediaGroup", chat_id=cfg["channel"], media=media)
        return
    tg(
        cfg,
        "sendPhoto",
        chat_id=cfg["channel"],
        photo=p["product_main_image_url"],
        caption=caption(cfg, p),
        parse_mode="HTML",
        reply_markup={"inline_keyboard": [[{"text": cfg.get("button_text", "🛍️ اطلب الحين"), "url": p["promotion_link"]}]]},
    )


# ---------------------------------------------------------------- الأوامر
def cmd_check(cfg):
    ok = True
    for k in ("app_key", "app_secret", "bot_token", "channel"):
        if not cfg[k]:
            print(f"✗ ناقص: {k}")
            ok = False
    if not ok:
        return 1
    me = tg(cfg, "getMe")
    print(f"✓ البوت: @{me['username']}")
    chat = tg(cfg, "getChat", chat_id=cfg["channel"])
    print(f"✓ القناة: {chat.get('title')}")
    prods = fetch_candidates(cfg)
    print(f"✓ علي إكسبريس: {len(prods)} منتج مرشح")
    return 0


def cmd_fields(cfg):
    """فحص: يطبع كل البيانات اللي يرجعها علي إكسبريس لمنتج واحد."""
    base = {"keywords": "women evening dress", "tracking_id": cfg["tracking_id"],
            "target_currency": cfg["currency"], "target_language": "EN",
            "ship_to_country": cfg["ship_to"], "page_size": 5}
    d = ali_call(cfg, "aliexpress.affiliate.product.query", base)
    prods = extract_products(d)
    print("=== product.query (no fields param) ===")
    print(json.dumps(prods[0] if prods else d, ensure_ascii=False, indent=1)[:4000])
    if prods:
        pid = prods[0]["product_id"]
        dd = ali_call(cfg, "aliexpress.affiliate.productdetail.get", {
            "product_ids": pid, "target_currency": cfg["currency"], "target_language": "EN",
            "ship_to_country": cfg["ship_to"], "tracking_id": cfg["tracking_id"]})
        print("=== productdetail.get (no fields param) ===")
        print(json.dumps(dd, ensure_ascii=False, indent=1)[:5000])
    for days in (3, 5, 7, 10, 15):
        try:
            n = len(extract_products(ali_call(cfg, "aliexpress.affiliate.product.query", dict(base, delivery_days=days, page_size=50))))
        except Exception as e:
            n = f"error {e}"
        print(f"delivery_days={days}: {n}")
    return 0


def cmd_linktest(cfg):
    """فحص: يتتبع تحويلات الروابط بأشكالها المختلفة عشان نعرف وين تنكسر."""
    ua = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
    d = ali_call(cfg, "aliexpress.affiliate.product.query", {
        "keywords": "makeup brushes set", "tracking_id": cfg["tracking_id"],
        "target_currency": cfg["currency"], "target_language": cfg["language"],
        "ship_to_country": cfg["ship_to"], "page_size": 3})
    p = extract_products(d)[0]
    pid = p["product_id"]
    variants = {"A_query_promotion_link": p.get("promotion_link")}
    for name, src in [("B_detail_url_with_params", p.get("product_detail_url")),
                      ("C_clean_item_url", f"https://www.aliexpress.com/item/{pid}.html")]:
        try:
            g = ali_call(cfg, "aliexpress.affiliate.link.generate", {
                "promotion_link_type": 0, "source_values": src,
                "tracking_id": cfg["tracking_id"], "ship_to_country": cfg["ship_to"]})
            variants[name] = g["aliexpress_affiliate_link_generate_response"]["resp_result"]["result"]["promotion_links"]["promotion_link"][0]["promotion_link"]
        except Exception as e:
            variants[name] = f"ERR {e}"
    print("product_detail_url:", p.get("product_detail_url"))
    for name, url in variants.items():
        print(f"\n=== {name}: {url}")
        if not str(url).startswith("http"):
            continue
        try:
            r = requests.get(url, headers={"User-Agent": ua}, timeout=30, allow_redirects=True)
            for h in r.history:
                print(f"  {h.status_code} -> {h.headers.get('Location', '')[:400]}")
            print(f"  FINAL {r.status_code} {r.url[:400]}")
            body = r.text
            print(f"  body_len={len(body)} has_captcha={'captcha' in body.lower() or 'punish' in r.url} "
                  f"title={body[body.find('<title>'):body.find('</title>')][:120]!r}")
        except Exception as e:
            print("  request error:", e)
    return 0


def cmd_preview(cfg, state):
    prods = pick(cfg, state, fetch_candidates(cfg), 5)
    for p in prods:
        print(caption(cfg, p))
        print("-" * 40)
    print(f"{len(prods)} منتج جاهز")
    return 0


def cmd_post(cfg, state, count):
    d = today()
    done = state["daily"].get(d, 0)
    left = cfg["posts_per_day"] - done
    if left <= 0:
        print(f"وصلنا الحد اليومي ({cfg['posts_per_day']})")
        return 0
    count = min(count, left)
    prods = pick(cfg, state, fetch_candidates(cfg), count)
    sent = 0
    for p in prods:
        p["promotion_link"] = short_link(cfg, p)
        p["_ships"] = ships_to(cfg, p)
        print(f"منتج {p.get('product_id')}: يوصل لـ {','.join(p['_ships'])}")
        try:
            send_product(cfg, p)
        except Exception as e:
            print(f"[warn] فشل نشر {p.get('product_id')}: {e}", file=sys.stderr)
            continue
        state["posted"][str(p["product_id"])] = d
        state["recent_keys"] = (state.get("recent_keys", []) + [title_key(p)])[-400:]
        state["recent_imgs"] = (state.get("recent_imgs", []) + [p["product_main_image_url"]])[-400:]
        state["last_cat"] = category(p)
        state["daily"][d] = state["daily"].get(d, 0) + 1
        sent += 1
        save_state(state)
        if sent < len(prods):
            time.sleep(8)
    print(f"انتشر {sent} منتج. مجموع اليوم: {state['daily'].get(d, 0)}/{cfg['posts_per_day']}")
    return 0 if sent or not prods else 1


def in_window(cfg, now):
    start, end = cfg["post_hours"]  # مثلاً [9, 25] يعني ٩ الصبح لين ١ بالليل
    h = now.hour + now.minute / 60
    if h < start and end > 24:
        h += 24
    return start <= h < end


def cmd_run(cfg):
    start, end = cfg["post_hours"]
    gap = (end - start) * 3600 / cfg["posts_per_day"]
    print(f"البوت شغال: منشور كل {gap/60:.0f} دقيقة تقريبًا بين {start}:00 و {end % 24}:00 بتوقيت الرياض")
    while True:
        now = datetime.now(RIYADH)
        if in_window(cfg, now):
            state = load_state()
            try:
                cmd_post(cfg, state, 1)
            except Exception as e:
                print(f"[error] {e}", file=sys.stderr)
            time.sleep(gap + random.uniform(-120, 120))
        else:
            time.sleep(300)


def main():
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["check", "preview", "post", "run", "fields", "linktest"])
    ap.add_argument("--count", type=int, default=1)
    a = ap.parse_args()
    cfg = load_config()
    if a.command == "check":
        return cmd_check(cfg)
    if a.command == "fields":
        return cmd_fields(cfg)
    if a.command == "linktest":
        return cmd_linktest(cfg)
    if a.command == "preview":
        return cmd_preview(cfg, load_state())
    if a.command == "post":
        return cmd_post(cfg, load_state(), a.count)
    return cmd_run(cfg)


if __name__ == "__main__":
    sys.exit(main())
