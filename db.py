import json
import os
from datetime import datetime


class Database:
    def __init__(self, path: str = "data.json"):
        self.path = path
        self._data = {"users": {}, "used_codes": []}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception:
                pass

    def _save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def get_user(self, tg_id: str) -> dict | None:
        return self._data["users"].get(str(tg_id))

    def mark_verified(self, tg_id: str, code: str, tg_name: str):
        self._data["users"][str(tg_id)] = {
            "verified": True,
            "code": code,
            "tg_name": tg_name,
            "mc_name": None,
            "verified_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        self._save()

    def is_code_used(self, code: str) -> bool:
        return code.upper() in self._data.get("used_codes", [])

    def mark_code_used(self, code: str):
        codes = self._data.setdefault("used_codes", [])
        if code.upper() not in codes:
            codes.append(code.upper())
        self._save()

    def get_stats(self) -> dict:
        total = len(self._data["users"])
        verified = sum(1 for u in self._data["users"].values() if u.get("verified"))
        return {"total": total, "verified": verified}
