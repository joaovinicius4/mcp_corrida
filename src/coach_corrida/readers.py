from __future__ import annotations

import csv
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET
from zipfile import ZipFile
from zoneinfo import ZoneInfo


NAMESPACE = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def number(value):
    if value is None or value == "":
        return None
    n = float(value)
    if not math.isfinite(n):
        raise ValueError("Valor numérico não finito.")
    return n


def validated(facts: dict) -> dict:
    if facts.get("sport") not in {"run", "ride", "swim", "walk", "other"}:
        raise ValueError("Modalidade não reconhecida.")
    datetime.strptime(facts["local_date"], "%Y-%m-%d")
    for field in ("distance_m", "moving_time_s", "elapsed_time_s"):
        value = facts.get(field)
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError(f"{field} inválido.")
    for field in ("average_hr", "max_hr"):
        value = facts.get(field)
        if value is not None and not 0 < value < 255:
            raise ValueError(f"{field} fora do intervalo do registro; não foi imputado.")
    return facts


def workbook(path: Path) -> tuple[list[dict], list[str]]:
    """Lê a tabela Corridas. Fórmulas sem cache não são inventadas/recalculadas."""
    with ZipFile(path) as z:
        if sum(info.file_size for info in z.infolist()) > 100_000_000:
            raise ValueError("Planilha excede o limite de leitura de 100 MB.")
        strings = []
        if "xl/sharedStrings.xml" in z.namelist():
            strings = ["".join(n.itertext()) for n in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("s:si", NAMESPACE)]
        rels = {n.attrib["Id"]: n.attrib["Target"] for n in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        props = wb.find("s:workbookPr", NAMESPACE)
        epoch = datetime(1904, 1, 1) if props is not None and props.attrib.get("date1904") in {"1", "true"} else datetime(1899, 12, 30)
        table = next((s for s in wb.findall("s:sheets/s:sheet", NAMESPACE) if s.attrib["name"] == "Corridas"), None)
        if table is None:
            raise ValueError("A planilha precisa da aba Corridas.")
        target = rels[table.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]]
        target = target.lstrip("/") if target.startswith("/") else "xl/" + target
        if ".." in PurePosixPath(target).parts:
            raise ValueError("Relacionamento inválido na planilha.")
        rows = []
        for row in ET.fromstring(z.read(target)).findall("s:sheetData/s:row", NAMESPACE):
            values = {}
            for cell in row.findall("s:c", NAMESPACE):
                node = cell.find("s:v", NAMESPACE)
                value = node.text if node is not None else None
                if cell.attrib.get("t") == "s" and value is not None:
                    value = strings[int(value)]
                elif cell.attrib.get("t") == "inlineStr":
                    value = "".join(cell.find("s:is", NAMESPACE).itertext())
                if cell.find("s:f", NAMESPACE) is not None and value is None:
                    raise ValueError(f"Fórmula sem resultado salvo em {cell.attrib['r']}.")
                values[re.sub(r"\d", "", cell.attrib["r"])] = value
            if any(v is not None for v in values.values()):
                rows.append((int(row.attrib["r"]), values))
    if not rows:
        raise ValueError("Aba Corridas vazia.")
    header = rows[0][1]
    columns = {v: k for k, v in header.items() if v is not None}
    required = ["Data", "Distância (km)", "Tempo em movimento"]
    if any(name not in columns for name in required):
        raise ValueError("Cabeçalhos incompatíveis; esperado Data, Distância (km), Tempo em movimento.")
    activities = []
    for row_number, values in rows[1:]:
        def get(label):
            return values.get(columns.get(label, ""))
        if get("Data") in (None, ""):
            continue
        day = epoch + timedelta(days=number(get("Data")))
        seconds = round((number(get("Hora")) or 0) * 86400)
        start = day.replace(hour=0, minute=0, second=0) + timedelta(seconds=seconds)
        facts = validated({
            "sport": "run", "local_date": day.date().isoformat(),
            "started_at": start.isoformat() if get("Hora") is not None else None,
            "time_basis": "local_unverified", "name": get("Nome"), "location": get("Local"),
            "distance_m": number(get("Distância (km)")) * 1000,
            "moving_time_s": round(number(get("Tempo em movimento")) * 86400),
            "elapsed_time_s": round(number(get("Tempo total")) * 86400) if get("Tempo total") is not None else None,
            "average_hr": number(get("FC média (bpm)")), "max_hr": number(get("FC máx (bpm)")),
            "hr_in_description": number(get("FC citada na descrição (bpm)")),
            "description": get("Descrição (Strava)"), "elevation_gain_m": number(get("Elevação (m)")),
            "calories": number(get("Calorias")),
        })
        activities.append({"record_key": f"Corridas:{row_number}", "facts": facts, "priority": 60})
    return activities, ["Horas da planilha mantidas como locais com fuso não confirmado.", "FC vazia significa não fornecida nesta fonte; não foi preenchida com FC da descrição."]


