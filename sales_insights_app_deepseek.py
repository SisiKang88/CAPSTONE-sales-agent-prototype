"""
Sales Insights Assistant - Streamlit front-end + LLM back-end

Run:
    pip install streamlit openai requests beautifulsoup4 pypdf python-pptx python-docx
    export DEEPSEEK_API_KEY="your-key"
    streamlit run sales_insights_app_ds.py
"""

import io
import json
import os
import re
from urllib.parse import urlparse

import requests
import streamlit as st
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
MODEL_NAME = os.getenv("LLM_MODEL", "deepseek-chat")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MAX_DOC_CHARS = 12000  # cap on document text sent to the LLM

st.set_page_config(page_title="Sales Insights Assistant", page_icon="📈", layout="wide")


# ----------------------------------------------------------------------------
# URL helpers: derive company ID + lightweight metadata
# ----------------------------------------------------------------------------
def normalize_url(url: str) -> str:
    url = url.strip()
    if url and not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


def is_valid_url(url: str) -> bool:
    try:
        parsed = urlparse(normalize_url(url))
        return bool(parsed.netloc and "." in parsed.netloc)
    except Exception:
        return False


def derive_company_id(url: str) -> str:
    """e.g. https://www.snowflake.com/en/ -> 'snowflake'"""
    host = urlparse(normalize_url(url)).netloc.lower()
    host = re.sub(r"^www\.", "", host)
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net", "ac"}:
        return parts[-3]  # handles domains like example.co.uk
    return parts[-2] if len(parts) >= 2 else host


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_company_metadata(url: str) -> dict:
    """Best-effort scrape of title / description. Fails gracefully."""
    url = normalize_url(url)
    meta = {
        "url": url,
        "domain": urlparse(url).netloc,
        "company_id": derive_company_id(url),
        "title": "",
        "description": "",
    }
    try:
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0 (SalesInsightsBot)"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        if soup.title and soup.title.string:
            meta["title"] = soup.title.string.strip()
        tag = soup.find("meta", attrs={"name": "description"}) or soup.find(
            "meta", attrs={"property": "og:description"}
        )
        if tag and tag.get("content"):
            meta["description"] = tag["content"].strip()
    except Exception as e:
        meta["error"] = str(e)
    return meta


# ----------------------------------------------------------------------------
# Document parsing (PDF, PPTX, DOCX, TXT/MD)
# ----------------------------------------------------------------------------
def extract_text_from_upload(uploaded_file) -> str:
    name = uploaded_file.name.lower()
    data = uploaded_file.getvalue()
    text = ""

    if name.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)

    elif name.endswith(".pptx"):
        from pptx import Presentation

        prs = Presentation(io.BytesIO(data))
        chunks = []
        for i, slide in enumerate(prs.slides, start=1):
            chunks.append(f"--- Slide {i} ---")
            for shape in slide.shapes:
                if shape.has_text_frame:
                    chunks.append(shape.text_frame.text)
        text = "\n".join(chunks)

    elif name.endswith(".docx"):
        from docx import Document

        doc = Document(io.BytesIO(data))
        text = "\n".join(p.text for p in doc.paragraphs)

    elif name.endswith((".txt", ".md")):
        text = data.decode("utf-8", errors="ignore")

    else:
        raise ValueError("Unsupported file type. Use PDF, PPTX, DOCX, TXT or MD.")

    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ----------------------------------------------------------------------------
# LLM back-end
# ----------------------------------------------------------------------------
SYSTEM_PROMPT = """You are an expert B2B sales strategist. Given details about a product,
a target company, competitors and a target buyer, produce practical, specific sales
insights. Respond with ONLY valid JSON (no markdown fences, no preamble) using this schema:

{
  "product_category": "short category label inferred from the product info",
  "product_summary": "2-3 sentence summary of the product (use the overview document if given)",
  "key_features": ["..."],
  "target_company_snapshot": "what the target company likely does and where this product could fit",
  "buyer_persona": {"likely_priorities": ["..."], "likely_objections": ["..."]},
  "competitive_positioning": [{"competitor": "...", "their_angle": "...", "how_to_differentiate": "..."}],
  "talking_points": ["..."],
  "discovery_questions": ["..."],
  "outreach_email": {"subject": "...", "body": "..."}
}

Be concrete and avoid generic filler. If you are unsure about a fact regarding a company,
say so rather than inventing details."""


