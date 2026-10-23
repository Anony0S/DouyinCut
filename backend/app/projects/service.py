import json
import shutil
import time
from pathlib import Path

from pydantic import Field

from ..core.domain import Model
from ..ai.roughcut import DEFAULT_INSTRUCTIONS


class ProjectDraft(Model):
    product_name: str = Field(default="", max_length=200)
    product_info: str = Field(default="", max_length=4000)
    instructions: str = Field(default=DEFAULT_INSTRUCTIONS, max_length=4000)
    target_duration_us: int = Field(default=60_000_000, ge=15_000_000, le=180_000_000)


# Each source video is one project. Read current revisions, never all revision bodies in Python.
LATEST = """
WITH latest AS (
 SELECT o.* FROM objects o JOIN
 (SELECT kind,id,MAX(revision) revision FROM objects GROUP BY kind,id) r
 USING(kind,id,revision)
), source_plans AS (
 SELECT *,json_extract(body,'$.clips[0].source_asset_id') asset_id FROM latest WHERE kind='plan'
), related_jobs AS (
 SELECT j.*,COALESCE(json_extract(j.payload,'$.asset_id'),p.asset_id) asset_id FROM jobs j
 LEFT JOIN source_plans p ON p.id=json_extract(j.payload,'$.plan_id')
 WHERE j.kind IN ('analyze','preview','export','jianying')
)
"""


def project_list(store, page=1, page_size=20, search="", status="all"):
    query = LATEST + """, summaries AS (
 SELECT a.id, json_extract(a.body,'$.name') name,
 json_extract(a.body,'$.duration_us') duration_us,
 json_extract(a.body,'$.width') width,json_extract(a.body,'$.height') height,
 COALESCE(json_extract(d.body,'$.draft.product_name'),
  (SELECT json_extract(payload,'$.product_name') FROM related_jobs WHERE asset_id=a.id AND kind='analyze'
   ORDER BY created DESC,id DESC LIMIT 1),'') product_name,
 MAX(a.created,COALESCE(d.created,0),
  COALESCE((SELECT MAX(created) FROM source_plans WHERE asset_id=a.id),0),
  COALESCE((SELECT MAX(updated) FROM related_jobs WHERE asset_id=a.id),0)) updated_at,
 (SELECT id FROM source_plans WHERE asset_id=a.id ORDER BY created DESC,id DESC LIMIT 1) plan_id,
 (SELECT body FROM source_plans WHERE asset_id=a.id ORDER BY created DESC,id DESC LIMIT 1) plan_body,
 CASE
  WHEN EXISTS(SELECT 1 FROM related_jobs WHERE asset_id=a.id AND state IN
    ('queued','running','pause_requested','cancel_requested')) THEN 'processing'
  WHEN EXISTS(SELECT 1 FROM related_jobs WHERE asset_id=a.id AND state='paused') THEN 'paused'
  WHEN (SELECT state FROM related_jobs WHERE asset_id=a.id ORDER BY updated DESC,id DESC LIMIT 1)='failed' THEN 'failed'
  WHEN (SELECT state FROM related_jobs WHERE asset_id=a.id ORDER BY updated DESC,id DESC LIMIT 1)='cancelled' THEN 'cancelled'
  WHEN EXISTS(SELECT 1 FROM source_plans p JOIN latest e ON e.kind='export'
    AND json_extract(e.body,'$.plan_id')=p.id AND json_extract(e.body,'$.revision')=p.revision
    WHERE p.id=(SELECT id FROM source_plans WHERE asset_id=a.id ORDER BY created DESC,id DESC LIMIT 1)) THEN 'completed'
  ELSE 'pending' END status
 FROM latest a LEFT JOIN latest d ON d.kind='project' AND d.id=a.id WHERE a.kind='asset'
), filtered AS (
 SELECT * FROM summaries WHERE (?='' OR instr(lower(name),lower(?))>0 OR instr(lower(product_name),lower(?))>0)
 AND (?='all' OR status=?)
)
"""
    args = (search.strip(), search.strip(), search.strip(), status, status)
    with store.connection() as conn:
        conn.execute("BEGIN")
        total = conn.execute(query + "SELECT COUNT(*) FROM filtered", args).fetchone()[0]
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = conn.execute(query + "SELECT * FROM filtered ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?",
                            (*args, page_size, (page - 1) * page_size)).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        raw = item.pop("plan_body")
        item["output_duration_us"] = (sum(c["source_end_us"] - c["source_start_us"] for c in json.loads(raw)["clips"])
                                      if raw else None)
        items.append(item)
    return {"items": items, "total": total, "page": page, "page_size": page_size, "pages": pages}


