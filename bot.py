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

import deals_site

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
            "product_detail_url,product_small_image_urls,second_level_category_name,shop_id,shop_name,promo_code_info",
        }
        try:
            prods = extract_products(ali_call(cfg, method, biz))
            if not prods and method.endswith("hotproduct.query"):
                prods = extract_products(ali_call(cfg, "aliexpress.affiliate.product.query", biz))
        except Exception as e:  # نكمل بالكلمات الثانية
            print(f"[warn] {kw}: {e}", file=sys.stderr)
            continue
        for p in prods:
            p["_kw"] = kw  # نعرف الفئة من كلمة البحث
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
    "غسول", "تبييض", "تفتيح", "بودرة", "powder", "palette", "باليت", "كونسيلر", "حواجب ملونة", "تينت",
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
    "atomizer", "refillable", "empty bottle", "زجاجة عطر فارغة", "قابلة لإعادة التعبئة",
    "فستان", "عباية", "عبايه", "بيجامة", "شبشب", "شنطة", "حقيبة يد", "محفظة", "حذاء", "مطبخ", "كوب",
]


def no_brand(shop_name):
    """المتاجر بدون اسم تجاري: فاضي، أو اسم تلقائي مثل Shop1105850050 Store أو Store 123456."""
    n = str(shop_name or "").strip().lower()
    if not n:
        return True
    n = re.sub(r"\s+", " ", n)
    if re.fullmatch(r"(shop|store)\s*\d+(\s*(store|shop))?", n):
        return True
    return sum(ch.isdigit() for ch in n) >= 6 and len(re.sub(r"[\d\s]|shop|store", "", n)) < 3


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
    # المنتج هو أول شي يذكره العنوان: "فرشاة لظلال العيون" أداة، و"بودرة حواجب مع فرشاة" مستحضر
    cos = [title.find(w) for w in COSMETIC_WORDS if w in title]
    tool = [title.find(w) for w in TOOL_WORDS if w in title]
    if cos and (not tool or min(cos) < min(tool)):
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
        if blocked(p) or no_brand(p.get("shop_name")):
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


def ensure_home_link(cfg):
    """رابط أفلييت للصفحة الرئيسية لعلي إكسبريس (نسويه مرة وحدة ونحفظه)."""
    f = deals_site.HOME_LINK_FILE
    if f.exists() and f.read_text(encoding="utf-8").strip():
        return
    try:
        data = ali_call(cfg, "aliexpress.affiliate.link.generate", {
            "promotion_link_type": 0,
            "source_values": cfg.get("home_url", "https://ar.aliexpress.com/"),
            "tracking_id": cfg["tracking_id"],
            "ship_to_country": cfg["ship_to"],
        })
        res = data["aliexpress_affiliate_link_generate_response"]["resp_result"]["result"]
        links = res["promotion_links"]["promotion_link"]
        link = str(links[0]["promotion_link"]).strip() if links else ""
        if link.startswith("http"):
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(link, encoding="utf-8")
            print(f"رابط الصفحة الرئيسية: {link}")
    except Exception as e:
        print(f"[warn] رابط الصفحة الرئيسية فشل: {e}", file=sys.stderr)


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
    title = html.escape(p.get("_title") or deals_site.clean_title(p.get("product_title", "")))
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
    promo = deals_site.promo_text(p.get("promo_code_info"))
    extra = [x for x in [promo, deals_site.code_line(cfg, price)] + deals_site.active_notes(cfg) if x]
    if extra:
        lines += [sep] + extra
    if as_album(cfg, p):
        # الألبوم ما يقبل زر، فنحط رابط الطلب في النص
        link = html.escape(p["promotion_link"], quote=True)
        lines += [sep, "", f'👈 <a href="{link}"><b>اضغط هنا للطلب</b></a>']
    if is_clothing(cfg, p):
        lines += ["", "📏 <b>تأكدي من مقاسك وفق الجدول الخاص بالبائع</b>"]
    text = "\n".join(lines)
    code = deals_site.code_line(cfg, price)
    if len(text) > 1020 and code:
        # الكابشن طويل: نخلي سطر الكود بدون تفاصيل الدول عشان ما ينقص الرابط
        text = text.replace(code, code.split("\n")[0])
    return text[:1020]


# ---------------------------------------------------------------- تليجرام
def tg(cfg, method, **payload):
    url = f"https://api.telegram.org/bot{cfg['bot_token']}/{method}"
    r = requests.post(url, json=payload, timeout=30)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram error: {data}")
    return data["result"]


def as_album(cfg, p):
    """الملابس تنزل ألبوم صور (لو فيه صور إضافية)."""
    return is_clothing(cfg, p) and bool(gallery(p))


