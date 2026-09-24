"""Üretilen videoyu Google Drive'daki (müşteriyle paylaşılan) klasöre yükler. Ayar yoksa sessizce atlanır."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .. import config

log = logging.getLogger("emlak.drive")
SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_info(json.loads(config.DRIVE_TOKEN_JSON), SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def upload(files: list[Path]) -> list[str]:
    if not (config.DRIVE_TOKEN_JSON and config.DRIVE_FOLDER_ID):
        log.info("Drive ayarı yok → yükleme atlandı (video Actions Artifacts'ta)")
        return []
    from googleapiclient.http import MediaFileUpload
    svc, out = _service(), []
    for f in files:
        mime = "video/mp4" if f.suffix == ".mp4" else "text/plain"
        meta = {"name": f.name, "parents": [config.DRIVE_FOLDER_ID]}
        res = svc.files().create(body=meta, media_body=MediaFileUpload(str(f), mimetype=mime, resumable=True),
                                 fields="id,webViewLink").execute()
        log.info("Drive'a yüklendi: %s → %s", f.name, res.get("webViewLink"))
        out.append(res.get("webViewLink", ""))
    return out
