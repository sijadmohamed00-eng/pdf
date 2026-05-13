#!/usr/bin/env python3
"""
app.py  (v2 — full integration)
════════════════════════════════
Enterprise OCR Backend
Integrates: LayoutAnalyzer · AdaptiveBatchExecutor · DocxBuilder
"""

import os, gc, sys, json, uuid, time, shutil, logging, threading, tempfile
from pathlib import Path
from dataclasses import dataclass, field, asdict

from flask import Flask, request, jsonify, send_file, Response, stream_with_context
from flask_cors import CORS
from werkzeug.utils import secure_filename

import fitz
import pytesseract
from PIL import Image

# Local modules
from layout_analyzer import LayoutAnalyzer, pil_from_fitz_page, enhance_for_ocr, ocr_region
from memory_manager  import AdaptiveBatchExecutor, RAMMonitor, system_info
from docx_builder    import DocxBuilder

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("app")

# ─── Paths ───────────────────────────────────────────────────────────────────
UPLOAD_FOLDER = Path(tempfile.gettempdir()) / "ocr_uploads"
OUTPUT_FOLDER = Path(tempfile.gettempdir()) / "ocr_outputs"
CHUNK_FOLDER  = Path(tempfile.gettempdir()) / "ocr_chunks"
for d in (UPLOAD_FOLDER, OUTPUT_FOLDER, CHUNK_FOLDER):
    d.mkdir(parents=True, exist_ok=True)

# ─── Job registry ─────────────────────────────────────────────────────────────
_jobs: dict[str, dict] = {}
_lock = threading.Lock()

# ─── Config defaults (overridable per-job) ────────────────────────────────────
DEFAULT_BATCH   = 10
DEFAULT_WORKERS = 4
DEFAULT_DPI     = 200
DEFAULT_LANG    = "ara+eng"
CHUNK_SIZE_MB   = 10
SIMPLE_MAX_MB   = 100
MAX_FILE_MB     = 500

# ─────────────────────────────────────────────────────────────────────────────
# Per-page processor  (called inside AdaptiveBatchExecutor)
# ─────────────────────────────────────────────────────────────────────────────

def make_page_processor(pdf_path: str, tmp_dir: Path, cfg: dict):
    """Return a closure that processes a single page."""
    analyzer = LayoutAnalyzer(dpi=cfg["dpi"])
    lang     = cfg["lang"]

    def _process(page_num: int) -> dict:
        doc = None
        try:
            # ── Layout analysis ─────────────────────────────────────────────
            layout = analyzer.analyze(pdf_path, page_num)

            # ── Render page to PIL ──────────────────────────────────────────
            doc = fitz.open(pdf_path)
            page_obj = doc[page_num]
            pil_img  = pil_from_fitz_page(page_obj, dpi=cfg["dpi"])
            doc.close(); doc = None

            # Save page image for DOCX embedding
            img_path = str(tmp_dir / f"page_{page_num:04d}.jpg")
            pil_img.save(img_path, "JPEG", quality=82)

            # ── Decide text source ──────────────────────────────────────────
            if len(layout.raw_text.strip()) > 60:
                # Native text is good enough
                final_text = layout.raw_text
            else:
                # Run full OCR on regions
                enhanced = enhance_for_ocr(pil_img)
                parts = []
                for region in layout.regions:
                    if region.kind == "image":
                        continue
                    ocr_txt = ocr_region(enhanced, region.bbox, lang=lang)
                    if ocr_txt:
                        parts.append(ocr_txt)
                final_text = "\n\n".join(parts)
                del enhanced

            del pil_img
            gc.collect()

            return {
                "page_num" : page_num,
                "text"     : final_text,
                "is_rtl"   : layout.is_rtl,
                "has_image": layout.has_images,
                "image_path": img_path,
                "num_cols" : layout.num_cols,
                "has_table": layout.has_tables,
                "error"    : None,
            }

        except Exception as exc:
            log.error("Page %d: %s", page_num, exc)
            if doc:
                doc.close()
            gc.collect()
            return {
                "page_num": page_num, "text": "", "is_rtl": False,
                "has_image": False, "image_path": None,
                "num_cols": 1, "has_table": False, "error": str(exc),
            }

    return _process


# ─────────────────────────────────────────────────────────────────────────────
# Background job runner
# ─────────────────────────────────────────────────────────────────────────────

