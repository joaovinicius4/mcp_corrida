from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .readers import csv_rows, fit_file, validated, workbook
from .storage import Store, encode, timestamp


def duration(text: str) -> float:
    values = [float(v) for v in text.split(":")]
    if len(values) not in (2, 3) or any(v < 0 or not math.isfinite(v) for v in values):
        raise ValueError("Duração inválida.")
    total = 0.0
    for value in values:
        total = total * 60 + value
    return total


def source_facts(db, activity_id: int) -> tuple[dict, dict, list]:
    observations = db.execute("""SELECT o.*,s.name,s.kind,s.sha256 FROM observations o
        JOIN sources s ON s.id=o.source_id WHERE activity_id=? ORDER BY priority DESC,o.id DESC""", (activity_id,)).fetchall()
    facts, origins, sources = {}, {}, []
    for row in observations:
        payload = json.loads(row["facts_json"])
        source = {"source_id": row["source_id"], "name": row["name"], "kind": row["kind"], "record_key": row["record_key"], "sha256": row["sha256"]}
        sources.append(source)
        for key, value in payload.items():
            if value is not None and key not in facts:
                facts[key] = value
                origins[key] = source
    # A descriptive workout name is more useful than a generated FIT filename label.
    for row in observations:
        payload = json.loads(row["facts_json"])
        if row["kind"] != "fit" and payload.get("name"):
            facts["name"] = payload["name"]
            origins["name"] = next(s for s in sources if s["source_id"] == row["source_id"] and s["record_key"] == row["record_key"])
            break
    return facts, origins, sources


