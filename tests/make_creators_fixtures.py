"""
ردود Creators API صناعية للاختبارات (tests/fixtures/creators_*.json).

الشكل (أسماء المفاتيح والتداخل) منقول بالظبط من أمثلة الدوكس الرسمية:
  - get-started/using-curl: رد التوكن {access_token, scope, token_type, expires_in}
  - api-reference/operations/search-items: searchResult.items[] (asin, detailPageURL, images.primary.*,
    itemInfo.title, offersV2.listings[] بـ availability/condition/isBuyBoxWinner/merchantInfo/price{money,
    savingBasis, savings}), searchURL, totalResultCount
  - api-reference/operations/get-items: errors[] (code, message) + itemResults.items[]
  - troubleshooting/processing-of-errors: {"__type": "com.amazon.creators#...", "message": ...}
القيم نفسها (المنتجات، الأسعار، الـ ASINs، الصور) مألّفة.

التشغيل: python tests/make_creators_fixtures.py
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "fixtures"


def listing(amount, basis=None, merchant="Amazon.eg", avail="IN_STOCK", buybox=True):
    price = {"money": {"amount": amount, "currency": "EGP", "displayAmount": f"EGP {amount:,.2f}"}}
    if basis:
        price["savingBasis"] = {"money": {"amount": basis, "currency": "EGP", "displayAmount": f"EGP {basis:,.2f}"},
                                "savingBasisType": "LIST_PRICE", "savingBasisTypeLabel": "List Price"}
        price["savings"] = {"money": {"amount": round(basis - amount, 2), "currency": "EGP",
                                      "displayAmount": f"EGP {basis - amount:,.2f}"},
                            "percentage": round((basis - amount) / basis * 100)}
    return {"availability": {"maxOrderQuantity": 10, "minOrderQuantity": 1, "type": avail},
            "condition": {"conditionNote": "", "subCondition": "Unknown", "value": "New"},
            "isBuyBoxWinner": buybox, "merchantInfo": {"name": merchant}, "price": price, "violatesMAP": False}


def item(asin, title, listings):
    return {"asin": asin,
            "detailPageURL": f"https://www.amazon.eg/dp/{asin}?tag=testtag-21&linkCode=osi&th=1&psc=1",
            "images": {"primary": {"large": {"height": 500, "url": f"https://img.example.test/fake/{asin}._SL500_.jpg",
                                             "width": 500}}},
            "itemInfo": {"title": {"displayValue": title, "label": "Title", "locale": "en_AE"}},
            "offersV2": {"listings": listings}}


FILES = {
    "creators_token.json": {"access_token": "Atc|TEST-ACCESS-TOKEN", "scope": "creatorsapi::default",
                            "token_type": "bearer", "expires_in": 3600},
    "creators_search.json": {"searchResult": {
        "items": [
            item("B0API00001", "Nebula Wireless Headphones X1 Black", [listing(1499.0, 3999.0)]),
            # بايع خارجي، وليه listing تاني مش buybox
            item("B0API00002", "Quanta Air Fryer 6L Digital",
                 [listing(1900.0, 4200.0, merchant="Fake Gadgets Store", buybox=False),
                  listing(1800.0, 4000.0, merchant="Fake Gadgets Store", buybox=True)]),
            item("B0API00003", "Lumo Desk Lamp LED", [listing(900.0)]),  # مفيش savingBasis = مفيش خصم
            item("B0API00004", "Orion Smart Lamp Out Of Stock", [listing(700.0, 1600.0, avail="OUT_OF_STOCK")]),
        ],
        "searchURL": "https://www.amazon.eg/s/?field-keywords=headphones&search-alias=aps&tag=testtag-21&linkCode=osi",
        "totalResultCount": 4}},
    "creators_getitems.json": {
        "errors": [{"code": "ItemNotAccessible",
                    "message": "The ItemId B0APINOPE1 is not accessible through the Creators API."}],
        "itemResults": {"items": [
            item("B0WATCH001", "Kivo Gaming Mouse RGB 7200", [listing(450.0, 1250.0)]),
            item("B0WATCH002", "Terra Power Bank 20000mAh", [listing(600.0, 1500.0, avail="OUT_OF_STOCK")]),
        ]}},
    # troubleshooting/processing-of-errors (مثال مفيش نتايج، بنفس النص)
    "creators_error_404.json": {"__type": "com.amazon.creators#ResourceNotFoundException",
                                "message": "No results found for your search request",
                                "resourceType": "SearchResult", "resourceId": "Mystery Novels Non Existing"},
    # troubleshooting/error-codes-and-messages: UnauthorizedException (InvalidToken)
    "creators_error_401.json": {"__type": "com.amazon.creators#UnauthorizedException",
                                "message": "The authentication token is invalid or malformed."},
    "creators_error_401_expired.json": {"__type": "com.amazon.creators#UnauthorizedException",
                                        "message": "Authentication token has expired."},
    "creators_error_429.json": {"__type": "com.amazon.creators#ThrottleException",
                                "message": "Request was denied due to request throttling."},
}

if __name__ == "__main__":
    for name, body in FILES.items():
        (OUT / name).write_text(json.dumps(body, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        print(name)