def run_job(job_id: str, pdf_path: str, filename: str, cfg: dict):

    def upd(**kw):
        with _lock:
            _jobs[job_id].update(kw)

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"job_{job_id}_"))
    monitor = RAMMonitor(interval=8.0, job_id=job_id)
    monitor.start()

    try:
        upd(status="processing")

        # Page count
        with fitz.open(pdf_path) as d:
            total = len(d)
        upd(total_pages=total)
        log.info("Job %s | %s | %d pages | cfg=%s", job_id, filename, total, cfg)

        # Progress callback
        def on_progress(done, total_pg):
            upd(processed_pages=done)

        # Build page processor
        proc_fn = make_page_processor(pdf_path, tmp_dir, cfg)

        # Run adaptive executor
        executor = AdaptiveBatchExecutor(
            process_fn      = proc_fn,
            total_pages     = total,
            initial_batch   = cfg.get("batch", DEFAULT_BATCH),
            initial_workers = cfg.get("workers", DEFAULT_WORKERS),
            dpi             = cfg.get("dpi",  DEFAULT_DPI),
            progress_cb     = on_progress,
            spill_dir       = tmp_dir / "spill",
        )
        results = executor.run()

        # Detect dominant doc language for cover
        rtl_count = sum(1 for r in results if r.get("is_rtl"))
        doc_rtl   = rtl_count > (total / 2)

        upd(status="building")

        # Build DOCX
        out_name = Path(filename).stem + "_ocr.docx"
        out_path = str(OUTPUT_FOLDER / f"{job_id}_{out_name}")

        builder = DocxBuilder(
            output_path   = out_path,
            original_name = filename,
            embed_images  = cfg.get("embed_images", True),
            page_breaks   = cfg.get("page_breaks",  True),
            default_rtl   = doc_rtl,
        )
        builder.build_from_results(results, lang=cfg.get("lang", DEFAULT_LANG))

        file_size = os.path.getsize(out_path)
        upd(
            status      = "done",
            output_path = out_path,
            file_size   = file_size,
            peak_ram_mb = round(monitor.peak_mb, 1),
        )
        log.info("Job %s done | size=%.1f MB | peak_RAM=%.0f MB",
                 job_id, file_size / (1024*1024), monitor.peak_mb)

    except Exception as exc:
        log.exception("Job %s failed", job_id)
        upd(status="error", error_msg=str(exc))

    finally:
        monitor.stop()
        shutil.rmtree(tmp_dir, ignore_errors=True)
        try:
            os.unlink(pdf_path)
        except OSError:
            pass
        gc.collect()


# ─────────────────────────────────────────────────────────────────────────────
# Flask app
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)
CORS(app)
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_MB * 1024 * 1024


def _spawn(job_id, pdf_path, filename, cfg):
    t = threading.Thread(target=run_job, args=(job_id, pdf_path, filename, cfg), daemon=True)
    t.start()


def _new_job(filename: str) -> tuple[str, dict]:
    job_id = str(uuid.uuid4())
    state  = dict(
        job_id=job_id, filename=filename,
        total_pages=0, processed_pages=0,
        status="queued", error_msg="",
        output_path="", file_size=0,
        peak_ram_mb=0, created_at=time.time(),
    )
    with _lock:
        _jobs[job_id] = state
    return job_id, state


# ── Simple upload ─────────────────────────────────────────────────────────────
@app.route("/api/upload/simple", methods=["POST"])
def upload_simple():
    pdf = request.files.get("pdf")
    if not pdf:
        return jsonify({"error": "No file"}), 400

    cfg = {
        "batch":        int(request.form.get("batch",   DEFAULT_BATCH)),
        "workers":      int(request.form.get("workers", DEFAULT_WORKERS)),
        "dpi":          int(request.form.get("dpi",     DEFAULT_DPI)),
        "lang":         request.form.get("lang",    DEFAULT_LANG),
        "embed_images": request.form.get("embed_images", "true").lower() == "true",
        "page_breaks":  request.form.get("page_breaks",  "true").lower() == "true",
    }

    fname  = secure_filename(pdf.filename or "book.pdf")
    uid    = str(uuid.uuid4())
    path   = str(UPLOAD_FOLDER / f"{uid}_{fname}")
    pdf.save(path)

    job_id, _ = _new_job(fname)
    _spawn(job_id, path, fname, cfg)
    return jsonify({"job_id": job_id})


