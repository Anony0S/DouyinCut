"""Read-only snapshot of analyze jobs, cloud operations and ASR chunk caches in an isolated work directory."""

import argparse
import datetime
import json
from pathlib import Path
import sqlite3

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("work", type=Path, help="Isolated work directory created by prepare-windows-cloud-exit-check.py")
args = parser.parse_args()
database = args.work.resolve() / "database/douyincut.sqlite3"
if not database.is_file():
    raise SystemExit(f"Missing database: {database}")

# mode=ro never creates or migrates the file, so the running app keeps exclusive ownership of writes.
conn = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=10)
conn.row_factory = sqlite3.Row


def clock(value):
    return datetime.datetime.fromtimestamp(value).strftime("%H:%M:%S") if value else "-"


print(f"Snapshot {datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {database}\n")
print("Analyze jobs")
for job in conn.execute("SELECT id,state,progress,attempts,error,updated FROM jobs WHERE kind='analyze' ORDER BY created"):
    print(f"  {job['id']}  {job['state']:<16} progress={job['progress']:.0%}  attempts={job['attempts']}  "
          f"updated={clock(job['updated'])}" + (f"\n    error: {job['error']}" if job["error"] else ""))

print("\nCloud operations")
operations = conn.execute("SELECT job_id,stage,attempt,status,reason,cache_key,started,updated "
                          "FROM cloud_operations ORDER BY started").fetchall()
if not operations:
    print("  (none)")
for op in operations:
    chunk = ":".join(op["cache_key"].split(":")[-2:]) if op["cache_key"] else "-"
    print(f"  {clock(op['started'])}  attempt={op['attempt']}  {op['stage']:<10} {op['status']:<16} "
          f"reason={op['reason'] or '-':<13} chunk_us={chunk}")

print("\nASR chunk cache (start_us:end_us)")
chunks = conn.execute("SELECT key,body FROM cache WHERE key LIKE 'qwen-audio-v1:%' ORDER BY key").fetchall()
if not chunks:
    print("  (none)")
for row in sorted(chunks, key=lambda r: int(r["key"].split(":")[-2])):
    body = json.loads(row["body"])
    detail = f"  sentences={len(body.get('segments') or [])}" if body.get("status") == "succeeded" else ""
    print(f"  {':'.join(row['key'].split(':')[-2:]):<22} {body.get('status')}{detail}")

print("\nOSS uploads")
uploads = conn.execute("SELECT key,body FROM cache WHERE key LIKE 'oss-upload:%' ORDER BY key").fetchall()
if not uploads:
    print("  (none)")
for row in uploads:
    print(f"  {row['key'].rsplit('-', 2)[-2]}-{row['key'].rsplit('-', 1)[-1]:<20} {json.loads(row['body']).get('status')}")
conn.close()