def send_product(cfg, p):
    imgs = gallery(p)
    if as_album(cfg, p):
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


MEN_RE = re.compile(r"\b(car|cars|auto|automobiles?|motorcycles?|vehicle|dash ?cam|tire|tyre|men|men's|mens|male|camping|power ?bank|earbuds?|"
                    r"smart ?watch|charger|phone holder|motorcycle|bike)\b")
MEN_AR = ["سيارة", "السيارة", "سيارات", "للسيارة", "رجال", "رجالي", "للرجال", "تخييم", "باور بانك", "سماعات", "سماعة",
          "ساعة ذكية", "شاحن", "حامل جوال", "حامل هاتف", "إطار السيارة", "دراجة", "بنك طاقة", "بنك الطاقة",
          "رحلات", "كشتة", "صيد السمك", "لحية", "حلاقة"]


def is_mens(p):
    title = str(p.get("product_title", "")).lower()
    cats = (str(p.get("first_level_category_name", "")) + " " + str(p.get("second_level_category_name", ""))).lower()
    return bool(MEN_RE.search(title) or MEN_RE.search(cats) or has_any(title, MEN_AR))


AI_PROMPT = (
    "أنت محرر متجر إلكتروني سعودي. هذا عنوان منتج من علي إكسبريس مترجم آليًا ومليان كلام زايد:\n"
    "«{raw}»\n\n"
    "اكتب اسم قصير وواضح للمنتج بالعربي الفصيح البسيط يفهمه أي شخص في السعودية:\n"
    "- من 3 إلى 8 كلمات فقط\n"
    "- اذكر وش المنتج بالضبط، وأهم ميزة وحدة (مثل الخامة أو العدد أو المقاس) لو كانت مهمة\n"
    "- بدون أكواد أو موديلات أو سنوات أو كلمات تسويقية مثل جديد وأفضل وساخن\n"
    "- بدون إيموجي وبدون علامات تنصيص\n"
    "اكتب الاسم فقط في سطر واحد."
)


_GEMINI_MODELS = None


def gemini_models(key):
    """نسأل قوقل عن الموديلات المتاحة حاليًا ونختار الأسرع (flash-lite ثم flash)، عشان ما يتعطل البوت لو قوقل وقف موديل."""
    global _GEMINI_MODELS
    if _GEMINI_MODELS is not None:
        return _GEMINI_MODELS
    names = []
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                         params={"key": key, "pageSize": 200}, timeout=20)
        for m in r.json().get("models", []):
            if "generateContent" in m.get("supportedGenerationMethods", []):
                names.append(m["name"].split("/")[-1])
    except Exception as e:
        print(f"[warn] Gemini models: {e}", file=sys.stderr)
    bad = ("image", "tts", "audio", "live", "embed", "vision", "thinking", "computer")
    flash = [n for n in names if "flash" in n and not any(b in n for b in bad)]
    lite = sorted([n for n in flash if "lite" in n], reverse=True)
    rest = sorted([n for n in flash if "lite" not in n], reverse=True)
    _GEMINI_MODELS = (lite + rest)[:4] or ["gemini-flash-lite-latest", "gemini-flash-latest"]
    print(f"Gemini models: {', '.join(_GEMINI_MODELS)}")
    return _GEMINI_MODELS