# ── Chunked upload ────────────────────────────────────────────────────────────
@app.route("/api/upload/init", methods=["POST"])
def upload_init():
    data  = request.get_json(force=True)
    fname = secure_filename(data.get("filename", "book.pdf"))
    size  = int(data.get("size", 0))
    uid   = str(uuid.uuid4())
    chunk_sz = CHUNK_SIZE_MB * 1024 * 1024
    total_ch = max(1, -(-size // chunk_sz))

    (CHUNK_FOLDER / uid).mkdir(parents=True, exist_ok=True)
    meta = {"upload_id": uid, "filename": fname, "file_size": size,
            "chunk_size": chunk_sz, "total_chunks": total_ch,
            "cfg": data.get("cfg", {})}
    (CHUNK_FOLDER / uid / "meta.json").write_text(json.dumps(meta))
    return jsonify(meta)


@app.route("/api/upload/chunk", methods=["POST"])
def upload_chunk():
    uid   = request.form.get("upload_id")
    idx   = int(request.form.get("chunk_index", 0))
    chunk = request.files.get("chunk")
    if not uid or not chunk:
        return jsonify({"error": "Missing params"}), 400
    dest = CHUNK_FOLDER / uid
    if not dest.exists():
        return jsonify({"error": "Invalid upload_id"}), 404
    chunk.save(str(dest / f"chunk_{idx:06d}"))
    return jsonify({"received": idx})


@app.route("/api/upload/finalize", methods=["POST"])
def upload_finalize():
    data = request.get_json(force=True)
    uid  = data.get("upload_id")
    dest = CHUNK_FOLDER / uid
    if not dest.exists():
        return jsonify({"error": "Invalid upload_id"}), 404

    meta = json.loads((dest / "meta.json").read_text())
    fname = meta["filename"]

    final_path = str(UPLOAD_FOLDER / f"{uid}_{fname}")
    with open(final_path, "wb") as out:
        for c in sorted(dest.glob("chunk_*")):
            out.write(c.read_bytes())
    shutil.rmtree(dest, ignore_errors=True)

    cfg = meta.get("cfg", {})
    cfg.setdefault("batch",       DEFAULT_BATCH)
    cfg.setdefault("workers",     DEFAULT_WORKERS)
    cfg.setdefault("dpi",         DEFAULT_DPI)
    cfg.setdefault("lang",        DEFAULT_LANG)
    cfg.setdefault("embed_images", True)
    cfg.setdefault("page_breaks",  True)

    job_id, _ = _new_job(fname)
    _spawn(job_id, final_path, fname, cfg)
    return jsonify({"job_id": job_id})


# ── Status & SSE ──────────────────────────────────────────────────────────────
@app.route("/api/job/<jid>")
def job_status(jid):
    with _lock:
        job = _jobs.get(jid)
    if not job:
        return jsonify({"error": "Not found"}), 404
    total = job["total_pages"] or 1
    pct   = round((job["processed_pages"] / total) * 100)
    return jsonify({**job, "progress": pct})


@app.route("/api/job/<jid>/stream")
def job_stream(jid):
    def gen():
        while True:
            with _lock:
                job = _jobs.get(jid)
            if not job:
                yield f"data: {json.dumps({'error':'not_found'})}\n\n"; break
            total = job["total_pages"] or 1
            pct   = round((job["processed_pages"] / total) * 100)
            yield f"data: {json.dumps({**job,'progress':pct})}\n\n"
            if job["status"] in ("done", "error"):
                break
            time.sleep(1.5)
    return Response(stream_with_context(gen()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})


@app.route("/api/job/<jid>/download")
def job_download(jid):
    with _lock:
        job = _jobs.get(jid)
    if not job or job["status"] != "done":
        return jsonify({"error": "Not ready"}), 404
    path = job["output_path"]
    if not os.path.exists(path):
        return jsonify({"error": "File missing"}), 500
    name = Path(job["filename"]).stem + "_ocr.docx"
    return send_file(path, as_attachment=True, download_name=name,
                     mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


# ── Health ────────────────────────────────────────────────────────────────────
@app.route("/api/health")
def health():
    tess = False
    try:
        pytesseract.get_tesseract_version(); tess = True
    except Exception:
        pass
    return jsonify({"status": "ok", "tesseract": tess, **system_info()})


if __name__ == "__main__":
    log.info("OCR Enterprise Server → http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