def save_draft(store, asset_id, draft):
    store.get("asset", asset_id)
    data = {"id": asset_id, "draft": draft.model_dump()}
    with store.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        revision = conn.execute("SELECT COALESCE(MAX(revision),0)+1 FROM objects WHERE kind='project' AND id=?",
                                (asset_id,)).fetchone()[0]
        conn.execute("INSERT INTO objects VALUES ('project',?,?,?,?)",
                     (asset_id, revision, json.dumps(data, ensure_ascii=False), time.time()))
    return draft.model_dump()


def project_detail(store, asset_id):
    with store.connection() as conn:
        conn.execute("BEGIN")
        asset = conn.execute("SELECT body FROM objects WHERE kind='asset' AND id=? ORDER BY revision DESC LIMIT 1",
                             (asset_id,)).fetchone()
        if asset is None:
            raise KeyError(asset_id)
        plans = [json.loads(r[0]) for r in conn.execute(LATEST +
                 "SELECT body FROM source_plans WHERE asset_id=? ORDER BY created DESC,id DESC", (asset_id,))]
        candidates = [json.loads(r[0]) for r in conn.execute(LATEST +
                      "SELECT body FROM latest WHERE kind='candidate' AND json_extract(body,'$.source_asset_id')=?",
                      (asset_id,))]
        exports = [json.loads(r[0]) for r in conn.execute(LATEST +
                   "SELECT e.body FROM latest e JOIN source_plans p ON p.id=json_extract(e.body,'$.plan_id') "
                   "WHERE e.kind='export' AND p.asset_id=? ORDER BY e.created DESC,e.id DESC", (asset_id,))]
        jobs = [store.decode_job(r) for r in conn.execute(LATEST +
                "SELECT * FROM related_jobs WHERE asset_id=? ORDER BY "
                "CASE WHEN state IN ('queued','running','pause_requested','paused','cancel_requested') THEN 0 ELSE 1 END,"
                "created DESC,id DESC LIMIT 50", (asset_id,))]
        # Keep jobs' public contract; the CTE asset_id is only used to filter on the server.
        for job in jobs:
            job.pop("asset_id", None)
        latest_analysis = conn.execute("SELECT * FROM jobs WHERE kind='analyze' AND json_extract(payload,'$.asset_id')=? "
                                       "ORDER BY created DESC,id DESC LIMIT 1", (asset_id,)).fetchone()
        latest_analysis = store.decode_job(latest_analysis) if latest_analysis else None
        row = conn.execute("SELECT body FROM objects WHERE kind='project' AND id=? ORDER BY revision DESC LIMIT 1",
                           (asset_id,)).fetchone()
        draft = (json.loads(row[0])["draft"] if row else
                 {field: latest_analysis["payload"][field] for field in ProjectDraft.model_fields
                  if latest_analysis and field in latest_analysis["payload"]})
    return {"asset": json.loads(asset[0]), "draft": ProjectDraft.model_validate(draft).model_dump(),
            "plans": plans, "candidates": candidates, "artifacts": exports, "jobs": jobs,
            "latest_analysis": latest_analysis}


def _delete_paths(paths, roots):
    allowed = [Path(root).resolve() for root in roots]
    failed = []
    for raw_path in paths:
        path = Path(raw_path).resolve()
        if any(path != root and root in path.parents for root in allowed):
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                failed.append(str(path))
    return failed


def _ids_sql(values):
    return ",".join("?" for _ in values)


