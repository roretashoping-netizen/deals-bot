"""
موقع العروض: يبني صفحات هبوط ثابتة (HTML) لكل منتج ينشره البوت، مع صفحات فئات وخريطة موقع.
ينبني داخل مجلد docs/ وينشره GitHub Pages مجانًا.
"""
import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

BASE_DIR = Path(__file__).resolve().parent
PRODUCTS_FILE = BASE_DIR / "data" / "products.json"
HOME_LINK_FILE = BASE_DIR / "data" / "home_link.txt"
OUT = BASE_DIR / "docs"
SITE_VERSION = "9"  # لما يتغير شكل الصفحات نرفع الرقم، فينبني الموقع كامل مرة وحدة
RIYADH = timezone(timedelta(hours=3))

# الفئات: كل كلمة بحث تنتمي لفئة لها صفحة وهاشتاق
CATEGORIES = [
    {"slug": "evening-dresses", "name": "فساتين سهرة", "tag": "فساتين_سهرة",
     "keywords": ["Sharon Said Boutique evening dress", "Sharon Said evening dress", "Sharon Said dress",
                  "Serene Hill evening dress", "Serene Hill dress", "Serene Hill Boutique",
                  "arabic evening dress", "dubai evening gown", "muslim evening dress long sleeve",
                  "luxury evening dress women", "Sharon Said Couture evening dress", "Johara Al Sharq gown"]},
    {"slug": "dresses", "name": "فساتين", "tag": "فساتين",
     "keywords": ["women maxi dress", "modest long dress women", "women long sleeve dress", "women casual dress"]},
    {"slug": "abayas", "name": "عبايات", "tag": "عبايات", "keywords": ["abaya women"]},
    {"slug": "makeup-tools", "name": "أدوات المكياج", "tag": "أدوات_مكياج",
     "keywords": ["makeup brushes set", "makeup organizer", "eyelash curler", "makeup sponge set",
                  "makeup storage box", "makeup mirror led"]},
    {"slug": "nails", "name": "أدوات الأظافر", "tag": "أظافر", "keywords": ["nail art kit", "nail art tools"]},
    {"slug": "hair", "name": "أدوات وإكسسوارات الشعر", "tag": "اكسسوارات_شعر",
     "keywords": ["hair curler", "hair straightener brush", "hair clips women"]},
    {"slug": "skincare-tools", "name": "أدوات العناية بالبشرة", "tag": "عناية_بالبشرة",
     "keywords": ["skin care tools", "facial cleansing brush"]},
    {"slug": "jewelry", "name": "إكسسوارات ومجوهرات", "tag": "اكسسوارات",
     "keywords": ["jewelry box organizer", "women earrings", "women necklace"]},
    {"slug": "bags", "name": "شنط ومحافظ", "tag": "شنط",
     "keywords": ["genuine leather handbag women", "women leather shoulder bag", "women leather crossbody bag",
                  "women leather tote bag", "luxury leather handbag women", "cowhide leather bag women",
                  "women leather top handle bag", "women leather wallet",
                  "FOXER genuine leather bag women", "ZOOLER genuine leather handbag", "BOSTANTEN leather handbag women",
                  "Cnoles genuine leather bag women", "LAORENTOU cow leather bag women"]},
    {"slug": "kitchen", "name": "أدوات المطبخ", "tag": "أدوات_مطبخ",
     "keywords": ["kitchen gadgets", "kitchen organizer", "kitchen storage", "silicone kitchen utensils",
                  "spice rack organizer", "coffee tools", "baking tools"]},
    {"slug": "home", "name": "ديكور وتنظيم البيت", "tag": "ديكور", "keywords": ["home decor women", "perfume atomizer"]},
    {"slug": "loungewear", "name": "بيجامات وشباشب", "tag": "بيجامات", "keywords": ["women pajamas", "women slippers"]},
]
OTHER = {"slug": "more", "name": "منوعات", "tag": "منوعات", "keywords": []}


def category_for(kw):
    for c in CATEGORIES:
        if kw in c["keywords"]:
            return c
    return OTHER


# لمنتجات قديمة ما نعرف كلمة بحثها: نخمن الفئة من العنوان (الترتيب مهم)
TITLE_RULES = [
    ("nails", ["nail", "أظافر", "اظافر", "مناكير"]),
    ("abayas", ["abaya", "kaftan", "عباية", "عبايه", "قفطان"]),
    ("jewelry", ["watch", "ساعة", "ساعات"]),
    ("evening-dresses", ["evening", "party dress", "gown", "prom", "سهرة", "حفلة"]),
    ("dresses", ["dress", "فستان", "فساتين"]),
    ("loungewear", ["pajama", "sleepwear", "slipper", "sandal", "بيجامة", "بيجامات", "شبشب", "شباشب", "نعال"]),
    ("kitchen", ["kitchen", "cook", "spice", "coffee", "baking", "مطبخ", "طبخ", "بهارات", "قهوة", "خبز", "مطابخ"]),
    ("hair", ["hair", "شعر"]),
    ("skincare-tools", ["skin", "facial", "face ", "بشرة", "البشرة", "الوجه"]),
    ("makeup-tools", ["makeup", "make up", "brush", "sponge", "eyelash", "mirror", "مكياج", "فرشاة", "فرش", "رموش", "مرآة", "مراية"]),
    ("jewelry", ["earring", "necklace", "bracelet", "jewelry", "jewellery", "أقراط", "حلق", "قلادة", "سوار", "مجوهرات", "خاتم"]),
    ("bags", ["handbag", "bag", "wallet", "purse", "حقيبة", "شنطة", "محفظة"]),
    ("home", ["decor", "storage", "organizer", "ديكور", "منظم", "تخزين", "رف"]),
]


