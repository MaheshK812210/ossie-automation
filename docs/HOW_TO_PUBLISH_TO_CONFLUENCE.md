# How to publish this guide to Confluence

You have three easy options. Pick one.

## Option A — Import Word (simplest for most teams)

1. Open Confluence → create a page (e.g. *Ossie Semantic Model Builder*).
2. `···` → **Import** → **Word document** (wording varies by Confluence Cloud vs Data Center).
3. Upload:
   `docs/Ossie_Semantic_Model_Builder_Design_and_Capabilities.docx`
4. Images are embedded in the DOCX and should import with the page.
5. Review headings; adjust page labels / parent page as needed.

## Option B — Markdown + attach images

1. Create a blank Confluence page.
2. Drag all files from `docs/images/` onto the page (attachments).
3. Insert a **Markdown** macro (or paste into the editor if your site supports MD).
4. Paste the contents of:
   `docs/Ossie_Semantic_Model_Builder_Design_and_Capabilities.md`
5. If images don’t resolve, replace each `images/NN_....png` with the Confluence attachment link (or use **Insert → Image** under each figure caption).

## Option C — Paste HTML

1. Open `docs/confluence/Ossie_Semantic_Model_Builder.html` in a browser.
2. Select all → copy.
3. In Confluence, paste into the editor (Confluence usually keeps headings and images if you paste from a browser that loaded local images — otherwise attach images first as in Option B).

## Suggested Confluence page structure

- Title: **Ossie Semantic Model Builder — Design & Capabilities**
- Labels: `ossie`, `power-bi`, `fabric`, `semantic-model`, `data-platform`
- Parent: your Data Platform / Analytics Eng wiki space

## Files in this folder

| File | Purpose |
|---|---|
| `Ossie_Semantic_Model_Builder_Design_and_Capabilities.md` | Source Markdown |
| `Ossie_Semantic_Model_Builder_Design_and_Capabilities.docx` | Word import for Confluence |
| `confluence/Ossie_Semantic_Model_Builder.html` | Browser-friendly HTML |
| `images/*.png` | Screenshots + architecture diagram |
