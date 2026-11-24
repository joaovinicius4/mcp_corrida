from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from fitdecode import FitError

from .analysis import Analysis
from .history import History
from .journal import Journal
from .storage import Store, encode, timestamp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Histórico pessoal de corrida e bike. Saídas JSON, arquivos originais preservados.")
    parser.add_argument("--data-dir", default=None, help="Pasta do banco e originais; padrão COACH_DATA_DIR ou data/.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Inicializa o banco local.")
    imp = commands.add_parser("import", help="Importa FIT, planilha Corridas, CSV ou JSON de evidências.")
    imp.add_argument("path")
    imp.add_argument("--activity-id", type=int)
    boot = commands.add_parser("bootstrap", help="Importa os arquivos já fornecidos neste projeto, de forma idempotente.")
    boot.add_argument("--root", default=".")
    listing = commands.add_parser("activities")
    listing.add_argument("--start")
    listing.add_argument("--end")
    listing.add_argument("--sport", choices=["run", "ride", "swim", "walk", "other"])
    listing.add_argument("--limit", type=int, default=30)
    listing.add_argument("--offset", type=int, default=0)
    for name in ("detail", "candidates"):
        cmd = commands.add_parser(name)
        cmd.add_argument("id", type=int)
    compare = commands.add_parser("compare")
    compare.add_argument("first", type=int)
    compare.add_argument("second", type=int)
    period = commands.add_parser("period")
    period.add_argument("start")
    period.add_argument("end")
    week = commands.add_parser("week")
    week.add_argument("monday")
    segment = commands.add_parser("segment")
    segment.add_argument("id", type=int)
    segment.add_argument("start_km", type=float)
    segment.add_argument("end_km", type=float)
    sample = commands.add_parser("samples")
    sample.add_argument("id", type=int)
    sample.add_argument("--limit", type=int, default=200)
    sample.add_argument("--offset", type=int, default=0)
    commands.add_parser("quality")
    commands.add_parser("context")
    note = commands.add_parser("note", help="Cria um rascunho de relato; ainda não o confirma.")
    note.add_argument("date")
    note.add_argument("text")
    note.add_argument("--activity-id", type=int)
    note.add_argument("--rpe", type=float)
    plan = commands.add_parser("plan", help="Cria rascunho de plano a partir de JSON.")
    plan.add_argument("path")
    confirm = commands.add_parser("confirm", help="Confirma explicitamente um rascunho previamente revisado.")
    confirm.add_argument("draft_id")
    confirm.add_argument("confirmation", choices=["APROVAR"])
    backup = commands.add_parser("backup")
    backup.add_argument("destination")
    export = commands.add_parser("export", help="Exporta um snapshot JSON do banco, incluindo proveniência.")
    export.add_argument("destination")
    export.add_argument("--include-raw", action="store_true", help="Inclui amostras FIT e linhas CSV; pode gerar um JSON grande.")
    return parser


def bootstrap(history: History, root: Path) -> dict:
    root = root.resolve(strict=True)
    paths = []
    workbook = root / "corridas_2026.xlsx"
    if workbook.exists():
        paths.append(workbook)
    paths.extend(sorted(root.glob("*.fit")))
    evidence = sorted((root / "outputs").glob("dados_consolidados_*/dados_consolidados.json"))
    if evidence:
        paths.append(evidence[-1])
    for export_dir in sorted(root.glob("[0-9]*")):
        if export_dir.is_dir():
            paths.extend(sorted(export_dir.rglob("*.csv")))
    paths.extend(sorted(root.glob("WhatsApp Image *.jpeg")))
    if not paths:
        raise ValueError("Nenhuma fonte conhecida foi encontrada. Use import para informar arquivos individuais.")
    reports = []
    for path in paths:
        reports.append({"file": path.name, "report": history.import_file(path)})
    return {"imports": reports, "quality": history.quality()}


def export_snapshot(store: Store, destination: str, include_raw: bool) -> dict:
    path = Path(destination).resolve()
    if path.exists():
        raise ValueError("O destino já existe; não será sobrescrito.")
    tables = ["sources", "activities", "observations", "laps", "profile_entries", "journal", "plans"]
    if include_raw:
        tables += ["samples", "source_rows"]
    result = {"exported_at": timestamp(), "schema_version": 1, "includes_raw": include_raw, "tables": {}}
    with store.connect() as db:
        db.execute("BEGIN")
        for table in tables:
            result["tables"][table] = [dict(row) for row in db.execute(f"SELECT * FROM {table}")]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encode(result), encoding="utf-8")
    return {"path": str(path), "tables": {k: len(v) for k, v in result["tables"].items()}, "note": "Os arquivos originais são incluídos pelo comando backup, não por este JSON."}


def run(args) -> dict:
    store = Store(args.data_dir)
    history, journal = History(store), Journal(store)
    analysis = Analysis(history)
    match args.command:
        case "init": return {"database": str(store.database_path), "schema_version": 1}
        case "import": return history.import_file(args.path, args.activity_id)
        case "bootstrap": return bootstrap(history, Path(args.root))
        case "activities": return history.list_activities(args.start, args.end, args.sport, args.limit, args.offset)
        case "detail": return history.detail(args.id)
        case "samples": return history.samples(args.id, args.offset, args.limit)
        case "candidates": return analysis.candidates(args.id)
        case "compare": return analysis.compare(args.first, args.second)
        case "period": return analysis.period(args.start, args.end)
        case "week": return analysis.weekly(args.monday)
        case "segment": return analysis.segment(args.id, args.start_km, args.end_km)
        case "quality": return history.quality()
        case "context": return history.context()
        case "note": return journal.propose_note(args.date, args.text, args.activity_id, args.rpe)
        case "plan":
            payload = json.loads(Path(args.path).read_text(encoding="utf-8-sig"))
            return journal.propose_plan(payload["week_start"], payload["sessions"], payload["rationale"])
        case "confirm": return journal.confirm(args.draft_id, args.confirmation)
        case "backup": return store.backup(args.destination)
        case "export": return export_snapshot(store, args.destination, args.include_raw)
    raise ValueError("Comando desconhecido.")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    try:
        print(json.dumps(run(args), ensure_ascii=False, indent=2))
    except (ValueError, KeyError, OSError, sqlite3.Error, BadZipFile, ParseError, FitError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