# أقسام لازم عنوان المنتج يذكر نوعه (البحث في علي إكسبريس يرجع أحيانًا منتجات ما لها علاقة)
CAT_WORDS = {
    "evening-dresses": ("dress", "gown", "فستان", "فساتين"),
    "dresses": ("dress", "gown", "فستان", "فساتين"),
    "bags": ("bag", "handbag", "purse", "tote", "clutch", "wallet", "حقيبة", "شنطة", "شنط", "محفظة"),
    "abayas": ("abaya", "kaftan", "عباية", "عبايه", "قفطان"),
}


# كلمات تمنع المنتج من القسم حتى لو عنوانه فيه كلمة القسم (مثل: ساعة "فستان" = ساعة مو فستان)
CAT_NOT = {
    "evening-dresses": ("watch", "ساعة", "ساعات"),
    "dresses": ("watch", "ساعة", "ساعات"),
}


def fits_cat(slug, title):
    t = str(title).lower()
    if any(w in t for w in CAT_NOT.get(slug, ())):
        return False
    words = CAT_WORDS.get(slug)
    return not words or any(w in t for w in words)


def infer_cat(title):
    t = str(title).lower()
    for slug, words in TITLE_RULES:
        if any(w in t for w in words):
            return slug
    return OTHER["slug"]


def cat_by_slug(slug):
    for c in CATEGORIES + [OTHER]:
        if c["slug"] == slug:
            return c
    return OTHER


def num(x):
    try:
        return float(str(x).replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def money(x):
    return f"{x:,.0f}" if float(x).is_integer() else f"{x:,.2f}"


def esc(s):
    return html.escape(str(s), quote=True)


def clean_title(title, limit=70):
    """يحوّل عنوان علي إكسبريس المترجم آليًا لاسم قصير مفهوم."""
    s = str(title or "").strip()
    s = re.sub(r"[\[\(【（][^\]\)】）]*[\]\)】）]", " ", s)          # نشيل اللي بين الأقواس
    parts = [x.strip() for x in re.split(r"\s*[،,;؛|.:]\s*|\s+[-–—]\s+", s) if x.strip()]
    s = parts[0] if parts else s
    if len(s) < 18 and len(parts) > 1:
        s = f"{s} {parts[1]}"
    words = []
    for w in s.split():
        if re.fullmatch(r"[A-Za-z]{1,3}\d+[A-Za-z]*|\d+[A-Za-z]+\d*|[A-Za-z]|20\d\d", w):
            continue                                                   # أكواد مثل F1 / EF2 / 2025 / U
        if words and w == words[-1]:
            continue
        words.append(w)
    s = " ".join(words)
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0]
    return s.strip(" -–—،,") or str(title)[:limit]


def display_name(x):
    """الاسم اللي ينعرض: اسم الذكاء الاصطناعي لو موجود، وإلا العنوان بعد التنظيف."""
    return x.get("name") or clean_title(x["title"])


def _riyadh_today():
    return datetime.now(RIYADH).strftime("%Y-%m-%d")


def active_codes(cfg):
    """أكواد علي إكسبريس العامة (للخليج) لو ما انتهت."""
    pr = cfg.get("promos") or {}
    if not pr.get("codes") or _riyadh_today() > str(pr.get("codes_until", "9999")):
        return []
    return sorted(pr["codes"], key=lambda c: c["min"])


def active_notes(cfg):
    """عروض البنوك والملاحظات اللي ما انتهت."""
    pr = cfg.get("promos") or {}
    t = _riyadh_today()
    out = [n["text"] for n in pr.get("notes", []) if t <= str(n.get("until", "9999"))]
    if cfg.get("promo_note"):
        out.append(cfg["promo_note"])
    return out


def code_line(cfg, price):
    """سطر كود واحد يناسب سعر المنتج: أعلى فئة توصلها، وإلا أصغر فئة (تجمعين منتجات بالسلة)."""
    codes = active_codes(cfg)
    if not codes:
        return ""
    try:
        price = float(price or 0)
    except (TypeError, ValueError):
        price = 0
    fit = [c for c in codes if c["min"] <= price]
    c = fit[-1] if fit else codes[0]
    until = str((cfg.get("promos") or {}).get("codes_until", ""))
    end = f" · حتى {int(until[8:10])} {MONTHS_AR[int(until[5:7]) - 1]}" if len(until) == 10 else ""
    more = "" if fit else " (اجمعي أكثر من منتج بالسلة)"
    rows = [f"{x.get('flag', '')} خصم {_fx(c['off'], GCC_FX[x['code']][0])} {GCC_FX[x['code']][2]}"
            f" للطلب فوق {_fx(c['min'], GCC_FX[x['code']][0])}"
            for x in cfg.get("countries", []) if x["code"] in GCC_FX]
    return "\n".join([f"🎟️ كود الخصم: <b>{esc(c['code'])}</b>{end}"] + rows + ([more.strip()] if more else []))


# تقريب الحد الأدنى بعملة كل دولة (علي إكسبريس يحوّل من الريال وقت الدفع)
GCC_FX = {"SA": (1, "ريال سعودي", "ريال"), "AE": (0.98, "درهم", "درهم"), "KW": (0.082, "دينار كويتي", "دينار"),
          "QA": (0.97, "ريال قطري", "ريال"), "BH": (0.1, "دينار بحريني", "دينار"), "OM": (0.1025, "ريال عماني", "ريال")}


def _fx(v, rate):
    x = v * rate
    return f"{x:,.0f}" if x >= 20 else f"{x:,.1f}".replace(".0", "")


