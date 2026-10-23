"""
بيولّد صفحات HTML صناعية للاختبارات في tests/fixtures/.

الصفحات دي بتقلّد شكل HTML بتاع أمازون وكانبكام بس (نفس التاجات والكلاسات والـ attributes
اللي السكربت بيقرا منها)، وكل المحتوى مألّف: عناوين وماركات وأسعار وASINs وصور وهمية.
مفيش أي نص أو صورة أو سكربت منقول من أمازون أو كانبكام.

التشغيل (لو غيّرت حاجة هنا): python tests/make_fixtures.py
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent / "fixtures"
IMG = "https://img.example.test/fake/{asin}._AC_UL320_.jpg"  # دومين .test محجوز ومبيفتحش


def price_block(value, old=None):
    """سعر أمازون: span.a-price (الحالي) و span.a-price.a-text-price (القديم)"""
    html = (f'<span class="a-price" data-a-size="xl"><span class="a-offscreen">EGP {value}</span>'
            f'<span aria-hidden="true">{value}</span></span>')
    if old:
        html += (f' <span class="a-price a-text-price" data-a-strike="true"><span class="a-offscreen">EGP {old}</span>'
                 f'<span aria-hidden="true">{old}</span></span>')
    return html


def card(asin, heads, price=None, old=None, rating=None, reviews=None, extra=""):
    h2s = "".join(f'<a class="a-link-normal" href="/dp/{asin}"><h2 class="a-size-base-plus"><span>{h}</span></h2></a>'
                  for h in heads)
    rating_html = ""
    if rating:
        rating_html = (f'<span class="a-icon-alt">{rating} out of 5 stars</span>'
                       f'<a aria-label="{reviews} ratings" href="/dp/{asin}#customerReviews"><span>({reviews})</span></a>')
    return f"""
<div data-asin="{asin}" data-component-type="s-search-result" class="s-result-item s-asin">
  <div class="s-product-image-container"><img class="s-image" src="{IMG.format(asin=asin)}" alt=""></div>
  <div data-cy="title-recipe">{h2s}</div>
  <div class="a-row">{rating_html}</div>
  <div data-cy="price-recipe">{price_block(price, old) if price else '<span class="a-color-secondary">Currently unavailable</span>'}</div>
  {extra}
</div>"""


COUPON_YOU_PAY = ('<span class="s-coupon-clipped aok-hidden"><span class="a-color-base">EGP 150.00 off coupon applied</span></span>'
                  '<span class="s-coupon-unclipped"><span class="s-coupon-highlight-color">You pay EGP 850.00</span>'
                  ' with coupon</span>')
COUPON_PERCENT = '<span class="s-coupon-unclipped"><span class="s-coupon-highlight-color">Save 55%</span> with coupon</span>'
BANK_PROMO = '<div class="a-row"><span>20% off Fake Bank Cards</span></div>'

SEARCH_CARDS = [
    # (asin, عناوين h2, السعر, القديم, التقييم, عدد التقييمات, إضافات)
    ("B0TEST0001", ["Nebula Wireless Headphones X1 Black"], "1,499.00", "3,999.00", 4.6, "2,345", ""),
    ("B0TEST0002", ["Quanta Air Fryer 6L Digital"], "1,800.00", "4,000.00", 4.2, "310", ""),
    ("B0TEST0003", ["Lumo Desk Lamp LED"], "900.00", "1,000.00", 4.4, "87", ""),  # خصم 10% بس
    ("B0TEST0004", ["Corvi Electric Kettle 1.7L Steel"], "1,000.00", "1,900.00", 4.1, "1,020", COUPON_YOU_PAY),
    ("B0TEST0005", ["Pixa Smart Watch S5"], "2,000.00", None, 4.0, "56", COUPON_PERCENT),  # خصم من الكوبون بس
    ("B0TEST0006", ["Orbita", "mens Runner 2.0 Sneaker"], "1,200.00", "2,600.00", 4.3, "640", ""),  # ماركة + اسم
    ("B0TEST0007", ["Velora", "Velora Women Classic Leather Handbag Brown"], "950.00", "2,100.00", 4.5, "212", ""),
    ("B0TEST0008", ["Ghost Product Without Price"], None, None, 4.9, "10", ""),  # مفيش سعر
    ("B0TEST0009", ["Terra Power Bank 20000mAh"], "600.00", "1,500.00", 4.7, "4,410", BANK_PROMO),
    ("B0TEST0010", ["Mira Bluetooth Speaker Mini"], "700.00", "1,600.00", 4.3, "998", ""),
    ("B0TEST0011", ["Sola Hair Dryer 2200W"], "650.00", "1,450.00", 4.0, "143", ""),
    ("B0TEST0012", ["Kivo Gaming Mouse RGB 7200"], "500.00", "1,250.00", 4.4, "1,777", ""),
    ("B0TEST0013", ["Zentro"], "1,100.00", "2,500.00", 4.6, "301", ""),  # عنوان كلمة واحدة
    ("B0TEST0014", ["Arko Backpack 30L Waterproof"], "٥٥٠٫٠٠", "١٬٣٠٠٫٠٠", 4.2, "77", ""),  # أرقام عربي
    ("B0TEST0015", ["Lowrated Blender Basic"], "400.00", "1,000.00", 3.1, "12", ""),  # تقييم قليل
]


def search_page():
    cards = "".join(card(*c) for c in SEARCH_CARDS)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Synthetic search results</title></head>
<body><div class="s-main-slot s-result-list">{cards}
</div></body></html>
"""


