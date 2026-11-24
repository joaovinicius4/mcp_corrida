import sys
import os
import socket
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client


def test_real_stdio_discovery_queries_and_errors(history, workbook_file):
    history.import_file(workbook_file)

    async def exercise():
        params = StdioServerParameters(command=sys.executable, args=["-m", "coach_corrida.mcp_server", "--data-dir", str(history.store.home)])
        with anyio.fail_after(30):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    listing = await client.list_tools()
                    names = {t.name for t in listing.tools}
                    assert "listar_atividades" in names
                    assert "confirmar_rascunho" not in names
                    assert all(t.annotations.read_only_hint for t in listing.tools)
                    result = await client.call_tool("listar_atividades", {"limite": 2})
                    assert not result.is_error
                    payload = result.structured_content
                    assert payload is not None, result.model_dump()
                    assert payload["total"] == 3
                    assert len(payload["items"]) == 2
                    assert payload["next_offset"] == 2
                    comparison = await client.call_tool("comparar_atividades", {"primeira_id": 1, "segunda_id": 2})
                    assert comparison.structured_content["conditioning_loss_percent"] is None
                    invalid = await client.call_tool("detalhar_atividade", {"atividade_id": 99999})
                    assert invalid.is_error
    anyio.run(exercise)


def test_stdio_write_draft_and_confirmation_roundtrip(history):
    async def exercise():
        params = StdioServerParameters(command=sys.executable, args=["-m", "coach_corrida.mcp_server", "--data-dir", str(history.store.home), "--enable-writes"])
        with anyio.fail_after(30):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    draft = await client.call_tool("propor_relato", {"data": "2026-11-23", "texto": "Relato de teste", "rpe": 3})
                    assert not draft.is_error
                    assert draft.structured_content is not None, draft.model_dump()
                    identifier = draft.structured_content["draft_id"]
                    context = await client.call_tool("consultar_contexto", {})
                    assert context.structured_content["recent_journal"] == []
                    bad = await client.call_tool("confirmar_rascunho", {"rascunho_id": identifier, "confirmacao": "SIM"})
                    assert bad.is_error
                    approved = await client.call_tool("confirmar_rascunho", {"rascunho_id": identifier, "confirmacao": "APROVAR"})
                    assert not approved.is_error
                    context = await client.call_tool("consultar_contexto", {})
                    assert len(context.structured_content["recent_journal"]) == 1
    anyio.run(exercise)


def test_streamable_http_local_roundtrip(history, workbook_file, tmp_path):
    history.import_file(workbook_file)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    log = tmp_path / "http-server.log"
    with log.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen([sys.executable, "-m", "coach_corrida.mcp_server", "--data-dir", str(history.store.home), "--http-port", str(port)], stdout=subprocess.DEVNULL, stderr=stderr)
        try:
            deadline = time.monotonic()+15
            while True:
                assert process.poll() is None, log.read_text(encoding="utf-8", errors="replace")
                try:
                    urlopen(f"http://127.0.0.1:{port}/", timeout=0.5).close()
                    break
                except HTTPError:
                    break  # Any HTTP response proves the local listener is ready.
                except (URLError, TimeoutError):
                    if time.monotonic() > deadline:
                        raise AssertionError("Servidor HTTP não iniciou: "+log.read_text(encoding="utf-8", errors="replace"))
                    time.sleep(0.1)

            async def exercise():
                with anyio.fail_after(20):
                    async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as (read, write):
                        async with ClientSession(read, write) as client:
                            await client.initialize()
                            result = await client.call_tool("resumir_periodo", {"inicio": "2026-01-01", "fim": "2026-12-31"})
                            assert not result.is_error
                            assert result.structured_content["by_sport"]["run"]["distance_km"] == 28
            anyio.run(exercise)
        finally:
            if os.name == "nt" and process.poll() is None:
                # Windows' venv launcher starts another interpreter. Terminating
                # only the launcher can leave the listener running.
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False, timeout=10)
            else:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            with socket.socket() as probe:
                probe.settimeout(1)
                assert probe.connect_ex(("127.0.0.1", port)) != 0, "O teste deixou o servidor HTTP ativo."
