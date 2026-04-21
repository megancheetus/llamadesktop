"""
Profile and history persistence.

Data is stored in:  <project_root>/data/profiles.json
"""
import json
import os
from typing import Dict

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_HERE, "data")
PROFILES_FILE = os.path.join(DATA_DIR, "profiles.json")

_DEFAULTS: Dict = {
    "geral": {
        "name": "Conversa Geral",
        "system_prompt": "Você é um assistente útil, claro e conciso.",
        "history": [],
    },
    "residencia": {
        "name": "Residência Médica",
        "system_prompt": (
            "Você é um tutor especializado em residência médica brasileira. "
            "Responda questões de múltipla escolha explicando o raciocínio clínico, "
            "fisiopatologia e detalhes que caem em provas. Seja preciso e didático."
        ),
        "history": [],
    },
}


def load_profiles() -> Dict:
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(PROFILES_FILE):
        try:
            with open(PROFILES_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if data:
                # Ensure every profile has a history list
                for v in data.values():
                    v.setdefault("history", [])
                return data
        except Exception:
            pass
    # First run — write defaults
    data = {k: dict(v) for k, v in _DEFAULTS.items()}
    save_profiles(data)
    return data


def save_profiles(profiles: Dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    # Strip empty assistant placeholders that may have been left by an interrupted generation
    clean = {}
    for key, profile in profiles.items():
        history = [
            msg for msg in profile.get("history", [])
            if not (msg.get("role") == "assistant" and not msg.get("content", "").strip())
        ]
        clean[key] = {**profile, "history": history}
    with open(PROFILES_FILE, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
