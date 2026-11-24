from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile
import struct

import pytest

from coach_corrida.history import History
from coach_corrida.storage import Store


ROOT = Path(__file__).resolve().parents[1]


def make_workbook(path, rows):
    """Minimal OOXML test input, independent from the importer implementation."""
    headers = ["Data", "Hora", "Nome", "Local", "Distância (km)", "Tempo em movimento", "Tempo total", "Pace médio (/km)", "Elevação (m)", "Calorias", "FC média (bpm)", "FC máx (bpm)", "FC citada na descrição (bpm)", "Descrição (Strava)", "Mês"]
    xml_rows = []
    for index, values in enumerate([headers] + rows, 1):
        cells = []
        for column, value in enumerate(values):
            ref = f"{chr(65+column)}{index}"
            if value is None:
                continue
            if isinstance(value, str):
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
            else:
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
        xml_rows.append(f'<row r="{index}">{"".join(cells)}</row>')
    with ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Corridas" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+"".join(xml_rows)+'</sheetData></worksheet>')
    return path


def row(day, km, seconds, hr=None, maximum=None, hour=10, minute=34, description=None):
    serial = (datetime.fromisoformat(day)-datetime(1899, 12, 30)).days
    return [serial, (hour*3600+minute*60)/86400, "Treino", "Montes Claros", km, seconds/86400, seconds/86400, seconds/km/86400, 0, None, hr, maximum, None, description, int(day[5:7])]


@pytest.fixture
def history(tmp_path):
    return History(Store(tmp_path / "data"))


@pytest.fixture
def workbook_file(tmp_path):
    return make_workbook(tmp_path / "running.xlsx", [
        row("2026-05-29", 10, 2947, hour=12, minute=27),
        row("2026-10-31", 10, 3605, 156.102, 169),
        row("2026-11-20", 8, 2757, 152.802, 172, 7, 49),
    ])


@pytest.fixture
def real_fit():
    path = ROOT / "Zepp20261031103454.fit"
    if not path.exists():
        pytest.skip("FIT pessoal não é distribuído com o código; adicione-o para este teste de integração.")
    return path


@pytest.fixture
def cycling_fit(tmp_path):
    """Small independent FIT binary with CRC, session and sparse cycling records."""
    def definition(local, global_number, fields):
        return bytes([0x40 | local, 0, 0]) + struct.pack("<H", global_number) + bytes([len(fields)]) + b"".join(bytes(f) for f in fields)
    start = 1164542400  # FIT epoch seconds; the exact calendar date is not material here.
    session_fields = [(2, 4, 0x86), (5, 1, 0), (7, 4, 0x86), (8, 4, 0x86), (9, 4, 0x86), (16, 1, 2), (17, 1, 2), (20, 2, 0x84)]
    record_fields = [(253, 4, 0x86), (3, 1, 2), (5, 4, 0x86), (73, 4, 0x86), (7, 2, 0x84)]
    data = definition(0, 18, session_fields) + definition(1, 20, record_fields)
    for elapsed, distance, hr in [(0, 0, 120), (900, 500000, 140), (1800, 1000000, 145)]:
        data += bytes([1]) + struct.pack("<IBIIH", start+elapsed, hr, distance, 5556, 180)
    data += bytes([0]) + struct.pack("<IBIIIBBH", start, 2, 1800000, 1800000, 1000000, 135, 145, 180)
    header = struct.pack("<BBHI4s", 12, 0x20, 2180, len(data), b".FIT")
    blob = header+data
    crc = 0
    for value in blob:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    path = tmp_path / "cycling.fit"
    path.write_bytes(blob+struct.pack("<H", crc))
    return path