def product_page(merchant, fulfiller, availability, promo="", price=None, title="Synthetic Product", asin="B0TEST0001"):
    """صفحة منتج: الـ divs اللي fetch_details بيقرا منها بس"""
    ful = f'<div id="fulfillerInfoFeature_feature_div"><span>{fulfiller}</span></div>' if fulfiller else ""
    core = (f'<div id="corePrice_feature_div"><span class="a-price"><span class="a-offscreen">EGP{price}</span>'
            f'<span aria-hidden="true">{price}</span></span></div>') if price else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Synthetic product page</title></head>
<body>
<span id="productTitle">{title}</span>
<img id="landingImage" src="{IMG.format(asin=asin).replace('_AC_UL320_', '_AC_SL1200_')}" alt="">
{core}
<div id="availability"><span class="a-size-medium">{availability}</span></div>
<div id="merchantInfoFeature_feature_div"><span>{merchant}</span></div>
{ful}
<div id="promoPriceBlockMessage_feature_div"><span>{promo}</span></div>
</body></html>
"""


def kanbkam_row(store, slug_asin, title, price):
    return f"""
<tr>
  <td class="item-img"><a data-gtmid="product-details|{store}|eg|en" href="https://www.kanbkam.com/eg/en/{slug_asin}"><img alt="" src="https://img.example.test/fake/kb.png"></a></td>
  <td class="item-name"><h3><a data-gtmid="product-details|{store}|eg|en" href="https://www.kanbkam.com/eg/en/{slug_asin}">{title}</a></h3></td>
  <td class="item-price"><p>{price} <span>EGP</span></p></td>
  <td><a class="buy-btn" data-gtmid="product-buy|{store}|eg|en" href="https://shop.example.test/{slug_asin}">Buy Now</a></td>
</tr>"""


def kanbkam_page():
    rows = (kanbkam_row("amazon", "quanta-air-fryer-B0TEST0002", "Quanta Air Fryer 6L Digital", "1,850")
            + kanbkam_row("noon", "nebula-x1-N0FAKE01", "Nebula Wireless Headphones X1 - Black", "1,300")
            + kanbkam_row("jumia", "other-headset-J0FAKE02", "Different Brand Headset Z9 Pro", "900"))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<link rel="canonical" href="https://www.kanbkam.com/eg/en/nebula-wireless-headphones-x1-black-B0TEST0001" />
<title>Synthetic price history</title></head>
<body>
<div class="price"><span class="current-price">Current Price</span><p><span id="priceNumber">1,499</span></p></div>
<ul class="list-inline price-summary">
  <li><div><span class="price-head">Lowest Price</span> <span id="min" class="green value">1,450</span></div></li>
  <li><div><span class="price-head">Highest Price</span> <span id="max" class="red value">4,200</span></div></li>
</ul>
<section class="similar-product row"><article class="col-md-12"><header><h2>Similar Products</h2></header>
<div class="table-responsive"><table class="table"><tbody>{rows}
</tbody></table></div></article></section>
</body></html>
"""


KANBKAM_HOME = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<link rel="canonical" href="https://www.kanbkam.com/eg/en/home" />
<title>Synthetic home</title></head><body><p>home</p></body></html>
"""

# صفحة تحقق: refresh فيه bm-verify (التوكن مألّف) زي اللي بيطلع لما أمازون يحجب
CHALLENGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="5; URL='/s?k=test&amp;bm-verify=FAKE-TOKEN-0000'" />
<title>&nbsp;</title></head><body></body></html>
"""

FILES = {
    "search.html": search_page(),
    "product_amazon.html": product_page("Shipper / Seller Amazon.eg", "", "In Stock",
                                        "Coupon: EGP 50.00 off coupon applied Terms Apply",
                                        "1,250.00", "Nebula Wireless Headphones X1 Black", "B0TEST0001"),
    "product_fba.html": product_page("Sold by Fake Gadgets Store", "Fulfilled by Amazon", "In Stock",
                                     "20% off Fake Bank Cards. Enter code FAKE500 at checkout.",
                                     "2,750.00", "Quanta Air Fryer 6L Digital", "B0TEST0002"),
    "product_third.html": product_page("Sold by Some Other Seller", "", "Currently unavailable.",
                                       title="Orion Smart Lamp Out Of Stock", asin="B0TEST0099"),
    "kanbkam_product.html": kanbkam_page(),
    "kanbkam_home.html": KANBKAM_HOME,
    "challenge.html": CHALLENGE,
}

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for name, body in FILES.items():
        (OUT / name).write_text(body, encoding="utf-8", newline="\n")  # LF حتى على ويندوز
        print(name, len(body))
