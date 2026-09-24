"""Google Drive için bir kerelik yetkilendirme + müşteriyle paylaşılacak klasörü oluşturma.

Kullanım (kendi bilgisayarında, bir kez):
  1) Google Cloud Console → APIs & Services → Drive API'yi etkinleştir → OAuth istemci kimliği (Desktop) →
     client_secret.json olarak bu klasöre koy.
  2) python tools/drive_token.py
  3) Çıktıdaki DRIVE_TOKEN_JSON değerini GitHub → Settings → Secrets → DRIVE_TOKEN_JSON'a,
     DRIVE_FOLDER_ID değerini Variables → DRIVE_FOLDER_ID'ye yapıştır.
  4) Drive'da o klasörü müşterinin e-postasıyla paylaş (Görüntüleyici yeterli).
"""
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0)
svc = build("drive", "v3", credentials=creds)
folder = svc.files().create(body={"name": "Bağlum Emlak Videoları",
                                  "mimeType": "application/vnd.google-apps.folder"}, fields="id").execute()
print("\nDRIVE_FOLDER_ID =", folder["id"])
print("\nDRIVE_TOKEN_JSON =\n" + creds.to_json())
