# 📄 OCR Enterprise — PDF to Word (Browser-Only)

![GitHub Pages](https://img.shields.io/badge/GitHub%20Pages-Ready-brightgreen?style=for-the-badge)
![No Server](https://img.shields.io/badge/No%20Server-Required-blue?style=for-the-badge)
![Arabic RTL](https://img.shields.io/badge/Arabic-RTL%20Support-gold?style=for-the-badge)
![Free](https://img.shields.io/badge/Cost-100%25%20Free-green?style=for-the-badge)

> تحويل PDF إلى Word مباشرة في المتصفح — بدون سيرفر، بدون بطاقة، بدون رفع ملفات

**🔗 Live Demo:** `https://YOUR_USERNAME.github.io/ocr-enterprise/`

---

## ✨ الميزات

- ✅ يعمل **100% في المتصفح** — لا Python، لا سيرفر
- ✅ **خصوصية كاملة** — ملفاتك لا تغادر جهازك
- ✅ **عربي + إنجليزي** — OCR ثنائي اللغة مع RTL
- ✅ **معاينة مباشرة** — يعرض كل صفحة أثناء المعالجة
- ✅ **شريط تقدم حي** مع ETA
- ✅ **GitHub Pages** — مجاني 100% بدون بطاقة

---

## 🚀 رفع على GitHub Pages (5 دقائق)

```bash
# 1. أنشئ repo جديد على github.com ثم:
git init
git add index.html README.md
git commit -m "OCR Enterprise - browser-based PDF to Word"
git branch -M main
git remote add origin https://github.com/USERNAME/ocr-enterprise.git
git push -u origin main

# 2. فعّل GitHub Pages:
# Settings → Pages → Source: Deploy from branch → main → / (root) → Save
```

رابطك سيكون: `https://USERNAME.github.io/ocr-enterprise/`

---

## 🛠️ التقنيات المستخدمة

| المكتبة | الاستخدام |
|---------|-----------|
| [PDF.js](https://mozilla.github.io/pdf.js/) | قراءة وعرض PDF |
| [Tesseract.js](https://tesseract.projectnaptha.com/) | OCR عربي + إنجليزي |
| [docx.js](https://docx.js.org/) | بناء ملفات Word |
| [FileSaver.js](https://github.com/eligrey/FileSaver.js/) | تحميل الملف |

---

## ⚠️ ملاحظات

- OCR في المتصفح **أبطأ** من Python — صبر على الكتب الكبيرة
- لا تغلق النافذة أثناء المعالجة
- الكتب > 100 صفحة تأخذ وقتاً أكثر
