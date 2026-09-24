# Fon müziği (isteğe bağlı)

Bu klasöre **telifsiz** müzik dosyaları (`.mp3`, `.wav`, `.m4a`, `.ogg`) koyarsan bot her videoda
rastgele birini seçer, konuşmanın altında otomatik kısar (ducking) ve sona doğru azaltır.

- Önerilen kaynak: **YouTube Studio → Ses Kitaplığı** (Audio Library). "Atıf gerekmez" filtresiyle
  sinematik / gerilimli / ilham verici parçalar seç.
- Atıf gerektiren bir parça kullanırsan, aynı ada sahip bir `.txt` dosyası ekle
  (ör. `epic_track.mp3` + `epic_track.txt`). İçindeki metin video açıklamasına otomatik eklenir.
- Klasör boşsa bot, videonun ruh haline (epic / mysterious / uplifting / emotional / energetic)
  uygun **özgün bir fon müziğini kendisi sentezler** — telif sorunu yoktur.
- Müziği tamamen kapatmak için: `MUSIC_MODE=off`.
