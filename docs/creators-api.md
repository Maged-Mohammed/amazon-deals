# Creators API (الـ API الرسمي من أمازون)

البوت دلوقتي بيجيب المنتجات من صفحات أمازون مصر نفسها (السكرابر). **Creators API** هو الـ API الرسمي من برنامج Amazon Associates، وهو اللي أمازون بتقول إنه بديل **PA-API 5** (اللي بقت deprecated). وبيدعم **www.amazon.eg**.

الكود جاهز ومتجرّب، بس **مقفول** لحد ما تبقى عندك الكريدنشيالز. الملف ده بيشرح تقدّم إزاي وتفعّله إزاي.

> كل المعلومات هنا من الدوكس الرسمية: https://affiliate-program.amazon.com/creatorsapi/docs/en-us/introduction

## 1. الشروط قبل ما تقدّم

- لازم يبقى عندك حساب **Amazon Associates** متراجع ومتقبل نهائيًا في البرنامج.
- لازم يكون عندك **10 مبيعات مؤهلة (qualifying sales) على الأقل في آخر 30 يوم**. ده الشرط الأساسي، والـ API بيرد بـ `AssociateNotEligible` لو مش متحقق.
- **صاحب الحساب الأساسي (primary account owner) بس** هو اللي يقدر يسجّل في Creators API. (تعرف إنك الـ primary owner لو بتقدر تفتح صفحات تحديث طريقة الدفع والضرايب للمتجر.)
- لو حسابك فضل **30 يوم متتالي من غير مبيعات مؤهلة** بيتقفل منك الوصول للـ API، وبيرجع خلال يومين بعد ما مبيعات جديدة تتشحن.

## 2. التسجيل وإنشاء الكريدنشيالز

1. ادخل على **Associates Central** (متجرك على amazon.eg).
2. من **Tools** اختار **Creators API**.
3. دوس **Create Application** واكتب اسم للتطبيق (مثلًا `loata-bot`).
4. ضيف **credentials** جديدة، وانسخ القيم أو نزّلها CSV:
   - **Credential ID**
   - **Secret**
   - **Version** (لمصر بيبقى من منطقة EU، يعني **3.2**؛ البوت بيستخدم بوابة التوكن بتاعة EU لوحده)
5. هتحتاج كمان **الـ tracking ID / store ID** بتاع متجرك على **amazon.eg** (زي `yourtag-21`)، ده الـ **partnerTag**. لازم يبقى بتاع amazon.eg بالظبط.

حدود من الدوكس: لحد تطبيقين لكل متجر، ولحد مجموعتين credentials لكل تطبيق.

## 3. حط الأسرار في GitHub (مش في الكود)

الريبو عام، فالأسرار **في GitHub Secrets بس**. من فولدر الريبو على جهازك:

```bash
gh secret set AMAZON_CREATORS_CREDENTIAL_ID
gh secret set AMAZON_CREATORS_CREDENTIAL_SECRET
gh secret set AMAZON_TAG
```

كل أمر هيسألك على القيمة (الزق ودوس Enter). **متبعتش القيم دي في أي شات.**

> ملحوظة: `AMAZON_TAG` بيشغّل كمان سطر الإفصاح بتاع الأفلييت في رسايل اللقطات ("ℹ️ لينك أفلييت..."). ده مطلوب مع Creators API لأن كل لينكات المنتجات بتيجي من الـ API ومعاها التاج بتاعك.

## 4. التحويل من السكرابر لـ Creators API

1. بعد ما تحط الأسرار، **استنى أول تشغيلة** (أو دوس ▶️ شغّل دلوقتي). البوت بيكتب في الحالة إن الكريدنشيالز موجودة.
2. في تليجرام: `/menu` ← **⚙️ الإعدادات** ← **🔌 Creators API**.
   - لو الأسرار لسه مش موجودة، الزرار **بيرفض التحويل** وبيقولك ناقص إيه.
3. ترجع للسكرابر في أي وقت من نفس الشاشة: **🕷️ السكرابر**.

القيمة بتتخزن في `config.json` كـ `"source": "scraper"` أو `"source": "creators_api"` (الافتراضي `scraper`).

## 5. لو حاجة باظت

- لو المصدر `creators_api` والكريدنشيالز **ناقصة** أو أمازون **رفضها** (401/403): البوت بيبعتلك تنبيه **مرة واحدة في اليوم** وبيوقف البحث. **مش بيرجع للسكرابر لوحده أبدًا**. ترجّعه انت من ⚙️ لو عايز.
- **حدود الطلبات** (من صفحة API Rates): في الأول **طلب واحد في الثانية (1 TPS)** و**8640 طلب في اليوم (8640 TPD)**، وبتزيد مع المبيعات اللي جاية من الـ API. البوت بيحترمها (token bucket للثانية + عداد لليوم بتوقيت القاهرة)، ولو أمازون رد بـ 429 بيستنى ويعيد (exponential backoff).
- تشغيلة عادية بتستخدم حوالي 31 طلب (30 بحث + المتابعة والبلاغات في طلب واحد بيجمع لحد 10 منتجات)، يعني بعيد جدًا عن حد اليوم.

## 6. الفرق عن السكرابر (حاجات الـ API مبيديهاش)

- **مفيش تقييمات ولا عدد تقييمات** في أي resource. فالبوت مبيرفضش اللقطة عشان التقييم مش معروف، وبيدي جزء التقييم في السكور نص النقط (لا مكافأة ولا عقاب). ولأن النشر التلقائي للبلاغات شرطه تقييم 4+، **كل البلاغات هتجيلك مراجعة** وانت على الـ API.
- **مفيش "Fulfilled by Amazon"** (`IsAmazonFulfilled` مش متاح). البايع بيبان "أمازون نفسه" بس لو اسم التاجر أمازون، غير كده "مش معروف".
- **مفيش كوبونات** في الـ API.
- **اللينكات**: لازم تتبعت زي ما الـ API رجّعها (`detailPageURL`) من غير ما نغيّر أي parameter، عشان المبيعات تتحسبلك. البوت بيعمل كده.

## الصفحات اللي الكود اتبنى عليها

- introduction، onboarding/register-for-creators-api، migrating-to-creatorsapi-from-paapi
- get-started/using-curl (Regional Endpoints والتوكن)
- concepts/common-request-headers-and-parameters، concepts/api-rates، concepts/best-programming-practices
- api-reference/operations/search-items، api-reference/operations/get-items، api-reference/resources/offersV2
- locale-reference، troubleshooting/error-codes-and-messages، troubleshooting/processing-of-errors
- frequently-asked-questions، paapiv5-deprecation