def delete_plan(store, plan_id, data_dir):
    paths = []
    with store.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        revisions = conn.execute("SELECT body FROM objects WHERE kind='plan' AND id=? ORDER BY revision", (plan_id,)).fetchall()
        if not revisions:
            raise KeyError(plan_id)
        plan_bodies = [json.loads(row[0]) for row in revisions]
        asset_id = plan_bodies[-1]["clips"][0]["source_asset_id"]
        active = conn.execute("SELECT 1 FROM jobs WHERE kind IN ('preview','export','jianying') "
                              "AND json_extract(payload,'$.plan_id')=? "
                              "AND state IN ('queued','running','pause_requested','paused','cancel_requested') LIMIT 1",
                              (plan_id,)).fetchone()
        if active:
            raise ValueError("该方案有正在处理的预览或导出任务，请完成或取消任务后再删除")
        exports = conn.execute("SELECT body FROM objects WHERE kind='export' "
                               "AND json_extract(body,'$.plan_id')=?", (plan_id,)).fetchall()
        paths.extend(json.loads(row[0])["path"] for row in exports)
        jobs = conn.execute("SELECT id FROM jobs WHERE kind IN ('preview','export','jianying') "
                            "AND json_extract(payload,'$.plan_id')=?", (plan_id,)).fetchall()
        job_ids = [row[0] for row in jobs]
        candidate_ids = {clip["candidate_id"] for body in plan_bodies for clip in body["clips"]}
        product_ids = {body["product_id"] for body in plan_bodies}
        shared_candidates = set()
        for other in conn.execute("SELECT body FROM objects WHERE kind='plan' AND id<>?", (plan_id,)):
            if any(clip["candidate_id"] in candidate_ids for clip in json.loads(other[0])["clips"]):
                shared_candidates.update(clip["candidate_id"] for clip in json.loads(other[0])["clips"]
                                         if clip["candidate_id"] in candidate_ids)
        candidate_ids -= shared_candidates
        for product_id in list(product_ids):
            if conn.execute("SELECT 1 FROM objects WHERE kind='plan' AND id<>? "
                            "AND json_extract(body,'$.product_id')=? LIMIT 1", (plan_id, product_id)).fetchone():
                product_ids.remove(product_id)
        conn.execute("DELETE FROM objects WHERE kind='plan' AND id=?", (plan_id,))
        conn.execute("DELETE FROM objects WHERE kind='export' AND json_extract(body,'$.plan_id')=?", (plan_id,))
        if candidate_ids:
            conn.execute(f"DELETE FROM objects WHERE kind='candidate' AND id IN ({_ids_sql(candidate_ids)})",
                         tuple(candidate_ids))
        if product_ids:
            conn.execute(f"DELETE FROM objects WHERE kind='product' AND id IN ({_ids_sql(product_ids)})",
                         tuple(product_ids))
        if job_ids:
            conn.execute(f"DELETE FROM attempts WHERE job_id IN ({_ids_sql(job_ids)})", tuple(job_ids))
            conn.execute(f"DELETE FROM jobs WHERE id IN ({_ids_sql(job_ids)})", tuple(job_ids))
            for job_id in job_ids:
                conn.execute("DELETE FROM cache WHERE key IN (?,?,?)",
                             (f"job-result:{job_id}", f"roughcut-timestamps-v1:{job_id}",
                              f"roughcut-transcript:{job_id}"))
    cleanup_pending = _delete_paths(paths, [Path(data_dir) / "previews", Path(data_dir) / "exports"])
    return {"deleted_plan_id": plan_id, "source_asset_id": asset_id,
            "cleanup_pending": cleanup_pending}


