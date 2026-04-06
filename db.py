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
            "tg_id": str(tg_id),
            "mc_name": None,
            "verified_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        self._save()

    def set_mc_name(self, tg_id: str, mc_name: str):
        """Сохраняет MC-ник и историю ников пользователя после верификации."""
        uid = str(tg_id)
        if uid in self._data["users"]:
            user = self._data["users"][uid]
            user["mc_name"] = mc_name
            # История MC-ников — для обнаружения смены ника после бана
            hist = user.setdefault("mc_names_history", [])
            if mc_name and mc_name not in hist:
                hist.append(mc_name)
            self._save()

    def is_code_used(self, code: str) -> bool:
        return code.upper() in self._data.get("used_codes", [])

    def mark_code_used(self, code: str):
        codes = self._data.setdefault("used_codes", [])
        if code.upper() not in codes:
            codes.append(code.upper())
        self._save()

    def get_stats(self) -> dict:
        total    = len(self._data["users"])
        verified = sum(1 for u in self._data["users"].values() if u.get("verified"))
        return {"total": total, "verified": verified}

    def add_ban_log(self, target: str, reason: str, by_tg_id: str):
        bans = self._data.setdefault("ban_log", [])
        bans.insert(0, {
            "target": target,
            "reason": reason,
            "by":     by_tg_id,
            "at":     datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        if len(bans) > 500:
            bans[:] = bans[:500]
        self._save()

    def get_ban_log(self) -> list:
        return self._data.get("ban_log", [])

    # ─── Поиск по Telegram ───────────────────────────────────────────────────

    def find_by_tg_id(self, tg_id: str) -> dict | None:
        """Ищет верифицированного пользователя по TG ID."""
        return self._data["users"].get(str(tg_id))

    def find_by_tg_name(self, tg_name: str) -> dict | None:
        """
        Ищет по TG-нику (без @, регистронезависимо).
        tg_name хранится как '@username' или 'Имя'.
        """
        needle = tg_name.lstrip("@").lower()
        for user in self._data["users"].values():
            stored = user.get("tg_name", "").lstrip("@").lower()
            if stored == needle:
                return user
        return None

    def find_by_mc_name(self, mc_name: str) -> dict | None:
        """Ищет по MC-нику (регистронезависимо)."""
        needle = mc_name.lower()
        for user in self._data["users"].values():
            if (user.get("mc_name") or "").lower() == needle:
                return user
        return None

    def find_by_mc_name_any(self, mc_name: str) -> dict | None:
        """Ищет по текущему и историческим MC-никам (регистронезависимо)."""
        needle = mc_name.lower()
        for user in self._data["users"].values():
            if (user.get("mc_name") or "").lower() == needle:
                return user
            for hist_name in user.get("mc_names_history", []):
                if hist_name.lower() == needle:
                    return user
        return None

    def get_all_mc_names_for_tg(self, tg_id: str) -> list[str]:
        """Все MC-ники (текущий + история) для данного TG ID."""
        user = self.find_by_tg_id(tg_id)
        if not user:
            return []
        names = list(user.get("mc_names_history", []))
        cur = user.get("mc_name")
        if cur and cur not in names:
            names.append(cur)
        return names

    def get_all_verified(self) -> list[dict]:
        """Список всех верифицированных пользователей."""
        return [u for u in self._data["users"].values() if u.get("verified")]
