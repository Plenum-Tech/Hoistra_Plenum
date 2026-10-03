"""Output Generation sends its text artefacts gzip-encoded, so the step is bytes-bound no more.

A 280k-row workbook's artefacts run to ~185 MB (output.json 75 MB, output.sql 70 MB, 17 CSVs
19 MB, output.xlsx 8 MB). From a laptop on a ~280 KB/s uplink that is nine minutes of "Output
generation is running" (runs ee83c4b4, 71151cfa, fcb2dd75 — 30 Sep / 1 Oct 2026). JSON, SQL,
CSV and Markdown compress 8–12×; uploaded with Content-Encoding: gzip they download as the same
plain files (browsers and every HTTP client decode it), only far faster. Binary artefacts —
the Excel workbook, the report — go as they are.
"""

from __future__ import annotations

import asyncio
import gzip


class _Client:
    def __init__(self, store, path):
        self.store, self.path = store, path
        self.url = "https://blob.example/" + path

    async def upload_blob(self, data, overwrite=False, progress_hook=None, content_settings=None):
        self.store[self.path.rsplit("/", 1)[-1]] = (bytes(data), content_settings)
        if progress_hook:
            await progress_hook(len(data), len(data))


class _Svc:
    def __init__(self):
        self.store: dict[str, tuple[bytes, object]] = {}

    def get_blob_client(self, container, blob):
        return _Client(self.store, blob)


class _Beat:
    def __init__(self):
        self.ticks: list[tuple[float, float]] = []

    async def tick(self, done, total):
        self.ticks.append((float(done), float(total)))


def _upload(artefacts):
    from src.graph.nodes.output_generator_node import upload_artefacts

    svc, beat, lines = _Svc(), _Beat(), []
    urls, n = asyncio.run(upload_artefacts(svc, "c", "migrations/m-1", artefacts, beat, lines.append))
    return svc.store, urls, n, beat, lines


ROWS = "\n".join(f"AHU-{i:04d},Air handling unit,Level {i % 12},2026-01-{(i % 28) + 1:02d}" for i in range(2000))


def test_json_sql_csv_and_markdown_go_gzip_encoded_and_decode_back_to_the_same_bytes():
    store, urls, n, _, _ = _upload({
        "output.json": '{"tables": {"assets": [' + ",".join(["{\"code\": \"AHU-0001\"}"] * 3000) + "]}}",
        "output.sql": "INSERT INTO assets (code) VALUES ('AHU-0001');\n" * 3000,
        "table_assets.csv": "code,name,location,installed\n" + ROWS,
        "structure.md": "# Structure\n\n- sites\n  - buildings\n" * 50,
    })
    assert n == 4 and set(urls) == {"output.json", "output.sql", "table_assets.csv", "structure.md"}
    for name, ctype in (("output.json", "application/json"), ("output.sql", "text/plain; charset=utf-8"),
                        ("table_assets.csv", "text/csv; charset=utf-8"), ("structure.md", "text/markdown; charset=utf-8")):
        sent, settings = store[name]
        assert settings is not None and settings.content_encoding == "gzip", name
        assert settings.content_type == ctype, name
        assert gzip.decompress(sent) != b"" and sent[:2] == b"\x1f\x8b", name
    raw = "INSERT INTO assets (code) VALUES ('AHU-0001');\n" * 3000
    assert gzip.decompress(store["output.sql"][0]).decode("utf-8") == raw
    assert len(store["output.sql"][0]) < len(raw) // 8, "the SQL script must shrink by an order of magnitude"


def test_the_workbook_and_the_report_go_as_they_are():
    store, urls, n, _, _ = _upload({"output.xlsx": b"PK\x03\x04" + b"\x00" * 500, "migration_report.pdf": b"report text"})
    assert n == 2
    for name in ("output.xlsx", "migration_report.pdf"):
        sent, settings = store[name]
        assert settings is None and sent[:2] != b"\x1f\x8b", name
    assert store["output.xlsx"][0] == b"PK\x03\x04" + b"\x00" * 500


def test_the_beat_counts_the_bytes_actually_sent_and_ends_complete():
    store, _, _, beat, _ = _upload({"output.sql": "SELECT 1;\n" * 5000, "output.xlsx": b"\x00" * 700})
    total_sent = len(store["output.sql"][0]) + len(store["output.xlsx"][0])
    assert beat.ticks and all(t == float(total_sent) for _, t in beat.ticks)
    assert beat.ticks[-1] == (float(total_sent), float(total_sent))
    assert total_sent < 5000 * len("SELECT 1;\n") + 700, "the total is the compressed size, not the raw one"


def test_the_log_line_says_both_sizes_so_the_saving_is_visible():
    _, _, _, _, lines = _upload({"output.sql": "SELECT 1;\n" * 5000})
    line = next(ln for ln in lines if "Uploaded: output.sql" in ln)
    assert "50,000 bytes" in line and "gzip" in line


def test_a_compressed_artefact_still_downloads_under_its_own_name():
    # Before compression these went as application/octet-stream, which a browser saves. Typed
    # as JSON / SQL text they would open in the tab instead — a 75 MB output.json in a viewer.
    store, _, _, _, _ = _upload({"output.json": '{"a": 1}', "table_assets.csv": "code\nAHU-1\n"})
    for name in ("output.json", "table_assets.csv"):
        _, settings = store[name]
        assert settings.content_disposition == f'attachment; filename="{name}"', name


def test_compression_runs_off_the_event_loop(monkeypatch):
    # ~185 MB of artefacts take over a second to gzip; on the loop that is a second in which
    # the worker answers nothing, progress beats included.
    import threading
    from src.graph.nodes import output_generator_node as ogn

    threads: list[str] = []
    real = ogn.encode_artefact

    def spy(filename, content):
        threads.append(threading.current_thread().name)
        return real(filename, content)

    monkeypatch.setattr(ogn, "encode_artefact", spy)
    _upload({"output.sql": "SELECT 1;\n" * 100})
    assert threads and all(t != threading.main_thread().name for t in threads)