MONTHS_AR = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس",
             "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]


def codes_page(cfg):
    """صفحة «كود خصم علي اكسبريس» تتحدث كل تشغيل، وتختفي الأكواد المنتهية لحالها."""
    codes = active_codes(cfg)
    notes = active_notes(cfg)
    pr = cfg.get("promos") or {}
    until = str(pr.get("codes_until", ""))
    rows = "".join(f"<tr><td><b>{esc(c['code'])}</b></td><td>{c['off']} ريال</td><td>فوق {c['min']:,} ريال</td></tr>"
                   for c in codes)
    table = (f"<h2>أكواد خصم علي إكسبريس للسعودية والخليج (بالريال السعودي)</h2>"
             f"<table class='codes'><tr><th>الكود</th><th>الخصم</th><th>الطلب</th></tr>{rows}</table>"
             + (f"<p class='note'>صالحة لين {esc(until)} (23:59 بتوقيت السعودية) أو لين تخلص الكمية. "
                f"كود واحد لكل طلب، وتكتبينه في خانة «كود الخصم» عند الدفع.</p>" if until else "")) if codes else \
            "<p>ما فيه أكواد عامة شغالة الحين. نحدّث الصفحة أول ما ينزل كود جديد.</p>"
    per = ""
    if codes:
        heads = "".join(f"<th>{esc(c.get('flag', ''))} {esc(c['name'])}</th>" for c in cfg.get("countries", []) if c["code"] in GCC_FX)
        body_rows = ""
        for k in codes:
            cells = "".join(
                f"<td>{_fx(k['off'], GCC_FX[c['code']][0])} / {_fx(k['min'], GCC_FX[c['code']][0])}</td>"
                for c in cfg.get("countries", []) if c["code"] in GCC_FX)
            body_rows += f"<tr><td><b>{esc(k['code'])}</b></td>{cells}</tr>"
        units = " · ".join(f"{esc(c['name'])}: {GCC_FX[c['code']][1]}" for c in cfg.get("countries", []) if c["code"] in GCC_FX)
        per = (f"<h2>نفس الأكواد لكل دول الخليج</h2><p>الأكواد تشتغل على الطلبات اللي تنشحن للسعودية والإمارات والكويت "
               f"وقطر والبحرين وعمان. هذا الخصم / الحد الأدنى بعملة كل دولة (تقريبًا، علي إكسبريس يحسبها من الريال وقت الدفع):</p>"
               f"<div style='overflow-x:auto'><table class='codes'><tr><th>الكود</th>{heads}</tr>{body_rows}</table></div>"
               f"<p class='note'>{units}</p>")
    nb = ("<h2>عروض البنوك والبطاقات</h2>" + "".join(f"<div class='promo'>{n}</div>" for n in notes)) if notes else ""
    tg = f"https://t.me/{cfg['telegram_public'].lstrip('@')}"
    home = HOME_LINK_FILE.read_text(encoding="utf-8").strip() if HOME_LINK_FILE.exists() else ""
    shop = (f"<a class='buy' href='{esc(home)}' target='_blank' rel='nofollow sponsored noopener'>"
            f"🛍️ ادخلي علي إكسبريس واستخدمي الكود</a>") if home.startswith("http") else ""
    body = (f"<h1>كود خصم علي اكسبريس اليوم</h1><p class='intro'>أكواد خصم علي إكسبريس الشغالة الحين للسعودية "
            f"والإمارات والكويت وقطر والبحرين وعمان، وعروض البنوك على علي إكسبريس. نشيل المنتهي أول بأول.</p>"
            f"{table}{shop}{per}{nb}{shop}<p><a class='join' href='{tg}'>📢 انضمي لقروب التليجرام عشان يوصلك كل كود جديد</a></p>")
    return page(cfg, "كود خصم علي اكسبريس اليوم للسعودية والخليج | عروض البنوك",
                "أكواد خصم علي إكسبريس الشغالة اليوم للسعودية والخليج بالريال، مع عروض البنوك والبطاقات.",
                "codes.html", body)


def promo_text(info):
    """نص كود الخصم لو علي إكسبريس أرسل كود للمنتج."""
    if isinstance(info, list):
        info = info[0] if info else None
    if not isinstance(info, dict) or not info.get("promo_code"):
        return ""
    txt = f"🏪 كود المتجر: <b>{esc(info['promo_code'])}</b>"
    if info.get("code_value"):
        txt += f" ({esc(info['code_value'])})"
    if info.get("code_mini_spend"):
        txt += f" عند الشراء بـ {esc(info['code_mini_spend'])}"
    end = str(info.get("code_availabletime_end") or "")[:10]
    if end:
        txt += f" · ينتهي {esc(end)}"
    return txt


# ---------------------------------------------------------------- تخزين المنتجات
def load_products():
    if PRODUCTS_FILE.exists():
        return json.loads(PRODUCTS_FILE.read_text(encoding="utf-8"))
    return []