class History:
    def __init__(self, store: Store, timezone_name: str = "America/Sao_Paulo"):
        self.store = store
        self.timezone_name = timezone_name
        ZoneInfo(timezone_name)  # Fail early instead of silently changing timezones.

    def _clock(self, value: str | None) -> datetime | None:
        if not value:
            return None
        value = datetime.fromisoformat(value)
        return value.astimezone(ZoneInfo(self.timezone_name)).replace(tzinfo=None) if value.tzinfo else value

    def _match(self, db, facts: dict, target: int | None) -> int | None:
        if target is not None:
            row = db.execute("SELECT * FROM activities WHERE id=?", (target,)).fetchone()
            if row is None:
                raise ValueError("Atividade de destino não existe.")
            if row["sport"] != facts["sport"] or row["local_date"] != facts["local_date"]:
                raise ValueError("Destino possui data ou modalidade diferente.")
            return target
        candidates = []
        for row in db.execute("SELECT * FROM activities WHERE sport=? AND local_date=?", (facts["sport"], facts["local_date"])):
            a, b = row["moving_time_s"], facts.get("moving_time_s")
            if a is None or b is None or abs(a-b) > max(3, min(a,b)*0.01):
                continue
            a, b = row["distance_m"], facts.get("distance_m")
            if a is not None and b is not None and abs(a-b) > max(30, min(a,b)*0.005):
                continue
            old_start, new_start = self._clock(row["started_at"]), self._clock(facts.get("started_at"))
            if old_start and new_start and abs((old_start-new_start).total_seconds()) > 120:
                continue
            candidates.append(row["id"])
        if len(candidates) > 1:
            raise ValueError("Vínculo ambíguo: mais de um treino coincide. Use --activity-id para confirmar o destino de um FIT.")
        return candidates[0] if candidates else None

    def _observation(self, db, source_id: int, item: dict, target: int | None) -> tuple[int, bool]:
        facts = validated(item["facts"])
        activity_id = self._match(db, facts, target)
        created = activity_id is None
        if created:
            cursor = db.execute("INSERT INTO activities(sport,local_date,created_at) VALUES(?,?,?)", (facts["sport"], facts["local_date"], timestamp()))
            activity_id = cursor.lastrowid
        db.execute("INSERT INTO observations(activity_id,source_id,record_key,priority,facts_json) VALUES(?,?,?,?,?)", (activity_id, source_id, item["record_key"], item["priority"], encode(facts)))
        for sequence, sample in enumerate(item.get("samples", [])):
            db.execute("""INSERT INTO samples VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
                activity_id, source_id, sequence, sample["timestamp"], sample["elapsed_s"], sample.get("distance_m"), sample.get("heart_rate"), sample.get("speed_mps"), sample.get("cadence"), sample.get("altitude_m"), encode(sample.get("payload", {})),
            ))
        for sequence, lap in enumerate(item.get("laps", [])):
            db.execute("INSERT INTO laps VALUES(?,?,?,?)", (activity_id, source_id, sequence, encode(lap)))
        resolved, _, _ = source_facts(db, activity_id)
        db.execute("UPDATE activities SET started_at=?,distance_m=?,moving_time_s=?,name=? WHERE id=?", (resolved.get("started_at"), resolved.get("distance_m"), resolved.get("moving_time_s"), resolved.get("name"), activity_id))
        return activity_id, created

    def _evidence(self, path: Path) -> tuple[list, dict]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "screenshots_transcribed" not in data or "user_confirmed_reference" not in data:
            raise ValueError("JSON não é uma consolidação de evidências reconhecida.")
        items = []
        for index, evidence in enumerate(data["screenshots_transcribed"]):
            laps = evidence.get("laps", [])
            total_time = evidence.get("exercise_time_display") or (laps[-1].get("cumulative_time_display") if laps else None)
            if not total_time:
                raise ValueError("Print sem duração; é necessário confirmar sua associação.")
            facts = {
                "sport": "run", "local_date": evidence["date"], "name": evidence["name"],
                "distance_m": evidence["distance_km"]*1000 if evidence.get("distance_km") is not None else None,
                "moving_time_s": duration(total_time), "average_hr": evidence.get("average_hr_bpm"),
                "rpe": evidence.get("rpe"), "temperature_c": evidence.get("temperature_display_c"),
                "humidity_percent": evidence.get("humidity_display_percent"),
                "evidence_images": evidence.get("sources") or [evidence["source"]],
                "date_confirmation": evidence.get("date_source"),
            }
            items.append({"record_key": f"screenshot:{index}", "priority": 80, "facts": facts, "laps": laps})
        for index, evidence in enumerate(data["user_confirmed_reference"]):
            items.append({"record_key": f"user:{index}", "priority": 75, "facts": {
                "sport": "run", "local_date": evidence["date"], "name": evidence["name"],
                "distance_m": evidence["distance_km"]*1000, "moving_time_s": evidence["time_s"],
                "average_hr": evidence.get("average_hr_bpm"), "reported_pace": evidence.get("pace_reported"),
                "hr_origin_note": evidence.get("hr_source"), "reference_note": evidence.get("note"),
            }})
        return items, data.get("user_reported_context", {})

    def import_file(self, filename: str | Path, activity_id: int | None = None) -> dict:
        path = Path(filename).resolve(strict=True)
        if path.stat().st_size > 100_000_000:
            raise ValueError("Arquivo excede 100 MB. Divida a importação.")
        suffix = path.suffix.lower()
        if suffix not in {".fit", ".xlsx", ".csv", ".json", ".jpeg", ".jpg", ".png"}:
            raise ValueError("Formato não suportado. Use FIT, XLSX, CSV, JSON de evidências ou uma imagem para arquivar.")
        digest, stored_path = self.store.archive(path)
        with self.store.connect() as db:
            previous = db.execute("SELECT id,report_json FROM sources WHERE sha256=?", (digest,)).fetchone()
        if previous:
            return {"status": "already_imported", "source_id": previous["id"], "previous_report": json.loads(previous["report_json"])}
        kind = {".fit": "fit", ".xlsx": "workbook", ".csv": "zepp_csv", ".json": "confirmed_evidence"}.get(suffix, "evidence_image")
        items, warnings, profile = [], [], {}
        if kind == "workbook":
            items, warnings = workbook(path)
        elif kind == "fit":
            items = fit_file(path, self.timezone_name)
        elif kind == "confirmed_evidence":
            items, profile = self._evidence(path)
            warnings.append("Transcrições e relatos não são amostras brutas de sensores. As imagens precisam ser preservadas separadamente.")
        if activity_id is not None and (kind != "fit" or len(items) != 1):
            raise ValueError("--activity-id é exclusivo de FIT com uma sessão.")
        report = {"status": "imported", "kind": kind, "new_activities": 0, "enriched_activities": 0, "activity_ids": [], "raw_rows": 0, "malformed_raw_rows": 0, "samples": 0, "warnings": warnings}
        with self.store.connect() as db:
            # Recheck inside the write transaction to avoid duplicate concurrent imports.
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT id,report_json FROM sources WHERE sha256=?", (digest,)).fetchone()
            if previous:
                return {"status": "already_imported", "source_id": previous["id"], "previous_report": json.loads(previous["report_json"])}
            cursor = db.execute("INSERT INTO sources(sha256,name,kind,stored_path,imported_at) VALUES(?,?,?,?,?)", (digest, path.name, kind, stored_path, timestamp()))
            source_id = cursor.lastrowid
            report["source_id"] = source_id
            for item in items:
                aid, created = self._observation(db, source_id, item, activity_id)
                report["new_activities" if created else "enriched_activities"] += 1
                report["activity_ids"].append(aid)
                report["samples"] += len(item.get("samples", []))
            if kind == "zepp_csv":
                for index, row in enumerate(csv_rows(path)):
                    db.execute("INSERT INTO source_rows VALUES(?,?,?)", (source_id, index, encode(row)))
                    report["raw_rows"] += 1
                    if "__extra_columns_unparsed" in row or "__missing_columns" in row:
                        report["malformed_raw_rows"] += 1
                if report["malformed_raw_rows"]:
                    report["warnings"].append("Há linhas fora do cabeçalho: campos excedentes/ausentes foram preservados e marcados, sem interpretação automática.")
                report["warnings"].append("CSV preservado como registros da fonte. FC diária não foi vinculada automaticamente às corridas; tipos numéricos de esporte não foram adivinhados.")
            if profile:
                db.execute("INSERT INTO profile_entries(source_id,entry_key,payload_json) VALUES(?,?,?)", (source_id, "user_reported_context", encode(profile)))
            db.execute("UPDATE sources SET report_json=? WHERE id=?", (encode(report), source_id))
        return report

    def list_activities(self, start: str | None = None, end: str | None = None, sport: str | None = None, limit: int = 30, offset: int = 0) -> dict:
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("Use limite de 1 a 200 e offset não negativo.")
        clauses, values = [], []
        for key, operator, value in (("local_date", ">=", start), ("local_date", "<=", end), ("sport", "=", sport)):
            if value is not None:
                if key == "local_date":
                    datetime.strptime(value, "%Y-%m-%d")
                elif value not in {"run", "ride", "swim", "walk", "other"}:
                    raise ValueError("Modalidade inválida.")
                clauses.append(f"{key}{operator}?")
                values.append(value)
        if start and end and start > end:
            raise ValueError("Data inicial posterior à final.")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.store.connect() as db:
            total = db.execute("SELECT count(*) FROM activities" + where, values).fetchone()[0]
            ids = db.execute("SELECT id FROM activities" + where + " ORDER BY local_date DESC,started_at DESC,id DESC LIMIT ? OFFSET ?", values + [limit, offset]).fetchall()
            items = []
            for row in ids:
                facts, origins, _ = source_facts(db, row["id"])
                items.append(self.summary(row["id"], facts, origins))
        return {"total": total, "offset": offset, "limit": limit, "items": items, "next_offset": offset + limit if offset + limit < total else None}

    @staticmethod
    def summary(activity_id: int, facts: dict, origins: dict) -> dict:
        keys = ("sport", "local_date", "started_at", "time_basis", "name", "distance_m", "moving_time_s", "elapsed_time_s", "average_hr", "max_hr", "average_power_w", "description", "rpe")
        result = {"id": activity_id, **{key: facts.get(key) for key in keys}}
        d, t = facts.get("distance_m"), facts.get("moving_time_s")
        result["pace_s_per_km"] = t / (d / 1000) if facts.get("sport") in {"run", "walk"} and d and t is not None else None
        result["average_speed_kmh"] = d / t * 3.6 if d is not None and t else None
        result["field_sources"] = {key: origins[key] for key in keys if key in origins}
        return result

    def detail(self, activity_id: int) -> dict:
        with self.store.connect() as db:
            if db.execute("SELECT 1 FROM activities WHERE id=?", (activity_id,)).fetchone() is None:
                raise ValueError("Atividade não encontrada.")
            facts, origins, sources = source_facts(db, activity_id)
            grouped = db.execute("""SELECT source_id,count(*) n,count(heart_rate) hr FROM samples
                WHERE activity_id=? GROUP BY source_id ORDER BY n DESC,source_id DESC""", (activity_id,)).fetchall()
            coverage = [dict(row) | {"hr_record_fraction": row["hr"]/row["n"]} for row in grouped]
            lap_source = db.execute("""SELECT l.source_id FROM laps l JOIN sources s ON l.source_id=s.id
                WHERE activity_id=? ORDER BY CASE s.kind WHEN 'fit' THEN 0 ELSE 1 END,l.source_id DESC LIMIT 1""", (activity_id,)).fetchone()
            laps = [json.loads(row[0]) for row in db.execute("SELECT payload_json FROM laps WHERE activity_id=? AND source_id=? ORDER BY sequence", (activity_id, lap_source[0]))] if lap_source else []
            notes = [dict(row) | {"payload": json.loads(row["payload_json"])} for row in db.execute("SELECT id,date,payload_json,confirmed_at FROM journal WHERE activity_id=? ORDER BY id", (activity_id,))]
        return {**self.summary(activity_id, facts, origins), "facts": facts, "sources": sources, "sample_sources": coverage, "laps": laps, "journal": notes, "limitations": ["FC de volta, FC média da sessão e FC máxima são métricas distintas.", "Cobertura de FC é fração de registros, não porcentagem do tempo."]}

    def samples(self, activity_id: int, offset: int = 0, limit: int = 200) -> dict:
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("Use limite de 1 a 1000 e offset não negativo.")
        detail = self.detail(activity_id)
        if not detail["sample_sources"]:
            return {"activity_id": activity_id, "total": 0, "items": [], "reason": "Não há amostras FIT. Prints de voltas não foram convertidos em amostras."}
        source = detail["sample_sources"][0]
        with self.store.connect() as db:
            rows = db.execute("SELECT sequence,timestamp,elapsed_s,distance_m,heart_rate,speed_mps,cadence,altitude_m FROM samples WHERE activity_id=? AND source_id=? ORDER BY sequence LIMIT ? OFFSET ?", (activity_id, source["source_id"], limit, offset)).fetchall()
        return {"activity_id": activity_id, "source_id": source["source_id"], "total": source["n"], "offset": offset, "items": [dict(r) for r in rows], "next_offset": offset + limit if offset + limit < source["n"] else None, "cadence_note": "Cadência é o valor bruto FIT; sua unidade pode representar ciclos e não passos completos por minuto."}

    def quality(self) -> dict:
        with self.store.connect() as db:
            activities = db.execute("SELECT id FROM activities ORDER BY id").fetchall()
            sources = [dict(row) for row in db.execute("SELECT id,name,kind,sha256,imported_at,report_json FROM sources ORDER BY id")]
            missing_hr, with_samples, counts = [], 0, {}
            for row in activities:
                facts, _, _ = source_facts(db, row["id"])
                counts[facts["sport"]] = counts.get(facts["sport"], 0) + 1
                if facts.get("average_hr") is None:
                    missing_hr.append(row["id"])
                if db.execute("SELECT 1 FROM samples WHERE activity_id=? LIMIT 1", (row["id"],)).fetchone():
                    with_samples += 1
        for s in sources:
            s["report"] = json.loads(s.pop("report_json"))
        return {"activities": len(activities), "by_sport": counts, "with_fit_samples": with_samples, "missing_average_hr_count": len(missing_hr), "missing_average_hr_ids": missing_hr, "sources": sources, "warnings": ["Dados ausentes não equivalem a zero.", "FC diária permanece separada da FC de exercício.", "Corridas divididas pelo relógio continuam separadas até confirmação explícita.", "Nenhuma conclusão automática de perda percentual de condicionamento é calculada."]}

    def context(self) -> dict:
        with self.store.connect() as db:
            profile = [{"id": r["id"], "source_id": r["source_id"], "key": r["entry_key"], "data": json.loads(r["payload_json"])} for r in db.execute("SELECT * FROM profile_entries ORDER BY id")]
            notes = [{"id": r["id"], "date": r["date"], "activity_id": r["activity_id"], "data": json.loads(r["payload_json"])} for r in db.execute("SELECT * FROM journal ORDER BY date DESC,id DESC LIMIT 30")]
            plans = [{"id": r["id"], "week_start": r["week_start"], "version": r["version"], "data": json.loads(r["payload_json"])} for r in db.execute("SELECT * FROM plans ORDER BY week_start DESC,version DESC LIMIT 12")]
        return {"profile": profile, "recent_journal": notes, "approved_plans": plans, "policy": "Seja honesto. Cite atividades/fontes; separe fato, hipótese e desconhecido. FC não determina esforço sozinha. Não invente zonas, VO2max ou prazo de recuperação."}
