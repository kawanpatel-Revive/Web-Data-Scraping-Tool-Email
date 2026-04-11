import streamlit as st
import pandas as pd
import io
import requests
from bs4 import BeautifulSoup
from backend import run_scraper, run_verifier, crawl_site, is_company, HEADERS

st.title("Company Scraping Tool")

# =========================
# DEBUG MODE TOGGLE
# =========================
debug_mode = st.sidebar.checkbox("🛠 Show Debug Logs", value=True)
log_lines = []

def log(msg):
    print(msg)
    log_lines.append(msg)

# =========================
# UI
# =========================

st.header("1️⃣ Upload Excel For Bulk Scraping")
uploaded_file = st.file_uploader("Upload Excel File", type=["xlsx"])

st.header("OR")

st.header("2️⃣ Scrape Single Website")
single_url = st.text_input("Enter Website URL")

operation = st.selectbox("Choose Operation", ["Run Scraper", "Run Verifier"])

if st.button("Run"):

    result = None

    with st.spinner("Processing..."):

        try:

            # -----------------------------------------------
            # SINGLE URL DEBUG FLOW
            # -----------------------------------------------
            if single_url and operation == "Run Scraper":

                if not single_url.startswith("http"):
                    single_url = "https://" + single_url

                log(f"▶ Target URL: {single_url}")

                # Step 1: Can we reach the site at all?
                log("── Step 1: Testing HTTP connection...")
                try:
                    r = requests.get(single_url, headers=HEADERS, timeout=15)
                    log(f"   ✅ HTTP {r.status_code} — reachable")
                    log(f"   Content-Type: {r.headers.get('Content-Type', 'unknown')}")
                    log(f"   Response size: {len(r.text)} chars")

                    # Step 2: Can BeautifulSoup parse it?
                    log("── Step 2: Parsing HTML...")
                    soup = BeautifulSoup(r.text, "html.parser")

                    # Remove noise
                    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                        tag.decompose()

                    all_tags = soup.find_all(["h1","h2","h3","h4","strong","b","td","li","span","a","p"])
                    log(f"   Found {len(all_tags)} candidate tags after noise removal")

                    # Step 3: Show sample raw text from tags
                    log("── Step 3: Sample tag texts (first 20):")
                    import re
                    seen = set()
                    count = 0
                    for tag in all_tags:
                        text = re.sub(r'\s+', ' ', tag.get_text(separator=" ").strip())
                        if text and text not in seen and len(text) > 3:
                            seen.add(text)
                            log(f"   [{tag.name}] {text[:80]}")
                            count += 1
                            if count >= 20:
                                break

                    # Step 4: Run is_company on each and show decisions
                    log("── Step 4: is_company() decisions (first 40 non-empty texts):")
                    seen2 = set()
                    checked = 0
                    passed = 0
                    for tag in all_tags:
                        text = re.sub(r'\s+', ' ', tag.get_text(separator=" ").strip())
                        if text and text not in seen2 and 4 <= len(text) <= 100:
                            seen2.add(text)
                            result_flag = is_company(text)
                            if result_flag:
                                log(f"   ✅ PASS  → {text[:70]}")
                                passed += 1
                            else:
                                log(f"   ❌ FAIL  → {text[:70]}")
                            checked += 1
                            if checked >= 40:
                                break

                    log(f"── Summary: {passed} / {checked} texts passed is_company()")

                except requests.exceptions.Timeout:
                    log("   ❌ TIMEOUT — site did not respond in 15s")
                except requests.exceptions.SSLError as e:
                    log(f"   ❌ SSL ERROR — {e}")
                except requests.exceptions.ConnectionError as e:
                    log(f"   ❌ CONNECTION ERROR — {e}")
                except Exception as e:
                    log(f"   ❌ UNEXPECTED ERROR — {e}")

                # Step 5: Run actual crawler
                log("── Step 5: Running full crawl...")
                companies = crawl_site(single_url)
                log(f"   Crawl complete. Companies found: {len(companies)}")
                if companies:
                    for c in sorted(companies)[:20]:
                        log(f"   • {c}")

                result = pd.DataFrame({"Company Name": sorted(companies)})

            # -----------------------------------------------
            # BULK FLOW
            # -----------------------------------------------
            elif uploaded_file and operation == "Run Scraper":
                log("▶ Bulk scrape mode")
                df = pd.read_excel(uploaded_file)
                log(f"   Loaded Excel: {df.shape[0]} rows, columns: {list(df.columns)}")
                result = run_scraper(df)
                log(f"   Total companies found: {len(result)}")

            # -----------------------------------------------
            # VERIFIER
            # -----------------------------------------------
            elif uploaded_file and operation == "Run Verifier":
                log("▶ Verifier mode")
                df = pd.read_excel(uploaded_file)
                result = run_verifier(df)
                log(f"   Verified {len(result)} rows")

            else:
                st.error("Please upload a file or enter a website URL.")
                st.stop()

        except ValueError as e:
            st.error(f"Input Error: {e}")
            log(f"ValueError: {e}")
            st.stop()
        except Exception as e:
            st.error(f"Something went wrong: {e}")
            log(f"Exception: {e}")
            st.stop()

    # =========================
    # SHOW DEBUG LOGS
    # =========================
    if debug_mode and log_lines:
        st.subheader("🛠 Debug Log")
        st.code("\n".join(log_lines), language="text")

    # =========================
    # SHOW RESULTS
    # =========================
    if result is not None and not result.empty:
        st.success("Completed")
        st.write("Rows in result:", len(result))
        st.dataframe(result.head(50))

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            result.to_excel(writer, index=False)
        buffer.seek(0)

        st.download_button(
            label="Download Excel",
            data=buffer,
            file_name="output.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        if debug_mode and log_lines:
            st.warning("No companies found. Check the debug log above to see why.")
        else:
            st.warning("No companies were found. Enable **🛠 Show Debug Logs** in the sidebar and try again.")