def record(cfg, p, gallery):
    """يحفظ المنتج المنشور عشان تنبني له صفحة."""
    items = load_products()
    pid = str(p["product_id"])
    items = [x for x in items if x["id"] != pid]
    cat = cat_by_slug(p["_cat"]) if p.get("_cat") else category_for(p.get("_kw", ""))
    if not fits_cat(cat["slug"], p.get("product_title", "")):
        cat = cat_by_slug(infer_cat(p.get("product_title", "")))
    items.append({
        "id": pid,
        "title": str(p.get("product_title", "")).strip(),
        "price": num(p.get("target_sale_price")),
        "old": num(p.get("target_original_price")),
        "disc": int(num(p.get("discount"))),
        "rating": round(num(p.get("evaluate_rate")) / 20, 1),
        "orders": int(num(p.get("lastest_volume"))),
        "image": p.get("product_main_image_url", ""),
        "gallery": [u for u in gallery if u != p.get("product_main_image_url")][:5],
        "link": p.get("promotion_link", ""),
        "ships": p.get("_ships", []),
        "shop": str(p.get("shop_name", "") or ""),
        "cat": cat["slug"],
        "promo": promo_text(p.get("promo_code_info")),
        "name": p.get("_title") or "",
        **{k: v for k, v in (p.get("_seo") or {}).items() if k in ("seo_title", "about")},
        "date": datetime.now(RIYADH).strftime("%Y-%m-%d"),
    })
    items = items[-5000:]
    PRODUCTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PRODUCTS_FILE.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- القوالب
CSS = """
:root{--bg:#fff7f9;--card:#fff;--ink:#2b1d24;--muted:#7a6670;--brand:#e62e5c;--brand2:#ff8a3d;--ok:#1f9d55;--line:#f1dde4}
*{box-sizing:border-box}body{margin:0;font-family:Tahoma,"Segoe UI",Arial,sans-serif;background:var(--bg);color:var(--ink);line-height:1.7}
a{color:inherit}.wrap{max-width:1100px;margin:auto;padding:0 16px}
header{background:linear-gradient(90deg,var(--brand),var(--brand2));color:#fff}
header .wrap{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 16px;flex-wrap:wrap}
.logo{font-weight:700;font-size:20px;text-decoration:none}
.tg{background:#fff;color:var(--brand);padding:8px 14px;border-radius:999px;text-decoration:none;font-weight:700;font-size:14px;white-space:nowrap}
nav.cats{display:flex;gap:10px;overflow-x:auto;padding:14px 16px;max-width:1100px;margin:auto;scroll-snap-type:x proximity;-webkit-overflow-scrolling:touch;scrollbar-width:none}
nav.cats::-webkit-scrollbar{display:none}
nav.cats a{background:#fff;border:2px solid var(--line);padding:12px 20px;min-height:48px;display:flex;align-items:center;border-radius:999px;text-decoration:none;white-space:nowrap;font-size:16px;font-weight:700;scroll-snap-align:start;box-shadow:0 1px 3px rgba(0,0,0,.06)}
nav.cats a:active{background:var(--brand);color:#fff;border-color:var(--brand)}
.codes{width:100%;border-collapse:collapse;margin:10px 0;font-size:15px}.codes td,.codes th{border:1px solid #eee;padding:8px;text-align:center}.codes th{background:#fff4d6}
.promo{background:#fff4d6;border:1px dashed #e0a800;border-radius:10px;padding:10px;margin-top:10px;font-size:15px}
h1{font-size:24px;margin:18px 0 6px}h2{font-size:20px;margin:24px 0 10px}
.intro{color:var(--muted);margin:0 0 16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;text-decoration:none;display:flex;flex-direction:column}
.card img{width:100%;aspect-ratio:1;object-fit:cover;background:#f6eef1}
.card .t{font-size:13px;padding:8px 10px 0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card .p{padding:4px 10px 10px;font-weight:700;color:var(--brand)}
.card .p s{color:var(--muted);font-weight:400;font-size:12px;margin-inline-start:6px}
.badge{display:inline-block;background:var(--brand);color:#fff;border-radius:6px;padding:0 6px;font-size:12px;margin:8px 10px 0;width:max-content}
.prod{display:grid;grid-template-columns:1fr;gap:20px;margin-top:16px}
@media(min-width:800px){.prod{grid-template-columns:1fr 1fr}}
.gal img{width:100%;border-radius:14px;background:#f6eef1}
.thumbs{display:flex;gap:8px;overflow-x:auto;margin-top:8px}.thumbs img{width:72px;height:72px;object-fit:cover;border-radius:8px}
.box{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px}
.now{font-size:28px;font-weight:700;color:var(--brand)}.was{color:var(--muted)}.was s{font-size:18px}
.save{color:var(--ok);font-weight:700}
.meta{margin:12px 0;padding:0;list-style:none}.meta li{padding:4px 0;border-bottom:1px dashed var(--line)}
.buy{display:block;text-align:center;background:linear-gradient(90deg,var(--brand),var(--brand2));color:#fff;text-decoration:none;padding:14px;border-radius:12px;font-size:18px;font-weight:700;margin-top:14px}
.share{display:flex;gap:8px;margin-top:10px}.share a{flex:1;text-align:center;color:#fff;text-decoration:none;padding:11px 6px;border-radius:12px;font-weight:700;font-size:15px}.share .wa{background:#25d366}.share .tgs{background:#229ed9}
.best{display:flex;gap:12px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px;margin:12px 0;position:relative}
.best img{width:120px;height:120px;object-fit:cover;border-radius:10px}.best h2{font-size:17px;margin:0 0 4px}.best h2 a{text-decoration:none}.best p{margin:2px 0;font-size:14px}
.best .bp{font-weight:700;color:var(--brand);font-size:16px}.best .bp s{color:var(--muted);font-weight:400;margin:0 6px}.best .bm{color:var(--muted)}.best .bt{flex:1;min-width:0}.best .buy{padding:9px;font-size:15px;margin-top:8px}
.bn{position:absolute;top:-8px;right:-8px;background:var(--brand);color:#fff;width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700}
.bests{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 14px}.bests a{background:#fff4d6;border:1px solid #e0a800;border-radius:999px;padding:8px 14px;text-decoration:none;font-weight:700;font-size:14px}
.join{display:block;text-align:center;background:#229ed9;color:#fff;text-decoration:none;padding:12px;border-radius:12px;font-weight:700;margin-top:10px}
.crumbs{font-size:13px;color:var(--muted);margin-top:12px}.crumbs a{color:var(--muted)}
.note{font-size:13px;color:var(--muted)}
footer{margin-top:40px;padding:24px 0;background:#2b1d24;color:#e9dfe3;font-size:13px}footer a{color:#fff}
"""