def delete_project(store, asset_id, data_dir):
    data_dir = Path(data_dir).resolve()
    paths = []
    with store.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        asset_row = conn.execute("SELECT body FROM objects WHERE kind='asset' AND id=? "
                                 "ORDER BY revision DESC LIMIT 1", (asset_id,)).fetchone()
        if asset_row is None:
            raise KeyError(asset_id)
        asset = json.loads(asset_row[0])
        plan_rows = conn.execute(LATEST + "SELECT id,body FROM source_plans WHERE asset_id=?", (asset_id,)).fetchall()
        plan_ids = [row[0] for row in plan_rows]
        clauses = ["(kind='analyze' AND json_extract(payload,'$.asset_id')=?)",
                   "(kind='import' AND json_extract(result,'$.asset_id')=?)"]
        args = [asset_id, asset_id]
        if plan_ids:
            clauses.append(f"(kind IN ('preview','export','jianying') AND json_extract(payload,'$.plan_id') IN ({_ids_sql(plan_ids)}))")
            args.extend(plan_ids)
        related = " OR ".join(clauses)
        active = conn.execute("SELECT 1 FROM jobs WHERE (" + related + ") AND state IN "
                              "('queued','running','pause_requested','paused','cancel_requested') LIMIT 1", args).fetchone()
        if active:
            raise ValueError("该项目仍有正在处理的任务，请完成或取消任务后再删除")
        job_rows = conn.execute("SELECT id,payload FROM jobs WHERE " + related, args).fetchall()
        job_ids = [row[0] for row in job_rows]
        paths.append(asset["path"])
        paths.append(str(data_dir / "previews" / "assets" / f"{asset_id}.mp4"))
        paths.extend(str(data_dir / "analysis" / job_id) for job_id in job_ids
                     if conn.execute("SELECT 1 FROM jobs WHERE id=? AND kind='analyze'", (job_id,)).fetchone())
        export_rows = conn.execute("SELECT body FROM objects WHERE kind='export' "
                                   "AND json_extract(body,'$.plan_id') IN (" + _ids_sql(plan_ids) + ")",
                                   plan_ids).fetchall() if plan_ids else []
        paths.extend(json.loads(row[0])["path"] for row in export_rows)
        if job_ids:
            for job_id in job_ids:
                conn.execute("DELETE FROM attempts WHERE job_id=?", (job_id,))
                conn.execute("DELETE FROM cache WHERE key IN (?,?,?)",
                             (f"job-result:{job_id}", f"roughcut-timestamps-v1:{job_id}",
                              f"roughcut-transcript:{job_id}"))
            conn.execute(f"DELETE FROM jobs WHERE id IN ({_ids_sql(job_ids)})", job_ids)
        conn.execute("DELETE FROM objects WHERE kind IN ('asset','project','transcript') AND "
                     "(id=? OR json_extract(body,'$.source_asset_id')=? OR "
                     "json_extract(body,'$.segment.source_asset_id')=?)", (asset_id, asset_id, asset_id))
        if plan_ids:
            placeholders = _ids_sql(plan_ids)
            product_ids = [row[0] for row in conn.execute(
                "SELECT DISTINCT json_extract(body,'$.product_id') FROM objects WHERE kind='plan' "
                f"AND id IN ({placeholders})", plan_ids)]
            product_ids = [product_id for product_id in product_ids if not conn.execute(
                "SELECT 1 FROM objects WHERE kind='plan' AND id NOT IN (" + placeholders + ") "
                "AND json_extract(body,'$.product_id')=? LIMIT 1", (*plan_ids, product_id)).fetchone()]
            conn.execute(f"DELETE FROM objects WHERE kind IN ('plan','export') AND "
                         f"(id IN ({placeholders}) OR json_extract(body,'$.plan_id') IN ({placeholders}))",
                         (*plan_ids, *plan_ids))
            if product_ids:
                conn.execute(f"DELETE FROM objects WHERE kind='product' AND id IN ({_ids_sql(product_ids)})",
                             product_ids)
        candidate_rows = conn.execute("SELECT id FROM objects WHERE kind='candidate' AND "
                                      "json_extract(body,'$.source_asset_id')=? GROUP BY id", (asset_id,)).fetchall()
        candidate_ids = [row[0] for row in candidate_rows]
        if candidate_ids and plan_ids:
            placeholders = _ids_sql(plan_ids)
            candidate_ids = [candidate_id for candidate_id in candidate_ids if not conn.execute(
                "SELECT 1 FROM objects WHERE kind='plan' AND id NOT IN (" + placeholders + ") "
                "AND EXISTS (SELECT 1 FROM json_each(objects.body,'$.clips') c "
                "WHERE json_extract(c.value,'$.candidate_id')=?) LIMIT 1", (*plan_ids, candidate_id)).fetchone()]
        if candidate_ids:
            conn.execute(f"DELETE FROM objects WHERE kind='candidate' AND id IN ({_ids_sql(candidate_ids)})",
                         candidate_ids)
        duplicate = conn.execute("SELECT 1 FROM objects WHERE kind='asset' AND id<>? "
                                 "AND json_extract(body,'$.sha256')=? LIMIT 1",
                                 (asset_id, asset["sha256"])).fetchone()
        if not duplicate:
            conn.execute("DELETE FROM cache WHERE key LIKE ?", (f"qwen-audio-v1:{asset['sha256']}:%",))
    cleanup_pending = _delete_paths(paths, [data_dir / "recordings" / "imports", data_dir / "analysis",
                                            data_dir / "previews", data_dir / "exports"])
    return {"deleted_asset_id": asset_id, "deleted_plans": len(plan_ids),
            "oss_audio_retained": True, "cleanup_pending": cleanup_pending}


def job_summary(store):
    with store.connection() as conn:
        row = conn.execute("SELECT COUNT(*) total,COALESCE(MAX(updated),0) updated_at,"
                           "COALESCE(SUM(state IN ('queued','running','pause_requested','cancel_requested')),0) active FROM jobs").fetchone()
    return dict(row)
