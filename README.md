# 📈 Sales Insights Assistant

A Streamlit app that helps a sales rep prepare for a deal. The rep enters details about the product they sell, the company they are targeting, the competition and the person they want to sell to. The app enriches those inputs (company metadata, optional product deck) and sends them to an LLM, which returns structured sales insights: positioning, talking points, discovery questions and a draft outreach email.

The inputs are fully dynamic, so you can pick any product you like (software, apparel, services, etc.) and the insights adapt to it.

---

## Features

- **Simple input form** for product, target company, competitors, value proposition and target buyer
- **Automatic company ID extraction** from URLs (e.g. `https://www.guess.com` → `guess`)
- **Lightweight web metadata scraping** (page title and meta description) for the target company and each competitor
- **Product category inference**: leave the category blank, or enter a sentence, and the LLM identifies the category
- **Optional document upload** (PDF, PPTX, DOCX, TXT, MD) whose text is extracted and used to enrich product understanding
- **Structured JSON output** rendered as readable sections in the UI
- **Editable email draft** and a **download button** for the full insights as JSON

---

## Project structure

```
.
├── sales_insights_app.py   # Streamlit front-end + backend logic
├── sales_insights.json     # Example output produced by the app
└── README.md
```

---

## Inputs

| Field | Required | Description |
|---|---|---|
| **Product Name** | Yes | The product you are selling |
| **Company URL** | Yes | URL of the company you are targeting. Used to derive the company ID and scrape metadata |
| **Product Category** | No | One word or a sentence (e.g. "Data Warehousing"). If blank, the LLM infers it |
| **Competitors** | No | Competitor URLs, one per line |
| **Value Proposition** | Yes | One sentence summarizing the product's value |
| **Target Customer** | Yes | Name (and ideally title) of the person you are selling to |
| **Product overview upload** | No | PDF / PPTX / DOCX / TXT / MD. The app extracts its text for extra context |

---

## How it works

```
Sales rep fills form
        │
        ▼
 Input validation (required fields, URL format)
        │
        ▼
 Company enrichment ── derive company ID + scrape title/description
        │               (target company and each competitor)
        ▼
 Document parsing ──── extract text from the optional upload
        │               (capped at 12,000 characters)
        ▼
 Prompt builder ────── combines everything into one prompt
        │
        ▼
 LLM call ──────────── returns structured JSON
        │
        ▼
 Rendered insights + email draft + JSON download
```

### Key functions in `sales_insights_app.py`

| Function | Purpose |
|---|---|
| `normalize_url` / `is_valid_url` | Clean and validate user-entered URLs |
| `derive_company_id` | Turn a URL into a company ID (handles domains like `example.co.uk`) |
| `fetch_company_metadata` | Best-effort scrape of the page title and description. Fails gracefully and is cached for an hour |
| `extract_text_from_upload` | Parse PDF, PPTX, DOCX, TXT and MD files |
| `build_user_prompt` | Assemble all inputs into the LLM prompt |
| `call_llm` | Call the model in JSON mode and parse the response |
| `render_results` | Display the insights in the UI |

---

## Setup

### 1. Requirements

- Python 3.9 or newer (recommended)
- A DeepSeek API key from [platform.deepseek.com](https://platform.deepseek.com). Your account needs a positive balance

### 2. Install dependencies

```bash
python -m pip install streamlit openai requests beautifulsoup4 pypdf python-pptx python-docx
```

> The `openai` package is used because DeepSeek's API is OpenAI-compatible.

### 3. Set your API key

```bash
# macOS / Linux
export DEEPSEEK_API_KEY="sk-your-key-here"

# Windows PowerShell
$env:DEEPSEEK_API_KEY="sk-your-key-here"

# Windows Command Prompt
set DEEPSEEK_API_KEY=sk-your-key-here
```

If the variable is not set, the app shows an API key field in the sidebar.

### 4. Run the app

```bash
python -m streamlit run sales_insights_app.py
```

The app opens at **http://localhost:8501**. Press `Ctrl + C` in the terminal to stop it.

---

## Configuration

| Setting | Default | How to change |
|---|---|---|
| Model | `deepseek-chat` | Set the `LLM_MODEL` environment variable |
| Base URL | `https://api.deepseek.com` | Edit `DEEPSEEK_BASE_URL` in the code |
| Max document characters | `12000` | Edit `MAX_DOC_CHARS` in the code |

To use a different OpenAI-compatible provider, change the model name, base URL and API key variable in `call_llm`.

---

## Example

The repo includes `sales_insights.json`, a real output from the app. The inputs for this run were:

- **Product:** a clothing line positioned as "the frontline of fashion"
- **Target company:** `fanshionclothing.com`
- **Competitors:** Guess and Louis Vuitton
- **Category:** left blank, then inferred by the LLM

### Output schema

```json
{
  "product_category": "Fashion Clothing",
  "product_summary": "...",
  "key_features": ["..."],
  "target_company_snapshot": "...",
  "buyer_persona": {
    "likely_priorities": ["..."],
    "likely_objections": ["..."]
  },
  "competitive_positioning": [
    {
      "competitor": "Guess",
      "their_angle": "...",
      "how_to_differentiate": "..."
    }
  ],
  "talking_points": ["..."],
  "discovery_questions": ["..."],
  "outreach_email": { "subject": "...", "body": "..." }
}
```

### What the example produced

- **Inferred category:** `Fashion Clothing`
- **Positioning against Guess:** an agile, trend-responsive alternative without the legacy brand overhead
- **Positioning against Louis Vuitton:** accessibility and speed to trend rather than heritage luxury
- **Buyer objections:** the lack of detailed product information (materials, sizing, minimum order quantities) and price and margin concerns
- **Draft email:** subject line *"Fresh frontline fashion for fanshionclothing"*, with `[Name]` and `[Your Name]` placeholders for the rep to fill in

Notice that the model flags uncertainty where the input was thin. The target company's site title and description could not be scraped, so the snapshot says the business model is "uncertain" instead of guessing. Adding a product overview document or a fuller value proposition gives the LLM more to work with and produces more specific results.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Import "bs4" / "openai" could not be resolved` | The package isn't installed in the interpreter your editor uses. Run `python -m pip install beautifulsoup4 openai` and select the right interpreter |
| `urllib3 v2.0 only supports OpenSSL 1.1.1+` | Your Python is built on an old OpenSSL. Run `python -m pip install "urllib3<2"` or upgrade Python |
| "No API key found" | Set `DEEPSEEK_API_KEY` in the same terminal you run the app from, or use the sidebar field |
| "Insufficient balance" error | Add credit to your DeepSeek account |
| `streamlit: command not found` | Use `python -m streamlit run sales_insights_app.py` |
| Company title or description is empty | The site may block scrapers or load content with JavaScript. The app still runs, just with less context |
| "LLM returned a response that couldn't be parsed" | Click **Generate insights** again. Stick with `deepseek-chat`, since `deepseek-reasoner` doesn't support JSON mode |

---

## Limitations

- Web scraping is best-effort (title and meta description only). It does not read full pages or run JavaScript.
- Insights are only as good as the inputs. The LLM may make mistakes about companies it has no data on, so verify facts before using them in outreach.
- Large documents are truncated to the first 12,000 characters.
- Scanned PDFs without a text layer can't be parsed (no OCR).

---

## Security notes

- Never hard-code your API key or commit it to version control.
- If you use a `.env` file, add it to `.gitignore`.