def page(cfg, title, desc, path, body, jsonld=None, image=None):
    site = cfg["site_url"].rstrip("/")
    url = f"{site}/{path}" if path else f"{site}/"
    tg = f"https://t.me/{cfg['telegram_public'].lstrip('@')}"
    verify = cfg.get("google_site_verification", "")
    cats = f'<a href="{site}/codes.html">🎟️ أكواد الخصم</a>' + \
        (f'<a href="{site}/amazon.html">🛒 عروض أمازون</a>' if cfg.get("amazon_tag") else "") + \
        "".join(f'<a href="{site}/c/{c["slug"]}.html">{esc(c["name"])}</a>' for c in COLLECTIONS) + \
        "".join(f'<a href="{site}/c/{c["slug"]}.html">{esc(c["name"])}</a>' for c in CATEGORIES)
    ld = "".join(f'<script type="application/ld+json">{json.dumps(j, ensure_ascii=False)}</script>' for j in (jsonld or []))
    return f"""<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="website"><meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}"><meta property="og:url" content="{url}">
{f'<meta property="og:image" content="{esc(image)}">' if image else ''}
<meta name="twitter:card" content="summary_large_image">
{f'<meta name="google-site-verification" content="{esc(verify)}">' if verify else ''}
<link rel="stylesheet" href="{site}/style.css">
{ld}
</head><body>
<header><div class="wrap"><a class="logo" href="{site}/">🔥 {esc(cfg['site_name'])}</a>
<a class="tg" href="{tg}" target="_blank" rel="noopener">انضمي لقروب العروض على تليجرام</a></div></header>
<nav class="cats">{cats}</nav>
<main class="wrap">{body}</main>
<footer><div class="wrap">
<p>{esc(cfg['site_name'])}: عروض وتخفيضات علي إكسبريس يوميًا، مختارة بتقييم عالي وتوصيل للسعودية والخليج.</p>
<p>ℹ️ بعض الروابط في هذا الموقع روابط تسويق بالعمولة من برنامج علي إكسبريس وبرنامج أمازون، وما تزيد السعر عليك. بصفتنا مسوّقين بالعمولة مع أمازون، نربح من عمليات الشراء المؤهلة. الأسعار تتغير باستمرار، والسعر النهائي هو الموجود في صفحة علي إكسبريس.</p>
<p><a href="{tg}">تليجرام</a> · <a href="{site}/sitemap.xml">خريطة الموقع</a></p>
</div></footer></body></html>"""


def card(cfg, x):
    site = cfg["site_url"].rstrip("/")
    old = f"<s>{money(x['old'])}</s>" if x["old"] > x["price"] > 0 else ""
    badge = f'<span class="badge">خصم {x["disc"]}%</span>' if x["disc"] else ""
    return (f'<a class="card" href="{site}/p/{x["id"]}.html">'
            f'<img referrerpolicy="no-referrer" src="{esc(x["image"])}" alt="{esc(display_name(x))}" loading="lazy">'
            f'{badge}<span class="t">{esc(display_name(x))}</span>'
            f'<span class="p">{money(x["price"])} ريال{old}</span></a>')