def build_user_prompt(inputs: dict) -> str:
    competitor_block = (
        "\n".join(
            f"- {c['company_id']} ({c['url']}) | title: {c.get('title') or 'n/a'} | "
            f"description: {c.get('description') or 'n/a'}"
            for c in inputs["competitors"]
        )
        or "None provided"
    )
    target = inputs["company"]
    doc_block = inputs["document_text"][:MAX_DOC_CHARS] or "None provided"

    return f"""PRODUCT NAME: {inputs['product_name']}
PRODUCT CATEGORY (as entered by rep, may be blank or a sentence): {inputs['product_category'] or 'Not provided - infer it'}
VALUE PROPOSITION: {inputs['value_prop']}

TARGET COMPANY:
- company_id: {target['company_id']}
- url: {target['url']}
- page title: {target.get('title') or 'n/a'}
- page description: {target.get('description') or 'n/a'}

TARGET CUSTOMER (person to sell to): {inputs['target_customer']}

COMPETITORS:
{competitor_block}

PRODUCT OVERVIEW DOCUMENT (extracted text):
{doc_block}
"""


def call_llm(inputs: dict) -> dict:
    from openai import OpenAI

    api_key = os.getenv("DEEPSEEK_API_KEY") or st.session_state.get("api_key")
    if not api_key:
        raise RuntimeError("No API key found. Set DEEPSEEK_API_KEY or enter it in the sidebar.")

    # DeepSeek's API is OpenAI-compatible, so we reuse the openai package
    client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
    response = client.chat.completions.create(
        model=MODEL_NAME,
        max_tokens=2500,
        temperature=0.4,
        response_format={"type": "json_object"},  # forces valid JSON output
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(inputs)},
        ],
    )
    raw = response.choices[0].message.content or ""
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    return json.loads(raw)


# ----------------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------------
def render_results(result: dict):
    st.success(f"Identified category: **{result.get('product_category', 'N/A')}**")

    st.subheader("Product summary")
    st.write(result.get("product_summary", ""))
    if result.get("key_features"):
        st.markdown("**Key features**")
        for f in result["key_features"]:
            st.markdown(f"- {f}")

    st.subheader("Target company snapshot")
    st.write(result.get("target_company_snapshot", ""))

    persona = result.get("buyer_persona", {})
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Buyer priorities**")
        for p in persona.get("likely_priorities", []):
            st.markdown(f"- {p}")
    with c2:
        st.markdown("**Likely objections**")
        for o in persona.get("likely_objections", []):
            st.markdown(f"- {o}")

    if result.get("competitive_positioning"):
        st.subheader("Competitive positioning")
        for c in result["competitive_positioning"]:
            with st.expander(c.get("competitor", "Competitor")):
                st.markdown(f"**Their angle:** {c.get('their_angle', '')}")
                st.markdown(f"**How to differentiate:** {c.get('how_to_differentiate', '')}")

    st.subheader("Talking points")
    for t in result.get("talking_points", []):
        st.markdown(f"- {t}")

    st.subheader("Discovery questions")
    for q in result.get("discovery_questions", []):
        st.markdown(f"- {q}")

    email = result.get("outreach_email", {})
    if email:
        st.subheader("Draft outreach email")
        st.text_input("Subject", value=email.get("subject", ""), key="email_subject")
        st.text_area("Body", value=email.get("body", ""), height=260, key="email_body")

    st.download_button(
        "Download insights (JSON)",
        data=json.dumps(result, indent=2),
        file_name="sales_insights.json",
        mime="application/json",
    )


