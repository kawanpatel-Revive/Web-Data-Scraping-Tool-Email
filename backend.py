import pandas as pd
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import re

# =========================
# CONFIGURATION
# =========================

MAX_PAGES_PER_SITE = 50
MAX_DEPTH = 3
THREADS = 8
REQUEST_DELAY = 1

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

COMPANY_SUFFIX = [
    "ltd","limited","pvt","private","llp",
    "industries","corporation","corp",
    "systems","engineering","solutions",
    "technologies","services","group"
]

BLACKLIST = [
    "contact","about","career","login","privacy",
    "policy","copyright","terms","cookie",
    "news","blog","event","expo","article"
]

DIRECTORY_HINTS = [
    "member","directory","companies","suppliers",
    "manufacturers","partners","vendors","industries"
]

BAD_TERMS = [
    "report","technology transfer","expo","copyright",
    "event","episode","article","news","promise"
]


# =========================
# AUTO URL COLUMN DETECTION
# =========================

def detect_url_column(df):

    url_pattern = re.compile(r"(https?://|www\.)", re.IGNORECASE)

    best_column = None
    best_score = 0

    for col in df.columns:

        values = df[col].dropna().astype(str)

        score = 0

        for v in values[:50]:

            if url_pattern.search(v):
                score += 1

        if score > best_score:
            best_score = score
            best_column = col

    if best_column is None:
        raise Exception("No URL column detected in uploaded file.")

    print("Detected URL column:", best_column)

    return best_column


# =========================
# COMPANY DETECTION
# =========================

def is_company(text):

    t = text.lower().strip()

    if len(t.split()) > 7:
        return False

    if any(char.isdigit() for char in t):
        return False

    if any(b in t for b in BLACKLIST):
        return False

    if any(s in t for s in COMPANY_SUFFIX):
        return True

    return False


# =========================
# PAGE SCRAPER
# =========================

def scrape_page(url):

    companies = set()
    links = []

    try:

        r = requests.get(url, headers=HEADERS, timeout=15)

        soup = BeautifulSoup(r.text, "html.parser")

        # Extract companies
        for tag in soup.find_all(["h1","h2","h3","h4","strong","b","span","a"]):

            text = tag.get_text().strip()

            if 5 < len(text) < 120:

                if is_company(text):
                    companies.add(text.title())

        # Extract links
        for a in soup.find_all("a", href=True):

            link = urljoin(url, a["href"])
            links.append(link)

    except:
        pass

    return companies, links


# =========================
# FILTER INTERNAL LINKS
# =========================

def filter_internal_links(base_url, links):

    domain = urlparse(base_url).netloc

    clean = []

    for link in links:

        if domain in urlparse(link).netloc:
            clean.append(link)

    return clean


# =========================
# PRIORITIZE DIRECTORY LINKS
# =========================

def prioritize_links(links):

    directory = []
    others = []

    for link in links:

        if any(h in link.lower() for h in DIRECTORY_HINTS):
            directory.append(link)
        else:
            others.append(link)

    return directory + others


# =========================
# SITE CRAWLER
# =========================

def crawl_site(base_url):

    visited = set()
    queue = [(base_url, 0)]

    companies = set()

    while queue and len(visited) < MAX_PAGES_PER_SITE:

        url, depth = queue.pop(0)

        if url in visited:
            continue

        visited.add(url)

        page_companies, links = scrape_page(url)

        companies.update(page_companies)

        if depth < MAX_DEPTH:

            links = filter_internal_links(base_url, links)
            links = prioritize_links(links)

            for link in links[:30]:
                queue.append((link, depth + 1))

        time.sleep(REQUEST_DELAY)

    return companies


# =========================
# BULK SCRAPER
# =========================

def run_scraper(df):

    url_column = detect_url_column(df)

    urls = df[url_column].dropna().astype(str)

    cleaned_urls = []

    for url in urls:

        url = url.strip()

        if not url.startswith("http"):
            url = "https://" + url

        cleaned_urls.append(url)

    urls = list(set(cleaned_urls))

    print("Total URLs received:", len(urls))

    all_companies = set()
    failed = []

    with ThreadPoolExecutor(max_workers=THREADS) as executor:

        futures = {
            executor.submit(crawl_site, url): url
            for url in urls
        }

        for future in as_completed(futures):

            url = futures[future]

            try:
                result = future.result()
                all_companies.update(result)

            except:
                failed.append(url)

    result_df = pd.DataFrame({
        "Company Name": sorted(all_companies)
    })

    return result_df


# =========================
# VERIFIER
# =========================

def run_verifier(df):

    def check_company(name):

        n = str(name).lower()

        if any(b in n for b in BAD_TERMS):
            return "Likely Wrong"

        if len(n.split()) > 6:
            return "Suspicious"

        return "Likely Company"

    df["Check"] = df["Company Name"].apply(check_company)

    return df