def product_page(cfg, x, related):
    site = cfg["site_url"].rstrip("/")
    cat = cat_by_slug(x["cat"])
    name = display_name(x)
    short = name if len(name) <= 60 else clean_title(name, 60)
    disc_txt = f" بخصم {x['disc']}%" if x["disc"] else ""
    seo_t = x.get("seo_title") or short
    title = f"{seo_t}{disc_txt} | علي اكسبريس" if len(seo_t) <= 50 else f"{seo_t} | علي اكسبريس"
    desc = ((x["about"].split(".")[0] + ". ") if x.get("about") else "") + (f"{name} من علي إكسبريس بسعر {money(x['price'])} ريال"
            + (f" بدل {money(x['old'])} ريال" if x["old"] > x["price"] > 0 else "")
            + (f"، تقييم {x['rating']} من 5" if x["rating"] else "")
            + (f" وأكثر من {x['orders']:,} طلب" if x["orders"] else "")
            + ". توصيل للسعودية والخليج.")
    desc = desc[:300]
    names = {c["code"]: f'{c["flag"]} {c["name"]}' for c in cfg.get("countries", [])}
    ships = " ".join(names[c] for c in x.get("ships", []) if c in names)
    imgs = [x["image"]] + x.get("gallery", [])
    thumbs = "".join(f'<img referrerpolicy="no-referrer" src="{esc(u)}" alt="صورة {i + 2} لـ {esc(short)}" loading="lazy">'
                     for i, u in enumerate(imgs[1:]))
    old_html = (f'<div class="was">السعر قبل: <s>{money(x["old"])}</s> ريال</div>'
                f'<div class="save">🔥 خصم {x["disc"]}% · توفّرين {money(x["old"] - x["price"])} ريال</div>'
                if x["old"] > x["price"] > 0 else "")
    meta = ""
    if x["rating"]:
        meta += f"<li>⭐ التقييم: {x['rating']} من 5</li>"
    if x["orders"]:
        meta += f"<li>🛒 الطلبات: أكثر من {x['orders']:,} طلب</li>"
    if ships:
        meta += f"<li>🚚 الشحن متوفر لـ: {ships}</li>"
    if x["shop"]:
        meta += f"<li>🏪 المتجر: {esc(x['shop'])}</li>"
    meta += f"<li>📅 أضيف للعروض: {x['date']}</li>"
    intro = esc(x["about"]) if x.get("about") else esc(x["title"]) + "."
    about = (f"<h2>عن المنتج</h2><p>{intro} هذا المنتج من فئة {esc(cat['name'])} في علي إكسبريس، "
             f"اخترناه لأن تقييمه عالي" + (f" وعليه أكثر من {x['orders']:,} طلب" if x["orders"] else "")
             + f"، ومتوفر بسعر {money(x['price'])} ريال"
             + (f" بعد خصم {x['disc']}%" if x["disc"] else "") + ". "
             "تأكدي من الخيارات والمقاسات والسعر النهائي في صفحة المنتج على علي إكسبريس قبل الطلب.</p>")
    rel = ("<h2>منتجات مشابهة</h2><div class='grid'>" + "".join(card(cfg, r) for r in related) + "</div>") if related else ""
    tg = f"https://t.me/{cfg['telegram_public'].lstrip('@')}"
    body = f"""
<div class="crumbs"><a href="{site}/">الرئيسية</a> › <a href="{site}/c/{cat['slug']}.html">{esc(cat['name'])}</a></div>
<div class="prod">
 <div class="gal"><img referrerpolicy="no-referrer" src="{esc(x['image'])}" alt="{esc(name)}"><div class="thumbs">{thumbs}</div></div>
 <div><h1>{esc(name)}</h1>
  <div class="box"><div>السعر الحين:</div><div class="now">{money(x['price'])} ريال</div>{old_html}
   {f'<div class="promo">{x["promo"]}</div>' if x.get("promo") else ''}
   <a class="promo" style="display:block" href="{site}/codes.html">🎟️ أكواد خصم علي إكسبريس وعروض البنوك الحين ←</a>
   <ul class="meta">{meta}</ul>
   <a class="buy" href="{esc(x['link'])}" target="_blank" rel="nofollow sponsored noopener">🛍️ اطلبي من علي إكسبريس</a>
   {share_html(f"{site}/p/{x['id']}.html", f"{name} بـ {money(x['price'])} ريال من علي إكسبريس")}
   <a class="join" href="{tg}" target="_blank" rel="noopener">📢 عروض يومية مثل هذا؟ انضمي لقروب التليجرام</a>
   <p class="note">السعر وقت الإضافة، وقد يتغير في علي إكسبريس.</p>
  </div></div>
</div>
{about}{rel}"""
    jsonld = [
        {"@context": "https://schema.org", "@type": "Product", "name": x["title"][:150], "image": imgs[:5],
         "category": cat["name"],
         "offers": {"@type": "Offer", "price": f"{x['price']:.2f}", "priceCurrency": "SAR",
                    "availability": "https://schema.org/InStock", "url": f"{site}/p/{x['id']}.html"}},
        {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "الرئيسية", "item": f"{site}/"},
            {"@type": "ListItem", "position": 2, "name": cat["name"], "item": f"{site}/c/{cat['slug']}.html"},
            {"@type": "ListItem", "position": 3, "name": short}]},
    ]
    return page(cfg, title, desc, f"p/{x['id']}.html", body, jsonld, x["image"])


# صفحات تجميعية تستهدف عبارات يبحث فيها الناس في قوقل
COLLECTIONS = [
    {"slug": "under-10", "name": "أقل من 10 ريال", "title": "منتجات علي اكسبريس بأقل من 10 ريال | عروض رخيصة اليوم",
     "intro": "منتجات من علي إكسبريس سعرها أقل من 10 ريال، مختارة بتقييم عالي وطلبات كثيرة، مع توصيل للسعودية والخليج.",
     "pick": lambda xs: [x for x in xs if 0 < x["price"] < 10]},
    {"slug": "under-25", "name": "أقل من 25 ريال", "title": "عروض علي اكسبريس بأقل من 25 ريال | هدايا ومنتجات رخيصة",
     "intro": "عروض علي إكسبريس بأقل من 25 ريال: إكسسوارات وأدوات مكياج ومطبخ وهدايا بسيطة، بتقييم عالي وتوصيل للخليج.",
     "pick": lambda xs: [x for x in xs if 0 < x["price"] < 25]},
    {"slug": "best-sellers", "name": "الأكثر مبيعًا", "title": "الأكثر مبيعًا في علي اكسبريس للبنات | أفضل المنتجات طلبًا",
     "intro": "أكثر منتجات علي إكسبريس طلبًا عند البنات: منتجات مجربة عليها آلاف الطلبات وتقييمها عالي.",
     "pick": lambda xs: sorted(xs, key=lambda x: -x["orders"])},
    {"slug": "top-discounts", "name": "أقوى الخصومات", "title": "أقوى خصومات علي اكسبريس اليوم | تخفيضات فوق 50%",
     "intro": "أقوى تخفيضات علي إكسبريس الحين: منتجات خصمها 50% وأكثر بتقييم عالي وتوصيل للسعودية والخليج.",
     "pick": lambda xs: sorted([x for x in xs if x["disc"] >= 50], key=lambda x: -x["disc"])},
]


