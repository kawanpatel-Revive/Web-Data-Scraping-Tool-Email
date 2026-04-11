import streamlit as st
import pandas as pd
import io
from backend import run_scraper, run_verifier, crawl_site

st.title("Company Scraping Tool")

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

            # BULK SCRAPER
            if uploaded_file and operation == "Run Scraper":
                df = pd.read_excel(uploaded_file)
                result = run_scraper(df)

            # SINGLE WEBSITE SCRAPER
            elif single_url and operation == "Run Scraper":
                if not single_url.startswith("http"):
                    single_url = "https://" + single_url
                companies = crawl_site(single_url)
                result = pd.DataFrame({"Company Name": sorted(companies)})

            # VERIFIER
            elif uploaded_file and operation == "Run Verifier":
                df = pd.read_excel(uploaded_file)
                result = run_verifier(df)

            else:
                st.error("Please upload a file or enter a website URL.")
                st.stop()

        except ValueError as e:
            st.error(f"Input Error: {e}")
            st.stop()
        except Exception as e:
            st.error(f"Something went wrong: {e}")
            st.stop()

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
        st.warning("No companies were found. The site may be JavaScript-rendered or blocking scraping.")