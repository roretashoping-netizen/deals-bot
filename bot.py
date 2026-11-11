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
import base64
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


def post_day():
    """يوم النشر: من ٥ الفجر لين ٥ الفجر اللي بعده (منشورات ١٢–١ بالليل تنحسب على اليوم اللي قبل)."""
    return (datetime.now(RIYADH) - timedelta(hours=5)).strftime("%Y-%m-%d")


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
    # كلمات الفئات المهمة (شنط وفساتين) لها وزن أعلى فتنختار أكثر
    weights = cfg.get("category_weights", {})
    keywords = sorted(cfg["keywords"], reverse=True,
                      key=lambda k: random.random() ** (1.0 / max(0.1, weights.get(deals_site.category_for(k)["slug"], 1))))
    out = []
    deep = set(cfg.get("deep_search_cats", []))
    # الأقسام صعبة الشروط (سهرة/شنط) إضافة فوق البحث العادي، مو بداله:
    # كلمة وحدة منها كل تشغيل + كلمات عادية بالعدد الكامل، عشان النشر ما يوقف لو ما لقينا سهرة/شنطة مطابقة
    premium = [k for k in keywords if deals_site.category_for(k)["slug"] in deep]
    general = [k for k in keywords if deals_site.category_for(k)["slug"] not in deep]
    jobs = [(kw, pg) for kw in premium[: cfg.get("premium_keywords_per_run", 1)] for pg in (1, 2)]
    jobs += [(kw, random.randint(1, 3)) for kw in general[: cfg.get("keywords_per_run", 4)]]
    for kw, page_no in jobs:
        biz = {
            "keywords": kw,
            "tracking_id": cfg["tracking_id"],
            "target_currency": cfg["currency"],
            "target_language": cfg["language"],
            "ship_to_country": cfg["ship_to"],
            "sort": "LAST_VOLUME_DESC",
            "page_size": 50,
            "page_no": page_no,
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


STORE_FILE = deals_site.BASE_DIR / "data" / "store_products.json"
DETAIL_FIELDS = ("product_id,product_title,target_sale_price,target_original_price,discount,evaluate_rate,"
                 "lastest_volume,product_main_image_url,promotion_link,product_detail_url,product_small_image_urls,"
                 "first_level_category_name,second_level_category_name,shop_id,shop_name")


def fetch_store_candidates(cfg, state, k=10):
    """فساتين من المتاجر المختارة مباشرة (أرقام منتجاتها محفوظة في data/store_products.json)."""
    try:
        stores = json.loads(STORE_FILE.read_text(encoding="utf-8")).get("stores", {})
    except Exception:
        return []
    posted = set(state.get("posted", {}))
    ids = [pid for lst in stores.values() for pid in lst if str(pid) not in posted]
    random.shuffle(ids)
    out = []
    for i in range(0, min(len(ids), k), 10):
        try:
            prods = extract_products(ali_call(cfg, "aliexpress.affiliate.productdetail.get", {
                "product_ids": ",".join(map(str, ids[i:i + 10])), "target_currency": cfg["currency"],
                "target_language": cfg["language"], "ship_to_country": cfg["ship_to"],
                "tracking_id": cfg["tracking_id"], "fields": DETAIL_FIELDS}))
        except Exception as e:
            print(f"[warn] منتجات المتاجر: {e}", file=sys.stderr)
            continue
        for p in prods:
            p["_kw"] = cfg.get("store_keyword", "Sharon Said evening dress")
            p["_from_store"] = True
        out.extend(prods)
    print(f"من المتاجر المختارة: {len(out)} منتج")
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


def site_cat(p):
    """قسم المنتج عندنا (مطبخ، شعر، أظافر...) عشان ننوّع بين الأقسام."""
    if p.get("_kw"):
        return deals_site.category_for(p["_kw"])["slug"]
    return deals_site.infer_cat(p.get("product_title", ""))


def _grams(text):
    t = re.sub(r"[^a-z\u0600-\u06ff]+", " ", str(text).lower())
    t = re.sub(r"[\u064b-\u0652]", "", t).replace("ة", "ه").replace("أ", "ا").replace("إ", "ا")
    t = " ".join((w[2:] if w.startswith("ال") and len(w) > 4 else w) for w in t.split()[:8])
    return {t[i:i + 3] for i in range(len(t) - 2)}


def similar(a, b, limit=0.5):
    """نفس المنتج تقريبًا بصياغة ثانية (مثل: خفاقة بيض يدوية / خلاط بيض يدوي)."""
    A, B = _grams(a), _grams(b)
    return bool(A and B) and len(A & B) / min(len(A), len(B)) >= limit


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


def norm_shop(name):
    return re.sub(r"\s+", " ", str(name or "").strip().lower())


def trusted_shop(cfg, p):
    """متاجر فساتين السهرة اللي اختارها صاحب القروب."""
    if str(p.get("shop_id", "")) in cfg.get("store_ids", {}):
        return True
    allowed = {norm_shop(x) for x in cfg.get("evening_dress_shops", [])}
    return norm_shop(p.get("shop_name") or p.get("shop")) in allowed


EVENING_WORDS = ("evening", "party", "prom", "formal", "cocktail", "wedding guest", "bridesmaid", "ball gown",
                 "mermaid", "sequin", "سهرة", "سهره", "حفلة", "حفلات", "زفاف")


def is_evening(p):
    """فستان سهرة/حفلات؟"""
    t = " ".join(str(p.get(k, "")) for k in
                 ("product_title", "title", "first_level_category_name", "second_level_category_name")).lower()
    dress = any(w in t for w in ("dress", "gown", "فستان", "فساتين"))
    return ("gown" in t) or (dress and any(w in t for w in EVENING_WORDS))


def bag_rule_fail(cfg, p):
    """الشنط: جلد + متجر رسمي موثّق + تقييم عالي. يرجع سبب الرفض أو None."""
    rules = cfg.get("bag_rules")
    if not rules or not p.get("_kw") or deals_site.category_for(p["_kw"])["slug"] != "bags":
        return None
    title = str(p.get("product_title", "")).lower()
    if rules.get("require_words") and not any(w in title for w in rules["require_words"]):
        return "شنطة مو جلد"
    if any(w in title for w in rules.get("exclude_words", [])):
        return "شنطة جلد صناعي"
    shop = norm_shop(p.get("shop_name"))
    if rules.get("shop_words") and not any(w in shop for w in rules["shop_words"]):
        # متجر غير رسمي: نقبله بس لو جودته مثبتة بتقييم عالي جدًا وطلبات كثيرة
        alt = rules.get("unofficial_ok", {})
        if not alt or num(p.get("evaluate_rate")) < alt.get("min_rating_percent", 101) \
                or num(p.get("lastest_volume")) < alt.get("min_orders", 10 ** 9):
            return "شنطة من متجر غير رسمي"
    if num(p.get("evaluate_rate")) < rules.get("min_rating_percent", 0):
        return "شنطة تقييمها أقل"
    if num(p.get("lastest_volume")) < rules.get("min_orders", 0):
        return "شنطة طلباتها قليلة"
    return None


def has_any(text, words):
    return any(w in text for w in words)


# أسماء البراندات العالمية: أي منتج يذكرها غالبًا تقليد، وعلي إكسبريس يمنع الترويج للمقلّد
BRAND_RE = re.compile(
    r"\b(louis ?vuitton|lv|gucci|chanel|dior|herm[eè]s|prada|ysl|saint ?laurent|fendi|balenciaga|celine|givenchy|"
    r"bottega|burberry|michael ?kors|versace|valentino|miu ?miu|goyard|loewe|cartier|rolex|tiffany|bvlgari|bulgari|"
    r"guess|furla|longchamp|marc ?jacobs|kate ?spade|tory ?burch|jacquemus|coach bag|replica|dupe|high copy|mirror quality)\b"
    r"|لويس ?فيتون|غوتشي|قوتشي|شانيل|ديور|هيرمس|برادا|فندي|بالنسياغا|فيرساتشي|كارتير|رولكس|ماي ?كل ?كورس")


def blocked(p):
    """يرجع سبب المنع لو المنتج ممنوع، وإلا None."""
    text = " ".join(str(p.get(k, "")) for k in
                    ("product_title", "first_level_category_name", "second_level_category_name")).lower()
    if has_any(text, ADULT_WORDS):
        return "adult"
    if BRAND_RE.search(str(p.get("product_title", "")).lower()):
        return "brand"
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
    recent_list = state.get("recent_keys", [])[-400:]
    dull = [w.lower() for w in cfg.get("dull_words", [])]
    day = post_day()
    cat_day = state.get("cat_day", {}).get(day, {})
    cat_cap = cfg.get("max_per_cat_day", 6)
    why = {}

    def skip(reason):
        why[reason] = why.get(reason, 0) + 1
    for p in products:
        pid = str(p.get("product_id"))
        if pid in seen or not p.get("promotion_link") or not p.get("product_main_image_url"):
            skip("منشور قبل"); continue
        if blocked(p) or no_brand(p.get("shop_name")):
            skip("ممنوع/بدون اسم متجر"); continue
        if p.get("_kw") and not deals_site.fits_cat(deals_site.category_for(p["_kw"])["slug"], p.get("product_title", "")):
            skip("من غير القسم"); continue  # البحث رجّع منتج من غير القسم (ساعة لما ندور فستان مثلًا)
        trusted = trusted_shop(cfg, p)
        if is_evening(p) and not trusted:
            skip("سهرة من متجر ثاني")
            st = why.setdefault("_shops", {}).setdefault(str(p.get("shop_name")), [0, 0, 0])
            st[0] += 1
            st[1] = max(st[1], int(num(p.get("lastest_volume"))))
            st[2] = max(st[2], num(p.get("evaluate_rate")))
            continue
        bag_fail = bag_rule_fail(cfg, p)
        if bag_fail:
            skip(bag_fail); continue  # فساتين السهرة بس من المتاجر المختارة
        f = {**cfg["filters"], **cfg.get("trusted_shop_filters", {})} if trusted else cfg["filters"]
        if site_cat(p) == "evening-bags":  # حقائب السهرة: شروط فخامة خاصة (تقييم عالي، سعر يليق، صور كثيرة)
            f = {**cfg["filters"], **cfg.get("evening_bag_rules", {})}
            if len(gallery(p)) < f.get("min_images", 0):
                skip("حقيبة سهرة صورها قليلة"); continue
        if trusted and len(gallery(p)) < f.get("min_images", 0):
            skip("صور قليلة"); continue  # فساتين السهرة: لازم صور كافية تبين التفاصيل
        # نفس المنتج من بائع ثاني (نفس العنوان أو نفس الصورة) ما ينعاد
        if title_key(p) in recent_keys or p["product_main_image_url"] in recent_imgs:
            skip("مكرر"); continue
        if num(p.get("evaluate_rate")) < f["min_rating_percent"]:
            skip("تقييم"); continue
        if num(p.get("lastest_volume")) < f["min_orders"]:
            skip("طلبات قليلة"); continue
        if num(p.get("discount")) < f["min_discount_percent"]:
            skip("خصم قليل"); continue
        if num(p.get("target_sale_price")) < f.get("min_price", 0):
            skip("رخيص جدًا"); continue  # القطع الرخيصة جدًا غالبًا مو جذابة
        if any(w in str(p.get("product_title", "")).lower() for w in dull):
            skip("منتج ممل"); continue
        if cat_day.get(site_cat(p), 0) >= cat_cap and not cfg.get("_focus"):
            skip("القسم كمّل حده اليوم"); continue
        tk = title_key(p)
        # عناوين فساتين المتاجر المختارة متشابهة بطبيعتها (Luxury Beaded Evening...)، فنكتفي بمنع الصورة/العنوان المطابق
        if not trusted and (any(similar(tk, k) for k in recent_list) or any(similar(tk, title_key(q)) for _, q in good)):
            skip("مشابه لمنتج انتشر"); continue
        seen.add(pid)
        recent_keys.add(title_key(p))
        recent_imgs.add(p["product_main_image_url"])
        score = num(p.get("lastest_volume")) ** 0.5 + num(p.get("discount")) + num(p.get("commission_rate")) * 2
        if p.get("_from_store"):
            score += 40  # المتاجر المختارة لها أولوية
        good.append((score, p))
    shops = why.pop("_shops", {})
    print(f"المرشحين {len(products)}، المقبول {len(good)}، المرفوض: " + ", ".join(f"{k} {v}" for k, v in why.items()))
    if shops:
        print("متاجر فساتين سهرة انرفضت (عدد/أعلى طلبات/أعلى تقييم): " + " | ".join(
            f"{k} ({v[0]}/{v[1]}/{v[2]:.0f}%)" for k, v in sorted(shops.items(), key=lambda t: -t[1][1])[:40]))
    good.sort(key=lambda t: t[0], reverse=True)
    top = [p for _, p in good[: n * 6]]
    random.shuffle(top)  # تنويع بين أفضل المنتجات
    # تنويع: كل منتج بالتشغيل من قسم مختلف، ولا نعيد قسم آخر منشورين، ولا متجر مرتين
    recent_cats = state.get("recent_cats", [])[-2:]
    chosen, cats, shops, rejected = [], set(), set(), set()
    budget = [max(cfg.get("taste_checks_per_run", 12), n * 3 if cfg.get("_focus") else 0)]
    for strict in (True, False):
        for p in top:
            if len(chosen) >= n:
                break
            c = site_cat(p)
            if p in chosen or id(p) in rejected or (c in cats and not cfg.get("_focus")) or (strict and c in recent_cats and not cfg.get("_focus")):
                continue
            if p.get("shop_id") and p.get("shop_id") in shops:
                continue
            if not taste_ok(cfg, state, p, budget):
                rejected.add(id(p))
                continue
            chosen.append(p)
            cats.add(c)
            shops.add(p.get("shop_id"))
    # ترتيب النشر: ما يجي قسمين ورا بعض مثل آخر منشور
    if chosen and recent_cats and site_cat(chosen[0]) == recent_cats[-1] and len(chosen) > 1:
        chosen.append(chosen.pop(0))
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
    pitch = p.get("_pitch") or {}
    # ١) جملة جذب عاطفية  ٢) اسم المنتج  ٣) صورة ذهنية (وين بتلبسه/تستخدمه)
    lines = [f"<b>{html.escape(pitch['hook'])}</b>" if pitch.get("hook") else random.choice(cfg["headlines"]),
             "", f"<b>{title}</b>"]
    if pitch.get("pitch"):
        lines.append(f"<i>{html.escape(pitch['pitch'])}</i>")
    lines += ["", sep]
    # ٤) تثبيت السعر: القديم مشطوب ثم الجديد ثم كم توفرين (الخسارة لو ما طلبتي)
    if old > price > 0:
        # كل رقم بسطر لحاله عشان الأرقام والأسهم ما تتلخبط مع اتجاه العربي
        lines.append(f"🏷️ قبل: <s>{money(old)}</s> {cur}")
        lines.append(f"💰 الحين: <b>{money(price)} {cur}</b>")
        lines.append(f"🎁 توفّرين: <b>{money(old - price)} {cur}</b>" + (f" (خصم {disc}%)" if disc else ""))
    elif price:
        lines.append(f"💰 السعر: <b>{money(price)}</b> {cur}")
    # ٥) دليل اجتماعي: كثير غيرك جربوه وعجبهم
    rating = stars(p.get("evaluate_rate"))
    vol = int(num(p.get("lastest_volume")))
    if rating or vol:
        if vol >= 100:
            lines.append(f"👭 انطلب أكثر من {vol:,} مرة" + (f" · ⭐ {rating} من 5" if rating else ""))
        elif rating:
            lines.append(f"⭐ تقييم {rating} من 5" + (f" · {vol} طلب" if vol else ""))
    ships = p.get("_ships")
    if ships:
        names = {c["code"]: f'{c["flag"]} {c["name"]}' for c in cfg.get("countries", [])}
        items = [names[c] for c in ships if c in names]
        lines += [sep, "🚚 الشحن متوفر لـ:"]
        lines += ["   ".join(items[i:i + 3]) for i in range(0, len(items), 3)]
    promo = deals_site.promo_text(p.get("promo_code_info"))
    codes = [x for x in [promo, deals_site.code_line(cfg, price)] if x]
    if codes:
        lines += [sep] + codes
    for note in deals_site.active_notes(cfg):  # أي إضافة تحت الأكواد تنفصل بخط
        lines += [sep, note]
    if as_album(cfg, p):
        # الألبوم ما يقبل زر، فنحط رابط الطلب في النص
        link = html.escape(p["promotion_link"], quote=True)
        lines += [sep, "", f'👈 <a href="{link}"><b>اضغط هنا للطلب</b></a>']
    if is_clothing(cfg, p):
        lines += [sep, "📏 <b>تأكدي من مقاسك وفق الجدول الخاص بالبائع</b>"]
    if not as_album(cfg, p):
        lines += ["", "👇 <b>اطلبيه من الزر تحت قبل ينتهي الخصم</b>"]
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
    return not p.get("_single") and is_clothing(cfg, p) and bool(gallery(p))


def post_buttons(cfg, p):
    """زر الطلب، وتحته أكواد الخصم ومشاركة العرض (المشاركة تجيب أعضاء وزوار جدد)."""
    rows = [[{"text": cfg.get("button_text", "🛍️ اطلب الحين"), "url": p["promotion_link"]}]]
    site = cfg.get("site_url", "").rstrip("/")
    if site:
        from urllib.parse import quote
        page = f"{site}/p/{p['product_id']}.html"
        text = f"{p.get('_title') or 'عرض من علي إكسبريس'} 🔥"
        rows.append([
            {"text": "🎟️ أكواد الخصم", "url": f"{site}/codes.html"},
            {"text": "📤 شاركي العرض", "url": f"https://t.me/share/url?url={quote(page)}&text={quote(text)}"},
        ])
    return rows


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
        reply_markup={"inline_keyboard": post_buttons(cfg, p)},
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


TASTE_PROMPT = (
    "You are a senior luxury fashion stylist choosing products for elegant Saudi and Gulf women. "
    "Look at this product photo and judge only how classy and high-taste it looks. "
    "Reward refined colors (black, ivory, white, champagne, nude, blush, dusty rose, gold, silver, navy, emerald, "
    "burgundy, deep jewel tones, tasteful tone-on-tone) and rich-looking materials (satin, silk, crystal, pearls, "
    "fine beading, quality leather). Penalize garish or neon colors, clashing color mixes, cheap-looking shiny plastic, "
    "tacky prints, childish designs, messy styling and low-quality photos. "
    'Reply with JSON only, like {"score": 8, "reason": "short reason"}. score is 1-10 (10 = very luxurious and tasteful).'
)


def image_part(p):
    """صورة المنتج الرئيسية بصيغة Gemini (inline_data). يرجع None لو ما قدرنا ننزلها."""
    if "_img_part" in p:
        return p["_img_part"]
    part = None
    url = p.get("product_main_image_url")
    try:
        if url:
            img = requests.get(url, timeout=25)
            if img.status_code == 200 and len(img.content) <= 4_000_000:
                mime = img.headers.get("content-type", "image/jpeg").split(";")[0]
                if not mime.startswith("image/"):
                    mime = "image/jpeg"
                part = {"inline_data": {"mime_type": mime, "data": base64.b64encode(img.content).decode()}}
    except Exception as e:
        print(f"[warn] صورة {p.get('product_id')}: {e}", file=sys.stderr)
    p["_img_part"] = part
    return part


def taste_score(cfg, p):
    """درجة الذوق والفخامة من صورة المنتج (Gemini يشوف الصورة). يرجع رقم 1-10 أو None لو فشل."""
    key = os.environ.get("GEMINI_API_KEY", "")
    url = p.get("product_main_image_url")
    if not key or not url:
        return None
    try:
        img = requests.get(url, timeout=25)
        if img.status_code != 200 or len(img.content) > 4_000_000:
            return None
        mime = img.headers.get("content-type", "image/jpeg").split(";")[0]
        if not mime.startswith("image/"):
            mime = "image/jpeg"
        data = base64.b64encode(img.content).decode()
    except Exception as e:
        print(f"[warn] صورة {p.get('product_id')}: {e}", file=sys.stderr)
        return None
    body = {"contents": [{"parts": [{"text": TASTE_PROMPT}, {"inline_data": {"mime_type": mime, "data": data}}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 200}}
    for model in cfg.get("ai_models") or gemini_models(key):
        for attempt in range(2):
            try:
                r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                  params={"key": key}, json=body, timeout=60)
                if r.status_code == 429 and attempt == 0:
                    time.sleep(8)
                    continue
                if r.status_code != 200:
                    print(f"[warn] Gemini taste {model}: {r.status_code} {r.text[:200]}", file=sys.stderr)
                    break
                txt = " ".join(x.get("text", "") for x in r.json()["candidates"][0]["content"]["parts"])
                m = re.search(r'"score"\s*:\s*(\d+(?:\.\d+)?)', txt)
                if m:
                    why = re.search(r'"reason"\s*:\s*"([^"]*)"', txt)
                    p["_taste_why"] = why.group(1)[:80] if why else ""
                    return float(m.group(1))
                break
            except Exception as e:
                print(f"[warn] Gemini taste {model}: {e}", file=sys.stderr)
                break
    return None


UGLY_WORDS = ("neon", "fluorescent", "rainbow", "multicolor", "multi-color", "multicolour", "tie dye", "tie-dye",
              "graffiti", "cartoon", "فسفوري", "نيون", "قوس قزح", "متعدد الألوان", "كرتون")


def taste_ok(cfg, state, p, budget):
    """الفساتين والشنط: لازم ذوقها عالي وألوانها راقية. budget = كم صورة باقي نقدر نفحص بهالتشغيل."""
    if site_cat(p) not in cfg.get("taste_cats", []):
        return True
    title = str(p.get("product_title", "")).lower()
    if any(w in title for w in UGLY_WORDS):
        print(f"- ذوق: انرفض (ألوان صارخة بالعنوان) {p.get('product_id')}")
        return False
    cache = state.setdefault("taste", {})
    pid = str(p.get("product_id"))
    if pid not in cache:
        if budget[0] <= 0:
            return False
        budget[0] -= 1
        s = taste_score(cfg, p)
        if s is None:
            return False  # ما قدرنا نتأكد من الذوق: ما ننشره (الجودة أهم من العدد)
        cache[pid] = s
        if len(cache) > 1500:
            for k in list(cache)[:500]:
                del cache[k]
        print(f"- ذوق {pid} ({site_cat(p)}): {s:g}/10 {p.get('_taste_why', '')}")
    return cache[pid] >= cfg.get("taste_min", 7)


SEO_PROMPT = (
    "أنت محرر SEO لمتجر عروض سعودي. هذا عنوان منتج من علي إكسبريس مترجم آليًا:\n«{raw}»\n"
    "فئته: {cat}\n\n"
    "اكتب 3 أسطر بالضبط بهذا الشكل وبدون أي شي ثاني:\n"
    "الاسم: اسم قصير وواضح بالعربي من 3 إلى 8 كلمات (وش المنتج + أهم ميزة)\n"
    "العنوان: عنوان لصفحة قوقل لا يزيد عن 55 حرف، يبدأ بالكلمة اللي يبحث فيها الناس عن هالمنتج بالعربي\n"
    "الوصف: فقرة من جملتين أو ثلاث بالعربي البسيط: وش المنتج، ولمين مناسب، ووش أهم مميزاته\n\n"
    "القواعد: اعتمد بس على المعلومات الموجودة في العنوان ولا تخترع مواصفات أو أرقام. "
    "بدون أكواد موديلات أو سنوات، وبدون كلمات مبالغة مثل الأفضل والأقوى، وبدون إيموجي أو علامات تنصيص. "
    "ترجم المصطلحات للعربي وبدون حروف إنجليزية (مثلًا A-line = قصة واسعة من تحت)، "
    "وكن دقيق بالخامة: الزركون والمويسانيت مو ألماس، والمطلي بالذهب مو ذهب."
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


PITCH_PROMPT = (
    "أنتِ كاتبة إعلانات محترفة لقروب عروض نسائي في الخليج، وتفهمين علم نفس الشراء.\n"
    "المنتج: «{name}»\nالعنوان الأصلي: {raw}\nالقسم: {cat}\n"
    "السعر الحين {price} ريال بدل {old} ريال (خصم {disc}%). التقييم {rating} من 5، وانطلب أكثر من {orders} مرة.\n\n"
    "معك صورة المنتج الرئيسية اللي بتنزل بالمنشور. اكتبي 3 أسطر بالضبط بهذا الشكل وبدون أي شي ثاني:\n"
    "الاسم: اسم المنتج بالعربي من 3 إلى 8 كلمات، يوصف اللي يبان بالصورة بالضبط (النوع واللون والتفاصيل الظاهرة)\n"
    "الجذب: جملة افتتاحية قصيرة (من 4 إلى 9 كلمات) تبدأ بإيموجي واحد مناسب، تشد العين وتلعب على الإحساس: "
    "الفضول أو الرغبة أو حماس المناسبة أو إحساس الصفقة الحلوة. باللهجة الخليجية وموجهة للبنت.\n"
    "الوصف: جملة أو جملتين (إلى 28 كلمة) ترسم الصورة في خيالها: وين بتلبسه أو تستخدمه، وش الإحساس اللي يعطيها، "
    "وليش يستاهل. لهجة خليجية راقية، مو مبتذلة.\n\n"
    "قواعد مهمة:\n"
    "- لا تخترعين أي معلومة مو موجودة (مواصفات، خامات، ضمان، كمية محدودة، مدة العرض).\n"
    "- الصورة هي المرجع: لا تذكرين أي ميزة ما تبان في الصورة (مثل سلسلة أو حزام أو لون أو قطعة إضافية) حتى لو مذكورة بالعنوان.\n"
    "- لا تذكرين ملابس أو ثقافات غريبة على الخليج.\n"
    "- كوني دقيقة بالخامة: الزركون والمويسانيت مو ألماس، والمطلي بالذهب مو ذهب، والجلد الصناعي مو جلد طبيعي.\n"
    "- الكلام موجه لها هي ولاستخدامها هي (مو هدية لرجل).\n"
    "- اختاري الفعل الصح: الفستان تلبسينه، الشنطة تشيلينها، الأداة تستخدمينها.\n"
    "- عربي فقط بإملاء صحيح ومسافات صحيحة، بدون أي كلمة من لغة ثانية.\n"
    "- نوّعي الأسلوب وتجنبي العبارات المستهلكة مثل «تسرقين الأنظار» و«تخطفين الأنظار».\n"
    "- لا تذكرين السعر أو الخصم أو التقييم (موجودة بالمنشور). بدون علامات تنصيص وبدون هاشتاقات."
)

FOREIGN_RE = re.compile(r"[\u0590-\u05FF\u0400-\u04FF\u0E00-\u0E7F\u3040-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF]")


def pitch_bad(text):
    """نص فيه حروف لغة ثانية (عبري، روسي، صيني...) أو موجّه لرجل."""
    t = str(text)
    return bool(FOREIGN_RE.search(t)) or has_any(t, MEN_AR + ["غاليك", "لزوجك", "لرجلك"])


def ai_pitch(cfg, p):
    """جملة جذب + وصف عاطفي للمنشور (Gemini). يرجع {"hook","pitch"} أو None."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return None
    price, old = num(p.get("target_sale_price")), num(p.get("target_original_price"))
    prompt = PITCH_PROMPT.format(
        name=p.get("_title") or "", raw=str(p.get("product_title", ""))[:250],
        cat=deals_site.cat_by_slug(site_cat(p))["name"], price=money(price), old=money(old) if old else money(price),
        disc=int(num(p.get("discount"))), rating=stars(p.get("evaluate_rate")) or "-",
        orders=int(num(p.get("lastest_volume"))))
    parts = [{"text": prompt}]
    img = image_part(p)
    if img:
        parts.append(img)  # Gemini يشوف الصورة عشان الكلام يطابقها
    body = {"contents": [{"parts": parts}],
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1500}}
    for model in cfg.get("ai_models") or gemini_models(key):
        for attempt in range(2):
            try:
                r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                  params={"key": key}, json=body, timeout=60)
                if r.status_code == 429 and attempt == 0:
                    time.sleep(8)
                    continue
                if r.status_code != 200:
                    print(f"[warn] Gemini pitch {model}: {r.status_code} {r.text[:200]}", file=sys.stderr)
                    break
                txt = " ".join(x.get("text", "") for x in r.json()["candidates"][0]["content"]["parts"])
                out = {}
                clean = txt.replace("**", "")
                for label, k in (("الاسم", "name"), ("الجذب", "hook"), ("الوصف", "pitch")):
                    m = re.search(label + r"\s*[:：\-]\s*(.+)", clean)
                    if m:
                        out[k] = m.group(1).strip().strip(" \"'«»*")
                if not out.get("hook"):  # رد بدون عناوين: اسم ثم جذب ثم وصف
                    rows = [x.strip().lstrip("-*• ").strip(" \"'«»") for x in clean.splitlines() if x.strip()]
                    if len(rows) >= 3:
                        out.update(name=rows[0], hook=rows[1], pitch=rows[2])
                    elif rows:
                        out["hook"] = rows[0]
                        if len(rows) > 1:
                            out["pitch"] = rows[1]
                if out.get("name") and not (4 <= len(out["name"]) <= 80) or pitch_bad(out.get("name", "")):
                    out.pop("name", None)
                if len(out.get("pitch", "")) > 260:
                    out.pop("pitch")  # وصف طويل: نكتفي بجملة الجذب
                if out.get("hook") and pitch_bad(out["hook"] + " " + out.get("pitch", "")):
                    print(f"[warn] Gemini pitch {model}: نص فيه خطأ لغوي/موجه لرجل، نجرب ثاني: {out}", file=sys.stderr)
                    break
                if out.get("hook") and 4 <= len(out["hook"]) <= 90:
                    return out
                print(f"[warn] Gemini pitch {model}: رد غير متوقع: {txt[:200]!r}", file=sys.stderr)
                break
            except Exception as e:
                print(f"[warn] Gemini pitch {model}: {e}", file=sys.stderr)
                break
    return None


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


def not_leather_bag(cfg, cat, title):
    """منتج في قسم الشنط وعنوانه ما يثبت إنه جلد طبيعي (أو يذكر جلد صناعي/قماش)."""
    rules, t = cfg.get("bag_rules") or {}, str(title).lower()
    return cat == "bags" and bool(rules) and (
        not any(w in t for w in rules.get("require_words", [])) or
        any(w in t for w in rules.get("exclude_words", [])))


def cmd_prune(cfg):
    """يشيل من الموقع أي منتج ما يطابق الشروط الحالية (رجالي، جنسي، مستحضرات)."""
    items = deals_site.load_products()
    keep, removed = [], []
    for x in items:
        p = {"product_title": x["title"], "shop_name": x.get("shop")}
        if is_mens(p) or blocked(p) or no_brand(x.get("shop")) or (is_evening(p) and not trusted_shop(cfg, p)):
            removed.append(x)
            (deals_site.OUT / "p" / f"{x['id']}.html").unlink(missing_ok=True)
        else:
            if x.get("cat") == "more" or not deals_site.fits_cat(x.get("cat", ""), x["title"]):
                x["cat"] = deals_site.infer_cat(x["title"])  # قسم غلط: نصححه من العنوان
            if not_leather_bag(cfg, x.get("cat"), x["title"]):
                removed.append(x)  # شنطة مو جلد طبيعي: ما تناسب شرط الجودة
                (deals_site.OUT / "p" / f"{x['id']}.html").unlink(missing_ok=True)
                continue
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
            if is_mens(p) or blocked(p) or no_brand(p.get("shop_name")) or (is_evening(p) and not trusted_shop(cfg, p)):
                skipped.append(title)
                continue
            p["promotion_link"] = short_link(cfg, p)
            p["_ships"] = ships_to(cfg, p)
            p["_cat"] = deals_site.infer_cat(p.get("product_title", ""))
            if not_leather_bag(cfg, p["_cat"], p.get("product_title", "")):
                skipped.append(title)
                continue
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


PEAK_WEIGHTS = [[9, 16, 1], [16, 20, 1.5], [20, 25, 2.5]]  # المساء له حصة أكبر (وقت التصفح والشراء)


def day_share(cfg, h):
    """كم نسبة منشورات اليوم المفروض تكون نزلت لين الساعة h (توزيع يركز على المساء)."""
    w = cfg.get("peak_weights", PEAK_WEIGHTS)
    total = sum((b - a) * k for a, b, k in w)
    got = sum((min(h, b) - a) * k for a, b, k in w if h > a)
    return min(1.0, got / total) if total else 1.0


def maybe_digest(cfg, state):
    """مرة باليوم بالمساء: منشور واحد فيه أفضل ٥ عروض اليوم (سهل ينشاف وينرسل)."""
    now = datetime.now(RIYADH)
    d = today()
    if not (cfg.get("digest_hour", 21) <= now.hour <= 23) or state.get("digest_day") == d:
        return
    items = [x for x in deals_site.load_products() if x.get("date") == d and x.get("link")]
    if len(items) < 5:
        return
    top = sorted(items, key=lambda x: -(x["disc"] + x["orders"] ** 0.5 + x["rating"] * 5))[:5]
    nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    lines = ["🏆 <b>أفضل 5 عروض اليوم</b>", ""]
    for n, x in zip(nums, top):
        name = html.escape(deals_site.display_name(x))
        link = html.escape(x["link"], quote=True)
        disc = f" · خصم {x['disc']}%" if x["disc"] else ""
        lines += [f'{n} <a href="{link}"><b>{name}</b></a>', f"💰 {money(x['price'])} {cfg.get('currency_label', 'ريال')}{disc}", ""]
    lines.append("📤 أرسليها لصديقاتك وخلّيهم يلحقون العروض")
    site = cfg.get("site_url", "").rstrip("/")
    kb = [[{"text": "🛍️ كل عروض اليوم", "url": f"{site}/"}, {"text": "🎟️ أكواد الخصم", "url": f"{site}/codes.html"}]] if site else []
    if cfg.get("amazon_tag"):
        kb.append([{"text": "🛒 عروض أمازون السعودية اليوم",
                    "url": deals_site.amazon_url(cfg, "https://www.amazon.sa/deals?language=ar_AE")}])
    tg(cfg, "sendMessage", chat_id=cfg["channel"], text="\n".join(lines), parse_mode="HTML",
       disable_web_page_preview=True, reply_markup={"inline_keyboard": kb})
    state["digest_day"] = d
    save_state(state)
    print("انتشر ملخص أفضل ٥ عروض اليوم")


AMAZON_FILE = deals_site.BASE_DIR / "data" / "amazon_queue.json"


def maybe_amazon(cfg, state):
    """ينشر منتج أمازون واحد من القائمة (data/amazon_queue.json) بشكل مرتب: صورة كبيرة من أمازون + رابط مخفي + زر."""
    tag = cfg.get("amazon_tag")
    if not tag or not AMAZON_FILE.exists():
        return
    data = json.loads(AMAZON_FILE.read_text(encoding="utf-8"))
    d = post_day()
    done = state.setdefault("amazon_daily", {})
    if done.get(d, 0) >= cfg.get("amazon_per_day", 3):
        return
    posted = set(data.get("posted", []))
    item = next((x for x in data.get("queue", []) if x["asin"] not in posted), None)
    if not item:
        return
    url = f"https://www.amazon.sa/dp/{item['asin']}?tag={tag}"
    lines = [f"{item.get('emoji', '🛒')} <b>{html.escape(item['name'])}</b>"]
    if item.get("line"):
        lines.append(html.escape(item["line"]))
    if item.get("rating"):
        lines.append(f"⭐ تقييم {item['rating']} من 5 من أكثر من {item.get('reviews', '')} مشترية")
    lines += ["🚚 شحن من أمازون السعودية", "", f'🛒 <a href="{html.escape(url, quote=True)}">اطلبي من أمازون</a>']
    tg(cfg, "sendMessage", chat_id=cfg["channel"], text="\n".join(lines), parse_mode="HTML",
       link_preview_options={"url": url, "prefer_large_media": True, "show_above_text": True},
       reply_markup={"inline_keyboard": [[{"text": "🛍️ اطلبي من أمازون", "url": url}]]})
    data.setdefault("posted", []).append(item["asin"])
    AMAZON_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    done[d] = done.get(d, 0) + 1
    state["amazon_daily"] = {k: v for k, v in done.items() if k >= d}
    save_state(state)
    print(f"انتشر منتج أمازون: {item['name']}")


def finish_site(cfg, new_ids):
    """نبني الموقع كل تشغيل (الرئيسية والأقسام والأكواد تتحدث حتى لو ما انتشر شي)."""
    if not cfg.get("site_url"):
        return
    try:
        ensure_home_link(cfg)
        deals_site.build(cfg, only_ids=new_ids)
        print(f"الموقع: انضافت {len(new_ids)} صفحة")
    except Exception as e:
        print(f"[warn] بناء الموقع فشل: {e}", file=sys.stderr)


def publish(cfg, state, p, d, count_daily=True):
    """ينشر منتج واحد في القروب ويسجّله. يرجع True لو انتشر."""
    p["promotion_link"] = short_link(cfg, p)
    p["_ships"] = ships_to(cfg, p)
    p["_title"] = product_name(cfg, p)
    if has_any(str(p.get("_title") or ""), MEN_AR):
        # الذكاء الاصطناعي عرف من الصورة/العنوان إنه رجالي: ما ننشره ونعلّمه عشان ما يرجع
        print(f"- تخطيت {p.get('product_id')}: طلع رجالي ({p['_title']})")
        state["posted"][str(p["product_id"])] = d
        return False
    p["_pitch"] = ai_pitch(cfg, p)
    if (p["_pitch"] or {}).get("name"):
        p["_title"] = p["_pitch"]["name"]  # اسم مطابق للصورة
    print(f"منتج {p.get('product_id')} ({site_cat(p)}): يوصل لـ {','.join(p['_ships'])}")
    try:
        send_product(cfg, p)
    except Exception as e:
        print(f"[warn] فشل نشر {p.get('product_id')}: {e}", file=sys.stderr)
        return False
    state["posted"][str(p["product_id"])] = d
    state["recent_keys"] = (state.get("recent_keys", []) + [title_key(p)])[-400:]
    state["recent_imgs"] = (state.get("recent_imgs", []) + [p["product_main_image_url"]])[-400:]
    state["last_cat"] = category(p)
    state["recent_cats"] = (state.get("recent_cats", []) + [site_cat(p)])[-10:]
    cd = state.setdefault("cat_day", {})
    for k in [k for k in cd if k != d]:
        del cd[k]
    cd.setdefault(d, {})[site_cat(p)] = cd.get(d, {}).get(site_cat(p), 0) + 1
    if count_daily:
        state["daily"][d] = state["daily"].get(d, 0) + 1
    save_state(state)
    return True


def extra_cats(cfg):
    return set(cfg.get("extra_daily", {}))


def maybe_extras(cfg, state, new_ids):
    """حصة يومية إضافية (فوق الـ٥٠): فساتين سهرة وحقائب سهرة، موزعة على المساء."""
    quotas = cfg.get("extra_daily", {})
    if not quotas:
        return
    d = post_day()
    now = datetime.now(RIYADH)
    h = now.hour + now.minute / 60
    if h < 5:
        h += 24  # بعد ١٢ الليل تنحسب على نفس يوم النشر
    start, end = cfg.get("extra_hours", [16, 24])
    if h < start:
        return
    share = min(1.0, (h + cfg.get("run_every_minutes", 30) / 60 - start) / max(0.5, end - start))
    ed = state.setdefault("extra_day", {})
    for k in [k for k in ed if k != d]:
        del ed[k]
    today_done = ed.setdefault(d, {})
    for cat, quota in quotas.items():
        done = today_done.get(cat, 0)
        n = min(-(-int(quota * share * 100) // 100) - done, cfg.get("extra_per_run", 3), quota - done)
        if n <= 0:
            continue
        ccfg = {**cfg, "_focus": True, "deep_search_cats": [], "keywords_per_run": 4,
                "keywords": [k for k in cfg["keywords"] if deals_site.category_for(k)["slug"] == cat]}
        if not ccfg["keywords"]:
            continue
        prods = pick(ccfg, state, fetch_candidates(ccfg), n)
        for p in prods:
            if publish(cfg, state, p, d, count_daily=False):
                today_done[cat] = today_done.get(cat, 0) + 1
                save_state(state)
                if cfg.get("site_url"):
                    deals_site.record(cfg, p, gallery(p))
                    new_ids.append(str(p["product_id"]))
                time.sleep(8)
        print(f"الحصة الإضافية {cat}: {today_done.get(cat, 0)}/{quota}")


def cmd_post(cfg, state, count, paced=True):
    try:
        maybe_amazon(cfg, state)
    except Exception as e:
        print(f"[warn] منشور أمازون فشل: {e}", file=sys.stderr)
    if paced:
        try:
            maybe_digest(cfg, state)
        except Exception as e:
            print(f"[warn] ملخص اليوم فشل: {e}", file=sys.stderr)
    d = post_day()
    new_ids = []
    if paced:
        try:
            maybe_extras(cfg, state, new_ids)
        except Exception as e:
            print(f"[warn] الحصة الإضافية فشلت: {e}", file=sys.stderr)
        # الأقسام الإضافية لها حصتها الخاصة، فما ندوّر عليها بالنشر العادي
        cfg = {**cfg, "keywords": [k for k in cfg["keywords"]
                                   if deals_site.category_for(k)["slug"] not in extra_cats(cfg)]}
    done = state["daily"].get(d, 0)
    left = cfg["posts_per_day"] - done if paced else count  # الدفعات اليدوية إضافية فوق الـ٥٠
    if left <= 0:
        print(f"وصلنا الحد اليومي ({cfg['posts_per_day']})")
        finish_site(cfg, new_ids)
        return 0
    count = min(count, left)
    # توزيع المنشورات بالتساوي على باقي اليوم بدل ما تخلص بدري
    start, end = cfg["post_hours"]
    now = datetime.now(RIYADH)
    h = now.hour + now.minute / 60
    if h < start and end > 24:
        h += 24
    runs_left = max(1, int((end - h) * 60 // cfg.get("run_every_minutes", 30)) + 1)
    if paced:
        # نكثّف النشر بالمساء: نحسب كم المفروض يكون نازل لين نهاية هالتشغيل
        step = cfg.get("run_every_minutes", 30) / 60
        allowed = -(-int(cfg["posts_per_day"] * day_share(cfg, h + step) * 100) // 100) - done
        if runs_left <= 1:
            allowed = left
        if allowed <= 0:
            print(f"ما فيه نشر هالتشغيل (النشر مركّز على المساء). مجموع اليوم: {done}/{cfg['posts_per_day']}")
            finish_site(cfg, new_ids)
            return 0
        count = min(count, allowed)
    cands = (fetch_candidates(cfg) if cfg["keywords"] else []) + fetch_store_candidates(cfg, state)
    prods = pick(cfg, state, cands, count)
    sent = 0
    for p in prods:
        if not publish(cfg, state, p, d, count_daily=paced):
            continue
        sent += 1
        if cfg.get("site_url"):
            deals_site.record(cfg, p, gallery(p))
            new_ids.append(str(p["product_id"]))
        if sent < len(prods):
            time.sleep(8)
    finish_site(cfg, new_ids)
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


def cmd_recaption(cfg, pairs):
    """يعيد كتابة نص منشورات قديمة (تعديل بنفس مكانها) بالقواعد الحالية. pairs = "رقم_المنتج:رقم_الرسالة,..." """
    jobs = [x.split(":") for x in pairs.split(",") if ":" in x]
    ids = [pid.strip() for pid, _ in jobs]
    prods = {str(p["product_id"]): p for p in extract_products(ali_call(cfg, "aliexpress.affiliate.productdetail.get", {
        "product_ids": ",".join(ids), "target_currency": cfg["currency"], "target_language": cfg["language"],
        "ship_to_country": cfg["ship_to"], "tracking_id": cfg["tracking_id"], "fields": DETAIL_FIELDS}))}
    done = 0
    for pid, msg in jobs:
        p = prods.get(pid.strip())
        if not p:
            print(f"✗ {pid}: ما رجع من علي إكسبريس")
            continue
        p["_single"] = True  # المنشورات الحالية صورة وحدة مع أزرار
        p["promotion_link"] = short_link(cfg, p)
        p["_ships"] = ships_to(cfg, p)
        p["_title"] = product_name(cfg, p)
        p["_pitch"] = ai_pitch(cfg, p)
        if (p["_pitch"] or {}).get("name"):
            p["_title"] = p["_pitch"]["name"]
        text = caption(cfg, p)
        base = dict(chat_id=cfg["channel"], message_id=int(msg), caption=text, parse_mode="HTML")
        try:
            tg(cfg, "editMessageCaption", **base, reply_markup={"inline_keyboard": post_buttons(cfg, p)})
        except Exception as e:
            print(f"[warn] {msg}: {e}", file=sys.stderr)
            try:
                tg(cfg, "editMessageCaption", **base)
            except Exception as e2:
                print(f"✗ {msg}: {e2}")
                continue
        done += 1
        print(f"✓ عدّلت الرسالة {msg}: {p['_title']} | {(p.get('_pitch') or {}).get('hook', '')}")
        time.sleep(3)
    print(f"انعدّل {done} من {len(jobs)}")
    return 0


SCOUT_KEYWORDS = [
    "luxury evening dress women", "arabic evening dress", "dubai evening gown", "elegant evening gown long",
    "muslim evening dress long sleeve", "sequin evening dress", "satin evening gown", "beaded evening dress",
    "kaftan evening dress", "mermaid evening dress", "evening dress plus size", "prom dress long elegant",
]


def cmd_scout(cfg):
    """يدوّر فساتين سهرة من متاجر مشتركة بالعمولة، ويرتّب المتاجر حسب الجودة (للاختيار اليدوي)."""
    fields = ("product_id,product_title,target_sale_price,discount,evaluate_rate,lastest_volume,"
              "product_main_image_url,commission_rate,shop_id,shop_name")
    shops = {}
    for kw in SCOUT_KEYWORDS:
        for page in (1, 2, 3):
            try:
                prods = extract_products(ali_call(cfg, "aliexpress.affiliate.product.query", {
                    "keywords": kw, "tracking_id": cfg["tracking_id"], "target_currency": cfg["currency"],
                    "target_language": "EN", "ship_to_country": cfg["ship_to"], "sort": "LAST_VOLUME_DESC",
                    "page_size": 50, "page_no": page, "fields": fields}))
            except Exception as e:
                print(f"[warn] {kw} {page}: {e}", file=sys.stderr)
                continue
            for p in prods:
                if not is_evening(p) or blocked(p) or is_mens(p) or no_brand(p.get("shop_name")):
                    continue
                s = shops.setdefault(str(p.get("shop_id")), {"name": p.get("shop_name"), "items": {}})
                s["items"][str(p["product_id"])] = p
    rows = []
    for sid, s in shops.items():
        items = list(s["items"].values())
        good = [p for p in items if num(p.get("evaluate_rate")) >= 95 and num(p.get("lastest_volume")) >= 30]
        if len(good) < 3:
            continue
        orders = sum(num(p.get("lastest_volume")) for p in good)
        rating = sum(num(p.get("evaluate_rate")) for p in good) / len(good)
        good.sort(key=lambda p: -num(p.get("lastest_volume")))
        rows.append({"shop_id": sid, "name": s["name"], "n": len(good), "orders": int(orders),
                     "rating": round(rating, 1), "max_orders": int(num(good[0].get("lastest_volume"))),
                     "items": [{"id": str(p["product_id"]), "img": p.get("product_main_image_url", "").split("?")[0],
                                "price": p.get("target_sale_price"), "orders": int(num(p.get("lastest_volume"))),
                                "rating": p.get("evaluate_rate"), "disc": p.get("discount"),
                                "comm": p.get("commission_rate"), "title": str(p.get("product_title", ""))[:70]}
                               for p in good[:6]]})
    rows.sort(key=lambda r: -(r["orders"] * (r["rating"] / 100) ** 4))
    print(f"متاجر سهرة مؤهلة: {len(rows)}")
    for r in rows[:15]:
        print("SCOUT " + json.dumps(r, ensure_ascii=False))
    return 0


def main():
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["check", "preview", "post", "run", "fields", "linktest", "site", "backfill", "prune", "retitle", "scout", "recaption"])
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--ids", default="", help="للتعديل: رقم_المنتج:رقم_الرسالة,...")
    ap.add_argument("--focus", default="", help="أقسام بس، مثل: dresses,evening-dresses,bags")
    a = ap.parse_args()
    cfg = load_config()
    focus = {x.strip() for x in a.focus.split(",") if x.strip()}
    if focus:
        cfg["keywords"] = [k for k in cfg["keywords"] if deals_site.category_for(k)["slug"] in focus]
        cfg["keywords_per_run"] = max(cfg.get("keywords_per_run", 3), 4)
        cfg["_focus"] = True  # نشر مركّز على قسم: نسمح بأكثر من منتج من نفس القسم
        print(f"تركيز على: {', '.join(sorted(focus))}")
    if a.command == "check":
        return cmd_check(cfg)
    if a.command == "fields":
        return cmd_fields(cfg)
    if a.command == "recaption":
        return cmd_recaption(cfg, a.ids)
    if a.command == "scout":
        return cmd_scout(cfg)
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
        return cmd_post(cfg, load_state(), a.count, paced=not focus)
    return cmd_run(cfg)


if __name__ == "__main__":
    sys.exit(main())