AMAZON_LINKS = [
    ("🔥 عروض اليوم في أمازون السعودية", "https://www.amazon.sa/deals?language=ar_AE"),
    ("👗 فساتين نسائية", "https://www.amazon.sa/s?k=فساتين+نسائية&language=ar_AE"),
    ("👜 حقائب نسائية", "https://www.amazon.sa/s?k=حقائب+نسائية&language=ar_AE"),
    ("💄 أدوات المكياج", "https://www.amazon.sa/s?k=أدوات+مكياج&language=ar_AE"),
    ("🧴 العناية بالبشرة والشعر", "https://www.amazon.sa/s?k=العناية+بالبشرة&language=ar_AE"),
    ("🍳 أدوات المطبخ", "https://www.amazon.sa/s?k=أدوات+مطبخ&language=ar_AE"),
    ("🏠 ديكور وتنظيم البيت", "https://www.amazon.sa/s?k=تنظيم+المنزل&language=ar_AE"),
    ("💍 إكسسوارات ومجوهرات", "https://www.amazon.sa/s?k=اكسسوارات+نسائية&language=ar_AE"),
]


def amazon_url(cfg, url):
    tag = cfg.get("amazon_tag", "")
    return f"{url}&tag={tag}" if tag else url


def amazon_page(cfg):
    """صفحة روابط أمازون السعودية (بدون أسعار: أمازون ما تسمح بعرض السعر إلا من الـ API حقها)."""
    tg = f"https://t.me/{cfg['telegram_public'].lstrip('@')}"
    rows = "".join(f"<a class='buy' style='margin-top:10px' href='{esc(amazon_url(cfg, u))}' target='_blank' "
                   f"rel='nofollow sponsored noopener'>{esc(t)}</a>" for t, u in AMAZON_LINKS)
    body = ("<h1>عروض أمازون السعودية اليوم</h1><p class='intro'>روابط مباشرة لعروض وتخفيضات أمازون السعودية "
            "في الأقسام اللي تهم البنات: فساتين وحقائب ومكياج وأدوات مطبخ وتنظيم البيت. شحن سريع داخل المملكة، "
            "والأسعار تشوفينها محدّثة في أمازون.</p>"
            f"{rows}<p class='note'>بصفتنا مسوّقين بالعمولة مع أمازون، نربح من عمليات الشراء المؤهلة.</p>"
            f"<p><a class='join' href='{tg}'>📢 انضمي لقروب التليجرام لعروض علي إكسبريس وأمازون يوميًا</a></p>")
    return page(cfg, "عروض أمازون السعودية اليوم | تخفيضات فساتين وحقائب ومكياج ومطبخ",
                "روابط مباشرة لعروض وتخفيضات أمازون السعودية اليوم: فساتين، حقائب، مكياج، أدوات مطبخ وتنظيم البيت.",
                "amazon.html", body)


def share_html(url, text):
    """أزرار مشاركة واتساب وتليجرام (كل مشاركة تجيب زوار جدد)."""
    msg = quote(f"{text}\n{url}")
    return (f'<div class="share"><a class="wa" href="https://wa.me/?text={msg}" target="_blank" rel="noopener">📲 أرسليه واتساب</a>'
            f'<a class="tgs" href="https://t.me/share/url?url={quote(url)}&text={quote(text)}" target="_blank" rel="noopener">✈️ شاركيه تليجرام</a></div>')


def best_items(lst):
    """أفضل منتجات القسم: الأعلى تقييمًا ثم الأكثر طلبًا."""
    return sorted(lst, key=lambda x: (-x["rating"], -x["orders"]))[:10]


def best_page(cfg, c, lst):
    """صفحة «أفضل منتجات القسم» مرتبة بالأرقام، تستهدف بحث الناس عن: أفضل ... من علي اكسبريس."""
    site = cfg["site_url"].rstrip("/")
    top = best_items(lst)
    n = len(top)
    year = datetime.now(RIYADH).year
    h1 = f"أفضل {n} منتجات {c['name']} من علي إكسبريس {year}"
    intro = (f"جمعنا لك أفضل {n} منتجات في {c['name']} من علي إكسبريس، مرتبة حسب تقييم المشترين وعدد الطلبات، "
             f"مع السعر بالريال والخصم الحالي وتوصيل للسعودية والخليج. القائمة تتحدث تلقائيًا مع كل عروض جديدة.")
    rows = ""
    for i, x in enumerate(top, 1):
        name = display_name(x)
        about = esc(x["about"]) if x.get("about") else ""
        old = f" <s>{money(x['old'])}</s>" if x["old"] > x["price"] > 0 else ""
        disc = f" · خصم {x['disc']}%" if x["disc"] else ""
        rows += (f'<div class="best"><div class="bn">{i}</div>'
                 f'<a href="{site}/p/{x["id"]}.html"><img referrerpolicy="no-referrer" src="{esc(x["image"])}" alt="{esc(name)}" loading="lazy"></a>'
                 f'<div class="bt"><h2><a href="{site}/p/{x["id"]}.html">{esc(name)}</a></h2>'
                 f'<p>{about}</p>'
                 f'<p class="bm">⭐ {x["rating"]} من 5 · 🛒 أكثر من {x["orders"]:,} طلب</p>'
                 f'<p class="bp">{money(x["price"])} ريال{old}{disc}</p>'
                 f'<a class="buy" href="{esc(x["link"])}" target="_blank" rel="nofollow sponsored noopener">🛍️ اطلبي من علي إكسبريس</a></div></div>')
    body = (f'<div class="crumbs"><a href="{site}/">الرئيسية</a> › <a href="{site}/c/{c["slug"]}.html">{esc(c["name"])}</a></div>'
            f"<h1>{esc(h1)}</h1><p class='intro'>{esc(intro)}</p>{rows}"
            f'<p><a class="join" href="{site}/c/{c["slug"]}.html">شوفي كل عروض {esc(c["name"])} ←</a></p>')
    ld = [{"@context": "https://schema.org", "@type": "ItemList", "name": h1,
           "itemListElement": [{"@type": "ListItem", "position": i, "url": f"{site}/p/{x['id']}.html",
                                "name": display_name(x)} for i, x in enumerate(top, 1)]}]
    return page(cfg, f"{h1} | بتقييم عالي وطلبات كثيرة", intro[:300], f"best/{c['slug']}.html", body, ld,
                top[0]["image"] if top else None)


