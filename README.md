# 📄 OCR Enterprise — PDF to Word Converter

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?style=for-the-badge&logo=python"/>
  <img src="https://img.shields.io/badge/Flask-3.0-black?style=for-the-badge&logo=flask"/>
  <img src="https://img.shields.io/badge/Tesseract-OCR-red?style=for-the-badge"/>
  <img src="https://img.shields.io/badge/Arabic-RTL%20Support-green?style=for-the-badge"/>
  <img src="https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge"/>
</p>

> **نظام OCR متقدم لتحويل ملفات PDF (كتب كاملة) إلى Word مع دعم العربية والإنجليزية**  
> Enterprise-grade PDF→DOCX converter with Arabic/English OCR, batch processing, and smart memory management.

---

## ✨ Features

| Feature | Details |
|---------|---------|
| 🔄 **Batch Processing** | Processes books in configurable page batches (default 10) |
| 🧠 **Smart Memory** | Adaptive RAM management — safe for 500+ page books |
| 🌍 **Arabic + English** | Full RTL support, Arabic reshaping, PSM1 + PSM3 OCR modes |
| 📐 **Layout Engine** | Detects columns, tables, headers, footers, images |
| ⬆️ **Chunked Upload** | Auto chunked upload for files > 100 MB (up to 500 MB) |
| 📊 **Live Progress** | Real-time SSE progress bar with ETA |
| 📝 **Clean DOCX** | Page breaks, RTL paragraphs, embedded images, table formatting |

---

## 🗂️ Project Structure

```
ocr_enterprise/
├── backend/
│   ├── app.py               ← Flask API server (entry point)
│   ├── layout_analyzer.py   ← PDF layout engine (columns, regions, OCR)
│   ├── memory_manager.py    ← Adaptive batch executor + RAM monitor
│   └── docx_builder.py      ← Word document builder (RTL, tables, images)
├── frontend/
│   └── index.html           ← Single-file web UI (no dependencies)
├── requirements.txt
├── setup.py                 ← Auto-installer
├── .gitignore
└── README.md
```

---

## 🚀 Quick Start

### 1. Clone

```bash
git clone https://github.com/YOUR_USERNAME/ocr_enterprise.git
cd ocr_enterprise
```

### 2. Install dependencies

```bash
python setup.py
```

This automatically installs:
- All Python packages from `requirements.txt`
- Tesseract-OCR + Arabic language data (Linux/macOS)

> **Windows:** Download Tesseract from [UB-Mannheim](https://github.com/UB-Mannheim/tesseract/wiki), install, then add to PATH.

### 3. Run the server

```bash
cd backend
python app.py
```

Server starts at `http://localhost:5000`

### 4. Open the UI

Open `frontend/index.html` in any modern browser.

---

## ⚙️ Configuration

Settings are available in the UI and passed to the API:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `batch` | `10` | Pages per processing batch |
| `workers` | `4` | Parallel OCR threads |
| `dpi` | `200` | Render resolution (150/200/300) |
| `lang` | `ara+eng` | Tesseract language string |
| `embed_images` | `true` | Embed page images in DOCX |
| `page_breaks` | `true` | Insert page break after each PDF page |

---

## 🌐 API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET  /api/health` | GET | Server + Tesseract status |
| `POST /api/upload/simple` | POST | Upload PDF ≤ 100 MB |
| `POST /api/upload/init` | POST | Init chunked upload |
| `POST /api/upload/chunk` | POST | Send a chunk |
| `POST /api/upload/finalize` | POST | Assemble + start job |
| `GET  /api/job/<id>` | GET | Job status + progress |
| `GET  /api/job/<id>/stream` | GET | SSE live progress stream |
| `GET  /api/job/<id>/download` | GET | Download output DOCX |

---

## 🧠 Architecture

```
PDF Upload
    │
    ▼
AdaptiveBatchExecutor        ← memory_manager.py
    │  (batches of N pages)
    ▼
LayoutAnalyzer (per page)    ← layout_analyzer.py
    ├── Column detection
    ├── Region classification (text/image/table/header/footer)
    ├── Native text extraction (PyMuPDF)
    └── OCR fallback (Tesseract PSM1 + PSM3)
    │
    ▼
SpillManager                 ← memory_manager.py
    │  (disk-spill when RAM > 50 MB buffer)
    ▼
DocxBuilder                  ← docx_builder.py
    ├── Arabic reshaping + bidi
    ├── RTL paragraph formatting
    ├── Table reconstruction
    ├── Image embedding
    └── Page breaks
    │
    ▼
Output: book_ocr.docx
```

---

## 📋 Requirements

- Python 3.10+
- Tesseract OCR 5.x
- Tesseract language packs: `ara`, `eng`

### Python Packages

```
flask, flask-cors, PyMuPDF, pytesseract, Pillow,
python-docx, numpy, arabic-reshaper, python-bidi,
werkzeug, psutil, lxml, scipy
```

---

## 🛠️ Troubleshooting

**Tesseract not found:**
```bash
# Linux
sudo apt install tesseract-ocr tesseract-ocr-ara

# macOS
brew install tesseract && brew install tesseract-lang

# Windows — set path manually:
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
```

**Memory issues with large books:**
- Reduce DPI to 150
- Set batch size to 5
- Set workers to 2

**Arabic text garbled:**
```bash
pip install arabic-reshaper python-bidi
```

---

## 📄 License

MIT License — free to use, modify, and distribute.

---

## 🤝 Contributing

Pull requests welcome. For major changes, open an issue first.

```bash
git checkout -b feature/your-feature
git commit -m "Add your feature"
git push origin feature/your-feature
```