def ai_title(cfg, raw):
    """يعيد كتابة عنوان المنتج باسم قصير مفهوم عن طريق Gemini (مجاني). يرجع None لو ما فيه مفتاح أو فشل."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key or not raw:
        return None
    body = {"contents": [{"parts": [{"text": AI_PROMPT.format(raw=str(raw)[:300])}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 400}}
    for model in cfg.get("ai_models") or gemini_models(key):
        for attempt in range(2):
            try:
                r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                  params={"key": key}, json=body, timeout=60)
                if r.status_code == 429 and attempt == 0:
                    time.sleep(8)  # الخطة المجانية لها حد طلبات بالدقيقة
                    continue
                if r.status_code != 200:
                    print(f"[warn] Gemini {model}: {r.status_code} {r.text[:300]}", file=sys.stderr)
                    if r.status_code == 404 and _GEMINI_MODELS and model in _GEMINI_MODELS:
                        _GEMINI_MODELS.remove(model)  # موديل متوقف: ما نجربه مرة ثانية
                    break
                parts = r.json()["candidates"][0]["content"]["parts"]
                txt = " ".join(x.get("text", "") for x in parts).strip().splitlines()[0]
                txt = txt.strip(" \"'«»*-.").strip()
                if 4 <= len(txt) <= 80:
                    return txt
                break
            except Exception as e:
                print(f"[warn] Gemini {model}: {e}", file=sys.stderr)
                break
    return None


SEO_PROMPT = (
    "أنت محرر SEO لمتجر عروض سعودي. هذا عنوان منتج من علي إكسبريس مترجم آليًا:\n«{raw}»\n"
    "فئته: {cat}\n\n"
    "اكتب 3 أسطر بالضبط بهذا الشكل وبدون أي شي ثاني:\n"
    "الاسم: اسم قصير وواضح بالعربي من 3 إلى 8 كلمات (وش المنتج + أهم ميزة)\n"
    "العنوان: عنوان لصفحة قوقل لا يزيد عن 55 حرف، يبدأ بالكلمة اللي يبحث فيها الناس عن هالمنتج بالعربي\n"
    "الوصف: فقرة من جملتين أو ثلاث بالعربي البسيط: وش المنتج، ولمين مناسب، ووش أهم مميزاته\n\n"
    "القواعد: اعتمد بس على المعلومات الموجودة في العنوان ولا تخترع مواصفات أو أرقام. "
    "بدون أكواد موديلات أو سنوات، وبدون كلمات مبالغة مثل الأفضل والأقوى، وبدون إيموجي أو علامات تنصيص."
)


def ai_seo(cfg, raw, cat=""):
    """اسم + عنوان صفحة + وصف للمنتج بطلب واحد لـ Gemini. يرجع dict أو None."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key or not raw:
        return None
    body = {"contents": [{"parts": [{"text": SEO_PROMPT.format(raw=str(raw)[:300], cat=cat)}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 800}}
    for model in cfg.get("ai_models") or gemini_models(key):
        for attempt in range(2):
            try:
                r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                  params={"key": key}, json=body, timeout=60)
                if r.status_code == 429 and attempt == 0:
                    time.sleep(15)
                    continue
                if r.status_code != 200:
                    print(f"[warn] Gemini {model}: {r.status_code} {r.text[:300]}", file=sys.stderr)
                    if r.status_code == 404 and _GEMINI_MODELS and model in _GEMINI_MODELS:
                        _GEMINI_MODELS.remove(model)
                    break
                parts = r.json()["candidates"][0]["content"]["parts"]
                out = parse_seo(" ".join(x.get("text", "") for x in parts))
                if out:
                    return out
                break
            except Exception as e:
                print(f"[warn] Gemini {model}: {e}", file=sys.stderr)
                break
    return None


def parse_seo(txt):
    out = {}
    for line in str(txt).splitlines():
        line = line.strip().lstrip("-*• ").replace("**", "")
        for k, field in (("الاسم", "name"), ("العنوان", "seo_title"), ("الوصف", "about")):
            if line.startswith(k):
                v = line[len(k):].lstrip(" :：-").strip(" \"'«»")
                if v:
                    out[field] = v
    if not (4 <= len(out.get("name", "")) <= 80):
        return None
    if not (8 <= len(out.get("seo_title", "")) <= 75):
        out["seo_title"] = out["name"]
    if len(out.get("about", "")) < 20:
        out.pop("about", None)
    return out


def product_name(cfg, p):
    """يجهز اسم المنتج ومعلومات صفحته. يرجع اسم الذكاء الاصطناعي أو None (وقتها ينعرض العنوان المنظف)."""
    seo = ai_seo(cfg, p.get("product_title"), p.get("_cat") or category(p))
    if seo:
        p["_seo"] = seo
        return seo["name"]
    return ai_title(cfg, p.get("product_title"))


def cmd_retitle(cfg):
    """يعطي المنتجات القديمة في الموقع أسماء جديدة بالذكاء الاصطناعي."""
    items = deals_site.load_products()
    done = 0
    for x in items:
        if x.get("seo_title"):
            continue
        seo = ai_seo(cfg, x["title"], deals_site.cat_by_slug(x["cat"])["name"])
        if seo:
            x.update(seo)
            done += 1
            print(f"+ {seo['name']} | {seo['seo_title']}")
        time.sleep(5)
    deals_site.PRODUCTS_FILE.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    deals_site.build(cfg)
    print(f"انعاد تسمية {done} منتج")
    return 0


def cmd_prune(cfg):
    """يشيل من الموقع أي منتج ما يطابق الشروط الحالية (رجالي، جنسي، مستحضرات)."""
    items = deals_site.load_products()
    keep, removed = [], []
    for x in items:
        p = {"product_title": x["title"]}
        if is_mens(p) or blocked(p) or no_brand(x.get("shop")):
            removed.append(x)
            (deals_site.OUT / "p" / f"{x['id']}.html").unlink(missing_ok=True)
        else:
            keep.append(x)
    deals_site.PRODUCTS_FILE.write_text(json.dumps(keep, ensure_ascii=False), encoding="utf-8")
    for x in removed:
        print(f"- شلت: {x['title'][:60]}")
    deals_site.build(cfg)
    print(f"شلت {len(removed)} منتج، وباقي {len(keep)}")
    return 0


def cmd_backfill(cfg, state):
    """يضيف للموقع المنتجات اللي انتشرت قبل ما يصير فيه موقع (ما عدا منتجات الرجال والسيارات)."""
    have = {x["id"] for x in deals_site.load_products()}
    ids = [pid for pid in state["posted"] if pid not in have]
    print(f"منتجات قديمة مو في الموقع: {len(ids)}")
    fields = ("product_id,product_title,target_sale_price,target_original_price,discount,evaluate_rate,"
              "lastest_volume,product_main_image_url,promotion_link,product_detail_url,product_small_image_urls,"
              "first_level_category_name,second_level_category_name,shop_id,shop_name")
    added, skipped = [], []
    for i in range(0, len(ids), 10):
        chunk = ids[i:i + 10]
        try:
            prods = extract_products(ali_call(cfg, "aliexpress.affiliate.productdetail.get", {
                "product_ids": ",".join(chunk), "target_currency": cfg["currency"], "target_language": cfg["language"],
                "ship_to_country": cfg["ship_to"], "tracking_id": cfg["tracking_id"], "fields": fields}))
        except Exception as e:
            print(f"[warn] {e}", file=sys.stderr)
            continue
        for p in prods:
            title = str(p.get("product_title", ""))[:50]
            if is_mens(p) or blocked(p) or no_brand(p.get("shop_name")):
                skipped.append(title)
                continue
            p["promotion_link"] = short_link(cfg, p)
            p["_ships"] = ships_to(cfg, p)
            p["_cat"] = deals_site.infer_cat(p.get("product_title", ""))
            p["_title"] = product_name(cfg, p)
            deals_site.record(cfg, p, gallery(p))
            added.append(str(p["product_id"]))
            print(f"+ {p['_cat']}: {title}")
    for s in skipped:
        print(f"- تخطيت (رجالي/ممنوع): {s}")
    deals_site.build(cfg)  # نعيد بناء الموقع كامل عشان المنتجات المشابهة تشمل الجديدة
    print(f"انضاف {len(added)} منتج، وتخطيت {len(skipped)}")
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
    # توزيع المنشورات بالتساوي على باقي اليوم بدل ما تخلص بدري
    start, end = cfg["post_hours"]
    now = datetime.now(RIYADH)
    h = now.hour + now.minute / 60
    if h < start and end > 24:
        h += 24
    runs_left = max(1, int((end - h) * 60 // cfg.get("run_every_minutes", 30)) + 1)
    count = min(count, max(1, -(-left // runs_left)))
    prods = pick(cfg, state, fetch_candidates(cfg), count)
    sent, new_ids = 0, []
    for p in prods:
        p["promotion_link"] = short_link(cfg, p)
        p["_ships"] = ships_to(cfg, p)
        p["_title"] = product_name(cfg, p)
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
        if cfg.get("site_url"):
            deals_site.record(cfg, p, gallery(p))
            new_ids.append(str(p["product_id"]))
        if sent < len(prods):
            time.sleep(8)
    if new_ids:
        try:
            ensure_home_link(cfg)
            deals_site.build(cfg, only_ids=new_ids)
            print(f"الموقع: انضافت {len(new_ids)} صفحة")
        except Exception as e:
            print(f"[warn] بناء الموقع فشل: {e}", file=sys.stderr)
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
    ap.add_argument("command", choices=["check", "preview", "post", "run", "fields", "linktest", "site", "backfill", "prune", "retitle"])
    ap.add_argument("--count", type=int, default=1)
    a = ap.parse_args()
    cfg = load_config()
    if a.command == "check":
        return cmd_check(cfg)
    if a.command == "fields":
        return cmd_fields(cfg)
    if a.command == "retitle":
        return cmd_retitle(cfg)
    if a.command == "prune":
        return cmd_prune(cfg)
    if a.command == "backfill":
        return cmd_backfill(cfg, load_state())
    if a.command == "site":
        print(f"انبنت {deals_site.build(cfg)} صفحة منتج")
        return 0
    if a.command == "linktest":
        return cmd_linktest(cfg)
    if a.command == "preview":
        return cmd_preview(cfg, load_state())
    if a.command == "post":
        return cmd_post(cfg, load_state(), a.count)
    return cmd_run(cfg)


if __name__ == "__main__":
    sys.exit(main())