def list_page(cfg, title, h1, intro, path, items, extra=""):
    body = f"<h1>{esc(h1)}</h1><p class='intro'>{esc(intro)}</p>{extra}<div class='grid'>" + \
           "".join(card(cfg, x) for x in items) + "</div>"
    return page(cfg, title, intro[:300], path, body, image=items[0]["image"] if items else None)


# ---------------------------------------------------------------- البناء
def build(cfg, only_ids=None):
    """يبني الموقع. only_ids: نبني صفحات هالمنتجات بس (والرئيسية والفئات والخريطة دايمًا)."""
    items = load_products()
    site = cfg["site_url"].rstrip("/")
    (OUT / "p").mkdir(parents=True, exist_ok=True)
    (OUT / "c").mkdir(parents=True, exist_ok=True)
    (OUT / "style.css").write_text(CSS, encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    newest = list(reversed(items))
    by_cat = {}
    for x in newest:
        by_cat.setdefault(x["cat"], []).append(x)

    ver_file = OUT / ".version"
    if not ver_file.exists() or ver_file.read_text(encoding="utf-8").strip() != SITE_VERSION:
        only_ids = None  # تصميم جديد: نعيد بناء كل الصفحات
        ver_file.write_text(SITE_VERSION, encoding="utf-8")
    targets = items if only_ids is None else [x for x in items if x["id"] in set(only_ids)]
    for x in targets:
        related = [r for r in by_cat.get(x["cat"], []) if r["id"] != x["id"]][:8]
        (OUT / "p" / f"{x['id']}.html").write_text(product_page(cfg, x, related), encoding="utf-8")

    (OUT / "best").mkdir(parents=True, exist_ok=True)
    bests = [c for c in CATEGORIES if len(by_cat.get(c["slug"], [])) >= 5]
    for c in bests:
        (OUT / "best" / f"{c['slug']}.html").write_text(best_page(cfg, c, by_cat[c["slug"]]), encoding="utf-8")
    best_links = ("<div class='bests'>" + "".join(
        f'<a href="{site}/best/{c["slug"]}.html">🏆 أفضل {esc(c["name"])}</a>' for c in bests) + "</div>") if bests else ""

    for c in CATEGORIES + [OTHER]:
        lst = by_cat.get(c["slug"], [])[:60]
        intro = (f"أفضل عروض {c['name']} من علي إكسبريس بأسعار مخفضة، مختارة بتقييم عالي وآلاف الطلبات، "
                 f"مع توصيل للسعودية والإمارات والكويت وقطر والبحرين وعمان. نحدّثها يوميًا.")
        (OUT / "c" / f"{c['slug']}.html").write_text(
            list_page(cfg, f"{c['name']} علي اكسبريس | عروض وتخفيضات اليوم", f"{c['name']} من علي إكسبريس",
                      intro, f"c/{c['slug']}.html", lst,
                      f"<div class='bests'><a href='{site}/best/{c['slug']}.html'>🏆 أفضل {len(best_items(lst))} منتجات في {esc(c['name'])}</a></div>"
                      if c in bests else ""), encoding="utf-8")

    for c in COLLECTIONS:
        lst = c["pick"](newest)[:60]
        (OUT / "c" / f"{c['slug']}.html").write_text(
            list_page(cfg, c["title"], c["name"] + " من علي إكسبريس", c["intro"], f"c/{c['slug']}.html", lst),
            encoding="utf-8")

    intro = ("عروض وتخفيضات علي إكسبريس يوميًا للبنات: فساتين سهرة وعبايات، أدوات مكياج، إكسسوارات وشنط، "
             "وأدوات مطبخ. كل منتج مختار بتقييم عالي وطلبات كثيرة، والسعر بالريال مع الخصم، "
             "وتوصيل للسعودية والخليج.")
    (OUT / "index.html").write_text(
        list_page(cfg, f"{cfg['site_name']} | عروض وتخفيضات علي اكسبريس اليوم بالريال",
                  "عروض علي اكسبريس اليوم", intro, "", newest[:60], best_links), encoding="utf-8")

    (OUT / "codes.html").write_text(codes_page(cfg), encoding="utf-8")
    if cfg.get("amazon_tag"):
        (OUT / "amazon.html").write_text(amazon_page(cfg), encoding="utf-8")

    urls = [(f"{site}/", today_str()), (f"{site}/codes.html", today_str())] + \
           ([(f"{site}/amazon.html", today_str())] if cfg.get("amazon_tag") else []) + \
           [(f"{site}/c/{c['slug']}.html", today_str()) for c in COLLECTIONS if c["pick"](newest)] + \
           [(f"{site}/best/{c['slug']}.html", today_str()) for c in bests] + \
           [(f"{site}/c/{c['slug']}.html", today_str()) for c in CATEGORIES if by_cat.get(c["slug"])] + \
           [(f"{site}/p/{x['id']}.html", x["date"]) for x in newest]
    sm = "".join(f"<url><loc>{u}</loc><lastmod>{d}</lastmod></url>" for u, d in urls)
    (OUT / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sm}</urlset>',
        encoding="utf-8")
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {site}/sitemap.xml\n", encoding="utf-8")
    return len(targets)


def today_str():
    return datetime.now(RIYADH).strftime("%Y-%m-%d")