def fit_file(path: Path, timezone_name: str) -> list[dict]:
    import fitdecode
    sessions, records, lap_records, events = [], [], [], []
    with fitdecode.FitReader(path, check_crc=fitdecode.CrcCheck.RAISE) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            data = {f.name: f.value for f in frame.fields}
            if frame.name == "session":
                sessions.append(data)
            elif frame.name == "record":
                records.append(data)
            elif frame.name == "lap":
                lap_records.append(data)
            elif frame.name == "event":
                events.append(data)
    if len(sessions) != 1:
        raise ValueError("Esperada uma sessão por FIT. FIT multiesporte precisa ser separado; nenhuma sessão foi importada.")
    session = sessions[0]
    start = session.get("start_time")
    if not isinstance(start, datetime):
        raise ValueError("FIT sem início de sessão válido.")
    start = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start
    local = start.astimezone(ZoneInfo(timezone_name))
    sport = {"running": "run", "cycling": "ride", "swimming": "swim", "walking": "walk"}.get(session.get("sport"), "other")
    facts = validated({
        "sport": sport, "local_date": local.date().isoformat(), "started_at": start.isoformat(),
        "time_basis": "UTC", "timezone": timezone_name, "name": f"{sport} {local.date()}",
        "distance_m": session.get("total_distance"), "moving_time_s": session.get("total_timer_time"),
        "elapsed_time_s": session.get("total_elapsed_time"), "average_hr": session.get("avg_heart_rate"),
        "max_hr": session.get("max_heart_rate"), "elevation_gain_m": session.get("total_ascent"),
        "calories": session.get("total_calories"), "average_speed_mps": session.get("enhanced_avg_speed", session.get("avg_speed")),
        "average_power_w": session.get("avg_power"), "fit_session": session, "fit_events": events,
    })
    samples = []
    for r in records:
        ts = r.get("timestamp")
        if not isinstance(ts, datetime):
            continue
        ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
        hr = r.get("heart_rate")
        samples.append({
            "timestamp": ts.isoformat(), "elapsed_s": (ts - start).total_seconds(),
            "distance_m": r.get("distance"), "heart_rate": hr if isinstance(hr, (float, int)) and 0 < hr < 255 else None,
            "speed_mps": r.get("enhanced_speed", r.get("speed")), "cadence": r.get("cadence"),
            "altitude_m": r.get("enhanced_altitude", r.get("altitude")), "payload": r,
        })
    return [{"record_key": "session:0", "facts": facts, "samples": samples, "laps": lap_records, "priority": 100}]


def csv_rows(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames:
            raise ValueError("CSV sem cabeçalho.")
        for row in reader:
            if None in row:
                # Some Zepp exports contain unescaped JSON in the last column.
                # Keep every extra value explicitly; never guess where it belongs.
                row["__extra_columns_unparsed"] = row.pop(None)
            if any(v is None for v in row.values()):
                row["__missing_columns"] = [k for k, v in row.items() if v is None]
            yield row