# ----------------------------------------------------------------------------
# UI: input section
# ----------------------------------------------------------------------------
def main():
    st.title("📈 Sales Insights Assistant")
    st.caption("Enter your deal details and let the LLM generate tailored sales insights.")

    with st.sidebar:
        st.header("Settings")
        if not os.getenv("DEEPSEEK_API_KEY"):
            st.text_input("DeepSeek API key", type="password", key="api_key")
        st.markdown(f"Model: `{MODEL_NAME}`")

    with st.form("sales_inputs"):
        st.subheader("Deal details")

        col1, col2 = st.columns(2)
        with col1:
            product_name = st.text_input(
                "Product Name *", placeholder="e.g., Snowflake Data Cloud",
                help="What product are you selling?",
            )
            company_url = st.text_input(
                "Company URL *", placeholder="https://www.targetcompany.com",
                help="The company you are targeting. Used to derive the company ID and metadata.",
            )
            product_category = st.text_input(
                "Product Category", placeholder="e.g., Data Warehousing or Cloud Data Platform",
                help="One word or a sentence. Leave blank and the LLM will infer it.",
            )
            target_customer = st.text_input(
                "Target Customer *", placeholder="e.g., Jane Smith, VP of Data Engineering",
                help="Name of the person you are trying to sell to.",
            )
        with col2:
            competitors_raw = st.text_area(
                "Competitors (URLs)", height=120,
                placeholder="One URL per line, e.g.\nhttps://www.competitor1.com\nhttps://www.competitor2.com",
            )
            value_prop = st.text_area(
                "Value Proposition *", height=100,
                placeholder="One sentence summarizing the product's value.",
            )

        uploaded = st.file_uploader(
            "Optional: Product overview sheet or deck",
            type=["pdf", "pptx", "docx", "txt", "md"],
            help="The system parses this to extract more insights about the product.",
        )

        submitted = st.form_submit_button("Generate insights", type="primary")

    if not submitted:
        return

    # ---- Validation -------------------------------------------------------
    errors = []
    if not product_name.strip():
        errors.append("Product Name is required.")
    if not company_url.strip() or not is_valid_url(company_url):
        errors.append("Please provide a valid Company URL.")
    if not target_customer.strip():
        errors.append("Target Customer is required.")
    if not value_prop.strip():
        errors.append("Value Proposition is required.")

    competitor_urls = [u.strip() for u in competitors_raw.splitlines() if u.strip()]
    bad = [u for u in competitor_urls if not is_valid_url(u)]
    if bad:
        errors.append("Invalid competitor URL(s): " + ", ".join(bad))

    if errors:
        for e in errors:
            st.error(e)
        return

    # ---- Backend processing ----------------------------------------------
    with st.spinner("Gathering company metadata..."):
        company = fetch_company_metadata(company_url)
        competitors = [fetch_company_metadata(u) for u in competitor_urls]

    document_text = ""
    if uploaded is not None:
        with st.spinner("Parsing product overview document..."):
            try:
                document_text = extract_text_from_upload(uploaded)
                if not document_text:
                    st.warning("No readable text found in the uploaded document.")
            except Exception as e:
                st.warning(f"Could not parse document: {e}")

    inputs = {
        "product_name": product_name.strip(),
        "company": company,
        "product_category": product_category.strip(),
        "competitors": competitors,
        "value_prop": value_prop.strip(),
        "target_customer": target_customer.strip(),
        "document_text": document_text,
    }

    with st.expander("Processed inputs (what the system extracted)"):
        st.write(f"**Company ID:** `{company['company_id']}` ({company['domain']})")
        if company.get("title"):
            st.write(f"**Page title:** {company['title']}")
        if company.get("description"):
            st.write(f"**Description:** {company['description']}")
        if competitors:
            st.write("**Competitor IDs:** " + ", ".join(f"`{c['company_id']}`" for c in competitors))
        if document_text:
            st.write(f"**Document text extracted:** {len(document_text):,} characters")

    with st.spinner("Generating insights with the LLM..."):
        try:
            result = call_llm(inputs)
        except json.JSONDecodeError:
            st.error("The LLM returned a response that couldn't be parsed. Please try again.")
            return
        except Exception as e:
            st.error(f"LLM call failed: {e}")
            return

    render_results(result)


if __name__ == "__main__":
    main()
