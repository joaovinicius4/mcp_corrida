from __future__ import annotations

from datetime import date, timedelta
from statistics import mean

from .history import History, source_facts


class Analysis:
    def __init__(self, history: History):
        self.history = history

    def period(self, start: str, end: str) -> dict:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first > last:
            raise ValueError("Data inicial posterior à final.")
        with self.history.store.connect() as db:
            ids = db.execute("SELECT id FROM activities WHERE local_date BETWEEN ? AND ? ORDER BY local_date,id", (start, end)).fetchall()
            sports = {}
            for row in ids:
                facts, _, _ = source_facts(db, row["id"])
                entry = sports.setdefault(facts["sport"], {"records": 0, "distance_m": 0, "moving_time_s": 0, "distance_records": 0, "duration_records": 0, "with_average_hr": 0, "activity_ids": [], "dates": set()})
                entry["records"] += 1
                for field, counter in (("distance_m", "distance_records"), ("moving_time_s", "duration_records")):
                    if facts.get(field) is not None:
                        entry[field] += facts[field]
                        entry[counter] += 1
                entry["with_average_hr"] += facts.get("average_hr") is not None
                entry["activity_ids"].append(row["id"])
                entry["dates"].add(facts["local_date"])
        for sport, item in sports.items():
            item["training_days"] = len(item.pop("dates"))
            item["distance_km"] = item["distance_m"] / 1000 if item["distance_records"] else None
            # Overall pace requires the same records to provide both time and distance.
            complete = item["distance_records"] == item["duration_records"] == item["records"]
            item["pace_s_per_km"] = item["moving_time_s"] / item["distance_km"] if sport in {"run", "walk"} and complete and item["distance_km"] else None
            item["average_speed_kmh"] = item["distance_km"] / (item["moving_time_s"]/3600) if complete and item["moving_time_s"] else None
            if not item["duration_records"]:
                item["moving_time_s"] = None
            if not item["distance_records"]:
                item["distance_m"] = None
        return {"start": start, "end": end, "dates_inclusive": True, "by_sport": sports, "method": "Totais por modalidade; ritmos calculados de tempo total dividido por distância total apenas com cobertura completa.", "limitations": ["Registros separados do relógio não são necessariamente sessões independentes.", "Minutos de bike e corrida não são equivalentes; não foi criada uma carga fisiológica combinada.", "Os totais representam apenas os dados importados."]}

    def weekly(self, week_start: str) -> dict:
        start = date.fromisoformat(week_start)
        if start.weekday() != 0:
            raise ValueError("Informe a segunda-feira da semana em YYYY-MM-DD.")
        current = self.period(start.isoformat(), (start+timedelta(days=6)).isoformat())
        previous = self.period((start-timedelta(days=7)).isoformat(), (start-timedelta(days=1)).isoformat())
        changes = {}
        for sport in current["by_sport"].keys() | previous["by_sport"].keys():
            a, b = current["by_sport"].get(sport), previous["by_sport"].get(sport)
            changes[sport] = {}
            for key in ("distance_m", "moving_time_s"):
                now, before = a.get(key) if a else None, b.get(key) if b else None
                changes[sport][key] = {"current": now, "previous": before, "change_percent": 100*(now/before-1) if now is not None and before else None}
        return {"current": current, "previous": previous, "changes": changes, "interpretation": "Variação de volume registrado, não porcentagem de condicionamento ganho ou perdido."}

    def compare(self, first_id: int, second_id: int) -> dict:
        if first_id == second_id:
            raise ValueError("Escolha duas atividades diferentes.")
        first, second = self.history.detail(first_id), self.history.detail(second_id)
        if first["sport"] != second["sport"]:
            raise ValueError("Compare atividades da mesma modalidade. Use resumo por período para observar bike e corrida juntas.")
        a, b = first.get("pace_s_per_km"), second.get("pace_s_per_km")
        flags = ["Sem controle de esforço percebido, percurso, clima, fadiga e sensor, a comparação não estima perda de condicionamento."]
        d1, d2 = first.get("distance_m"), second.get("distance_m")
        if d1 and d2 and abs(d1/d2-1) > 0.05:
            flags.append("Distâncias diferem mais de 5%.")
        if first.get("average_hr") is None or second.get("average_hr") is None:
            flags.append("FC média não disponível em uma ou ambas as atividades.")
        elif abs(first["average_hr"]-second["average_hr"]) <= 5:
            flags.append("FC média próxima não garante esforço ou condições equivalentes.")
        else:
            flags.append("As frequências cardíacas médias diferem mais de 5 bpm.")
        if not first["sample_sources"] or not second["sample_sources"]:
            flags.append("Uma ou ambas não possuem séries FIT para comparar trechos.")
        return {"first": {k: first[k] for k in ("id", "local_date", "name", "distance_m", "moving_time_s", "pace_s_per_km", "average_speed_kmh", "average_power_w", "average_hr", "max_hr", "sources")},
                "second": {k: second[k] for k in ("id", "local_date", "name", "distance_m", "moving_time_s", "pace_s_per_km", "average_speed_kmh", "average_power_w", "average_hr", "max_hr", "sources")},
                "delta_second_minus_first": {"pace_s_per_km": b-a if a is not None and b is not None else None, "pace_percent": 100*(b/a-1) if a and b is not None else None, "average_speed_kmh": second["average_speed_kmh"]-first["average_speed_kmh"] if first["average_speed_kmh"] is not None and second["average_speed_kmh"] is not None else None, "average_hr_bpm": second["average_hr"]-first["average_hr"] if first["average_hr"] is not None and second["average_hr"] is not None else None},
                "conditioning_loss_percent": None, "method_version": "1", "limitations": flags}

    def candidates(self, activity_id: int, limit: int = 5) -> dict:
        if not 1 <= limit <= 20:
            raise ValueError("Limite de candidatos deve ser de 1 a 20.")
        reference = self.history.detail(activity_id)
        candidates = []
        with self.history.store.connect() as db:
            ids = db.execute("SELECT id FROM activities WHERE sport=? AND id<>?", (reference["sport"], activity_id)).fetchall()
            for row in ids:
                facts, origins, _ = source_facts(db, row["id"])
                d1, d2 = reference.get("distance_m"), facts.get("distance_m")
                t1, t2 = reference.get("moving_time_s"), facts.get("moving_time_s")
                if not all((d1, d2, t1, t2)):
                    continue
                distance_difference, duration_difference = abs(d2/d1-1), abs(t2/t1-1)
                if distance_difference <= 0.15 and duration_difference <= 0.30:
                    candidates.append((distance_difference+duration_difference, self.history.summary(row["id"], facts, origins)))
        candidates.sort(key=lambda x: (x[0], x[1]["local_date"]))
        return {"reference_id": activity_id, "candidates": [item for _, item in candidates[:limit]], "criteria": "Mesma modalidade; distância até 15% e duração até 30% diferentes; ordem pela soma das diferenças relativas.", "warning": "São candidatos por distância e duração; a equivalência de esforço e percurso precisa ser avaliada."}

    def segment(self, activity_id: int, start_km: float, end_km: float) -> dict:
        if not 0 <= start_km < end_km:
            raise ValueError("Informe 0 <= km inicial < km final.")
        activity = self.history.detail(activity_id)
        if not activity["sample_sources"]:
            return {"activity_id": activity_id, "available": False, "reason": "É necessário um FIT com amostras de distância; médias da sessão e prints não substituem essa série."}
        source_id = activity["sample_sources"][0]["source_id"]
        with self.history.store.connect() as db:
            maximum = db.execute("SELECT max(distance_m) FROM samples WHERE activity_id=? AND source_id=?", (activity_id, source_id)).fetchone()[0]
            if maximum is None or end_km*1000 > maximum + 1:
                return {"activity_id": activity_id, "available": False, "reason": "O trecho solicitado ultrapassa a distância disponível nas amostras."}
            rows = db.execute("SELECT timestamp,elapsed_s,distance_m,heart_rate,speed_mps FROM samples WHERE activity_id=? AND source_id=? AND distance_m BETWEEN ? AND ? ORDER BY sequence", (activity_id, source_id, start_km*1000, end_km*1000)).fetchall()
        if len(rows) < 2:
            return {"activity_id": activity_id, "available": False, "reason": "Menos de duas amostras no trecho."}
        hr = [r["heart_rate"] for r in rows if r["heart_rate"] is not None]
        speeds = [r["speed_mps"] for r in rows if r["speed_mps"] is not None]
        gaps = [b["elapsed_s"]-a["elapsed_s"] for a, b in zip(rows, rows[1:])]
        return {"activity_id": activity_id, "source_id": source_id, "available": True, "requested_km": [start_km, end_km],
                "actual_distance_bounds_m": [rows[0]["distance_m"], rows[-1]["distance_m"]], "first_timestamp": rows[0]["timestamp"], "last_timestamp": rows[-1]["timestamp"],
                "records": len(rows), "hr_records": len(hr), "mean_sample_hr_bpm": mean(hr) if hr else None, "max_sample_hr_bpm": max(hr) if hr else None,
                "mean_sample_speed_mps": mean(speeds) if speeds else None, "max_gap_s": max(gaps), "method_version": "1",
                "limitations": ["Médias aritméticas das amostras, sem imputação e sem ponderação por tempo.", "Lacunas e pausas podem afetar a representatividade; os limites reais não foram interpolados."]}
