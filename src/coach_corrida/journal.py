from __future__ import annotations

import json
import math
import uuid
from datetime import date, timedelta

from .storage import Store, encode, timestamp


class Journal:
    """Duas etapas: rascunho, apresentação ao usuário, confirmação explícita.

    O cliente MCP deve obter a confirmação humana. O banco exige o identificador
    do rascunho e a frase explícita; não infere consentimento de texto livre.
    """
    def __init__(self, store: Store):
        self.store = store

    def _draft(self, kind: str, payload: dict) -> dict:
        identifier = str(uuid.uuid4())
        with self.store.connect() as db:
            db.execute("INSERT INTO drafts(id,kind,payload_json,created_at) VALUES(?,?,?,?)", (identifier, kind, encode(payload), timestamp()))
        return {"draft_id": identifier, "kind": kind, "payload": payload, "saved_as_confirmed": False, "next_step": "Mostre este rascunho ao usuário. Somente após aprovação explícita, confirme com a frase APROVAR."}

    def propose_note(self, day: str, text: str, activity_id: int | None = None, rpe: float | None = None) -> dict:
        date.fromisoformat(day)
        if not text.strip() or len(text) > 10000:
            raise ValueError("Relato deve ter de 1 a 10000 caracteres.")
        if rpe is not None and (not math.isfinite(rpe) or not 0 <= rpe <= 10):
            raise ValueError("RPE deve estar entre 0 e 10.")
        if activity_id is not None:
            with self.store.connect() as db:
                if not db.execute("SELECT 1 FROM activities WHERE id=?", (activity_id,)).fetchone():
                    raise ValueError("Atividade não encontrada.")
        return self._draft("note", {"date": day, "text": text.strip(), "activity_id": activity_id, "rpe": rpe})

    def propose_plan(self, week_start: str, sessions: list[dict], rationale: str) -> dict:
        start = date.fromisoformat(week_start)
        if start.weekday() != 0:
            raise ValueError("A semana do plano deve começar na segunda-feira.")
        if not sessions or len(sessions) > 21 or not rationale.strip():
            raise ValueError("Informe de 1 a 21 sessões e a justificativa do plano.")
        validated_sessions = []
        for session in sessions:
            day = date.fromisoformat(session["date"])
            if not start <= day <= start + timedelta(days=6):
                raise ValueError("Sessão fora da semana do plano.")
            if session.get("sport") not in {"run", "ride", "swim", "walk", "strength", "rest"}:
                raise ValueError("Modalidade inválida no plano.")
            minutes = session.get("minutes")
            if not isinstance(minutes, (int, float)) or isinstance(minutes, bool) or not math.isfinite(minutes) or not 0 <= minutes <= 600:
                raise ValueError("Duração deve ser de 0 a 600 minutos.")
            if not isinstance(session.get("instruction"), str) or not session["instruction"].strip():
                raise ValueError("Cada sessão precisa de instrução.")
            validated_sessions.append({"date": day.isoformat(), "sport": session["sport"], "minutes": minutes, "instruction": session["instruction"]})
        return self._draft("plan", {"week_start": week_start, "sessions": validated_sessions, "rationale": rationale})

    def confirm(self, draft_id: str, confirmation: str) -> dict:
        if confirmation != "APROVAR":
            raise ValueError("A confirmação precisa ser exatamente APROVAR, após o usuário aprovar o conteúdo apresentado.")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            draft = db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if draft is None:
                raise ValueError("Rascunho não encontrado.")
            if draft["confirmed_at"]:
                return {"status": "already_confirmed", "kind": draft["kind"], "id": draft["saved_id"]}
            payload = json.loads(draft["payload_json"])
            now = timestamp()
            if draft["kind"] == "note":
                cursor = db.execute("INSERT INTO journal(activity_id,date,payload_json,confirmed_at,draft_id) VALUES(?,?,?,?,?)", (payload.get("activity_id"), payload["date"], encode(payload), now, draft_id))
            else:
                version = db.execute("SELECT coalesce(max(version),0)+1 FROM plans WHERE week_start=?", (payload["week_start"],)).fetchone()[0]
                cursor = db.execute("INSERT INTO plans(week_start,version,payload_json,approved_at,draft_id) VALUES(?,?,?,?,?)", (payload["week_start"], version, encode(payload), now, draft_id))
            identifier = cursor.lastrowid
            db.execute("UPDATE drafts SET confirmed_at=?,saved_id=? WHERE id=?", (now, identifier, draft_id))
        return {"status": "confirmed", "kind": draft["kind"], "id": identifier, "payload": payload}
