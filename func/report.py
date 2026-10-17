"""Streaming report writers (random-access memory stays flat, whatever the size).

Every format is written incrementally: as soon as a target finishes, its line
is appended to disk.  That is what allows scanning gigabyte sized credential
dumps without accumulating results in RAM.
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
from xml.sax.saxutils import escape as xml_escape

__all__ = ["FIELDS", "FORMATS", "ReportWriter", "generate_report"]

FIELDS = (
    "url",
    "username",
    "password",
    "status",
    "wp_version",
    "role",
    "admin",
    "name",
    "capabilities",
    "plugin_install",
    "http_code",
    "waf",
    "error",
    "elapsed",
)

FORMATS = ("csv", "json", "jsonl", "xml", "yaml", "sql", "parquet")

_PARQUET_BATCH = 2_000


def _ordered(record: dict) -> dict:
    out = {field: record.get(field, "") for field in FIELDS}
    for key, value in record.items():  # keep any extra data the worker adds
        if key not in out:
            out[key] = value
    return out


class _BaseWriter:
    def __init__(self, path: str | None) -> None:
        self.path = path
        self.count = 0

    def write(self, record: dict) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - interface
        pass


class _NullWriter(_BaseWriter):
    def write(self, record: dict) -> None:
        self.count += 1


class _CSVWriter(_BaseWriter):
    def __init__(self, path: str) -> None:
        super().__init__(path)
        self._handle = open(path, "w", newline="", encoding="utf-8-sig")
        self._writer = csv.DictWriter(self._handle, fieldnames=list(FIELDS), extrasaction="ignore")
        self._writer.writeheader()

    def write(self, record: dict) -> None:
        self._writer.writerow({key: record.get(key, "") for key in FIELDS})
        self.count += 1

    def close(self) -> None:
        self._handle.close()


class _JSONWriter(_BaseWriter):
    def __init__(self, path: str) -> None:
        super().__init__(path)
        self._handle = open(path, "w", encoding="utf-8")
        self._handle.write("[\n")

    def write(self, record: dict) -> None:
        prefix = "" if self.count == 0 else ",\n"
        self._handle.write(prefix + json.dumps(_ordered(record), ensure_ascii=False))
        self.count += 1

    def close(self) -> None:
        self._handle.write("\n]\n" if self.count else "]\n")
        self._handle.close()


class _JSONLWriter(_BaseWriter):
    def __init__(self, path: str) -> None:
        super().__init__(path)
        self._handle = open(path, "w", encoding="utf-8")

    def write(self, record: dict) -> None:
        self._handle.write(json.dumps(_ordered(record), ensure_ascii=False) + "\n")
        self.count += 1

    def close(self) -> None:
        self._handle.close()


class _XMLWriter(_BaseWriter):
    def __init__(self, path: str) -> None:
        super().__init__(path)
        self._handle = open(path, "w", encoding="utf-8")
        self._handle.write('<?xml version="1.0" encoding="utf-8"?>\n<results>\n')

    def write(self, record: dict) -> None:
        self._handle.write("  <site>\n")
        for key, value in _ordered(record).items():
            tag = key.replace(" ", "_")
            self._handle.write(f"    <{tag}>{xml_escape(str(value) if value is not None else '')}</{tag}>\n")
        self._handle.write("  </site>\n")
        self.count += 1

    def close(self) -> None:
        self._handle.write("</results>\n")
        self._handle.close()


class _YAMLWriter(_BaseWriter):
    def __init__(self, path: str) -> None:
        super().__init__(path)
        import yaml

        self._yaml = yaml
        self._handle = open(path, "w", encoding="utf-8")

    def write(self, record: dict) -> None:
        self._handle.write("---\n")
        self._handle.write(
            self._yaml.safe_dump(
                _ordered(record),
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
        )
        self.count += 1

    def close(self) -> None:
        self._handle.close()


class _SQLWriter(_BaseWriter):
    """Portable SQL text dump (loadable by sqlite3/mysql/psql)."""

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self._handle = open(path, "w", encoding="utf-8")
        columns = ", ".join(f"{field} TEXT" for field in FIELDS)
        self._handle.write("-- wp-checker report\n")
        self._handle.write(f"CREATE TABLE IF NOT EXISTS wp_credentials (\n  {columns}\n);\n")

    @staticmethod
    def _quote(value) -> str:
        if value is None:
            return "NULL"
        text = str(value).replace("'", "''")
        return f"'{text}'"

    def write(self, record: dict) -> None:
        values = ", ".join(self._quote(record.get(field, "")) for field in FIELDS)
        self._handle.write(f"INSERT INTO wp_credentials VALUES ({values});\n")
        self.count += 1

    def close(self) -> None:
        self._handle.close()


class _ParquetWriter(_BaseWriter):
    def __init__(self, path: str) -> None:
        super().__init__(path)
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "parquet output needs pyarrow: pip install pyarrow"
            ) from exc
        self._pa = pa
        self._pq = pq
        self._buffer: list[dict] = []
        self._writer = None
        self._schema = pa.schema([(field, pa.string()) for field in FIELDS])

    def write(self, record: dict) -> None:
        self._buffer.append({key: str(_ordered(record).get(key, "") or "") for key in FIELDS})
        self.count += 1
        if len(self._buffer) >= _PARQUET_BATCH:
            self._flush()

    def _flush(self) -> None:
        if not self._buffer:
            return
        table = self._pa.Table.from_pylist(self._buffer, schema=self._schema)
        if self._writer is None:
            self._writer = self._pq.ParquetWriter(self.path, self._schema)
        self._writer.write_table(table)
        self._buffer.clear()

    def close(self) -> None:
        self._flush()
        if self._writer is not None:
            self._writer.close()
        elif self.path:  # empty report still produces a valid file
            self._writer = self._pq.ParquetWriter(self.path, self._schema)
            self._writer.close()


class _SqliteWriter(_BaseWriter):
    """Optional real sqlite database (kept for backwards compatibility)."""

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self._conn = sqlite3.connect(path)
        columns = ", ".join(f"{field} TEXT" for field in FIELDS)
        self._conn.execute(f"DROP TABLE IF EXISTS wp_credentials")
        self._conn.execute(f"CREATE TABLE wp_credentials ({columns})")
        self._conn.commit()

    def write(self, record: dict) -> None:
        placeholders = ",".join("?" for _ in FIELDS)
        self._conn.execute(
            f"INSERT INTO wp_credentials VALUES ({placeholders})",
            [str(record.get(field, "") or "") for field in FIELDS],
        )
        self.count += 1
        if self.count % 500 == 0:
            self._conn.commit()

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()


_WRITERS = {
    "csv": _CSVWriter,
    "json": _JSONWriter,
    "jsonl": _JSONLWriter,
    "xml": _XMLWriter,
    "yaml": _YAMLWriter,
    "sql": _SQLWriter,
    "sqlite": _SqliteWriter,
    "parquet": _ParquetWriter,
    "none": _NullWriter,
}


class ReportWriter:
    """Facade that picks the right streaming writer for ``fmt``."""

    def __init__(self, path: str | None, fmt: str = "csv") -> None:
        fmt = (fmt or "csv").lower()
        if fmt not in _WRITERS:
            raise ValueError(f"unsupported report format: {fmt} (use one of {', '.join(FORMATS)})")
        if path:
            directory = os.path.dirname(os.path.abspath(path))
            if directory and not os.path.isdir(directory):
                os.makedirs(directory, exist_ok=True)
        self.format = fmt
        self.path = path
        self._writer = _WRITERS[fmt](path) if path else _NullWriter(None)

    def write(self, record: dict) -> None:
        self._writer.write(record)

    def close(self) -> None:
        try:
            self._writer.close()
        except Exception:
            pass

    @property
    def count(self) -> int:
        return self._writer.count

    def __enter__(self) -> "ReportWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def generate_report(results, output_file: str, fmt: str = "csv") -> None:
    """Legacy helper kept for compatibility (still streamed record by record)."""
    writer = ReportWriter(output_file, fmt)
    for record in results:
        writer.write(record)
    writer.close()
