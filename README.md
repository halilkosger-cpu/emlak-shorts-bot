# Emlak Shorts botu (Bağlum)

Günde 1-2 kez (10:00 ve 17:00 TSİ) otomatik dikey video (Reels/Shorts) üretir. **Yayınlamaz**: mp4 + paylaşım metni
(açıklama, hashtag, yasal not) üretir, Google Drive'daki müşteri klasörüne koyar; müşteri kendi hesaplarından paylaşır.

Türler: `piyasa` (güncel haber başlıkları), `bolge` (data/baglum_bilgi.md), `ilan` (medya/ilanlar/<ad>/).
Slot 2 mümkünse ilan; yoksa piyasa/bölge dönüşümlü. Medya en az kullanılandan seçilir.

## Kurulum
1. Bu klasörü **ayrı bir GitHub reposu** yap (özel/private): `git init`, `git add .`, commit, push.
2. Settings → Secrets → `GEMINI_API_KEY` (mevcut anahtarın olabilir).
3. Settings → Variables: `BRAND_NAME` (örn. "Ahmet Emlak"), `BRAND_PHONE`, isteğe bağlı `BADGE`, `REGION`, `ACCENT_COLOR`.
4. (İsteğe bağlı) Drive teslimi: `python tools/drive_token.py` → çıkan `DRIVE_TOKEN_JSON` secret, `DRIVE_FOLDER_ID` variable.
   Klasörü müşterinin e-postasıyla paylaş. Drive ayarı yoksa videolar Actions → run → Artifacts'ta (14 gün) durur.
5. `data/baglum_bilgi.md` dosyasını müşteriyle doldur. `medya/genel/` içine fotoğraf/video koy.
6. Actions → "Emlak videosu" → Run workflow (dry_run=true) ile dene.

## Günlük iş akışı (senin tarafında)
- Müşteri WhatsApp'tan foto/video yollar → `medya/genel/` veya `medya/ilanlar/<ilan>/` (+ bilgi.txt) → commit/push.
- Bot her gün videoyu üretir, Drive'a koyar. Müşteri indirip Instagram/Facebook/YouTube'da paylaşır.

## Yasal notlar
Getiri/kâr vaadi yok (bot bu ifadeleri engeller), her açıklamada "yatırım tavsiyesi değildir" notu var. Emlak reklamlarında
yetki belgesi numarası gibi zorunlu bilgileri müşteri paylaşırken kendisi eklemelidir. Kişi görüntüsü içeren medyada KVKK izni gerekir.
