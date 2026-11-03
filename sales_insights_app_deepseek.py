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

def call_multi_agent_system(inputs: dict) -> dict:
    from openai import OpenAI
    
    api_key = os.getenv("DEEPSEEK_API_KEY") or st.session_state.get("api_key")
    if not api_key:
        raise RuntimeError("No API key found. Set DEEPSEEK_API_KEY or enter it in the sidebar.")

    client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
    
    # ---------------------------------------------------------
    # Agent 1: Company Research Agent (公司研究员)
    # ---------------------------------------------------------
    st.toast("🕵️ Research Agent is analyzing the company...")
    research_prompt = f"Product: {inputs['product_name']}\nCompany Info: {inputs['company']}\nDocument: {inputs['document_text'][:8000]}"
    research_sys = "You are a B2B Company Research Agent. Output ONLY JSON with keys: 'product_category', 'product_summary', 'key_features', 'target_company_snapshot'."
    
    res_1 = client.chat.completions.create(
        model=MODEL_NAME, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": research_sys}, {"role": "user", "content": research_prompt}]
    )
    research_data = json.loads(res_1.choices[0].message.content)

    # ---------------------------------------------------------
    # Agent 2: Competitor Analysis Agent (竞争分析师)
    # ---------------------------------------------------------
    st.toast("⚔️ Competitor Agent is evaluating rivals...")
    comp_prompt = f"Product: {inputs['product_name']}\nValue Prop: {inputs['value_prop']}\nCompetitors: {inputs['competitors']}"
    comp_sys = "You are a Competitor Analysis Agent. Output ONLY JSON with key 'competitive_positioning' (a list of dicts with keys: 'competitor', 'their_angle', 'how_to_differentiate'). If unsure, say 'Data unavailable'."
    
    res_2 = client.chat.completions.create(
        model=MODEL_NAME, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": comp_sys}, {"role": "user", "content": comp_prompt}]
    )
    comp_data = json.loads(res_2.choices[0].message.content)

    # ---------------------------------------------------------
    # Agent 3: Buyer Persona Agent (心理画像师)
    # ---------------------------------------------------------
    st.toast("👤 Persona Agent is profiling the buyer...")
    persona_prompt = f"Target Customer: {inputs['target_customer']}\nProduct Value: {inputs['value_prop']}"
    persona_sys = "You are a Buyer Persona Agent. Output ONLY JSON with key 'buyer_persona' containing lists for 'likely_priorities' and 'likely_objections'."
    
    res_3 = client.chat.completions.create(
        model=MODEL_NAME, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": persona_sys}, {"role": "user", "content": persona_prompt}]
    )
    persona_data = json.loads(res_3.choices[0].message.content)

    # ---------------------------------------------------------
    # Agent 4: Orchestrator / Lead Strategist (销售总监)
    # ---------------------------------------------------------
    st.toast("🧠 Orchestrator is drafting the final strategy...")
    orchestrator_prompt = f"""
    Research Report: {json.dumps(research_data)}
    Competitor Report: {json.dumps(comp_data)}
    Persona Report: {json.dumps(persona_data)}
    """
    orchestrator_sys = "You are the Lead Sales Orchestrator. Using the provided agent reports, output ONLY JSON with keys: 'talking_points', 'discovery_questions', and 'outreach_email' (which is a dict with 'subject' and 'body')."
    
    res_4 = client.chat.completions.create(
        model=MODEL_NAME, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": orchestrator_sys}, {"role": "user", "content": orchestrator_prompt}]
    )
    orchestrator_data = json.loads(res_4.choices[0].message.content)

    # ---------------------------------------------------------
    # Combine all agent outputs for the UI
    # ---------------------------------------------------------
    final_result = {
        **research_data,
        **comp_data,
        **persona_data,
        **orchestrator_data
    }
    return final_result
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
            result = call_multi_agent_system(inputs)
        except json.JSONDecodeError:
            st.error("The LLM returned a response that couldn't be parsed. Please try again.")
            return
        except Exception as e:
            st.error(f"LLM call failed: {e}")
            return

    render_results(result)


if __name__ == "__main__":
    main()
