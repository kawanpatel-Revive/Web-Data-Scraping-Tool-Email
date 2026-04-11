import pandas as pd
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque
import time
import re

# =========================
# CONFIGURATION
# =========================

MAX_PAGES_PER_SITE = 50
MAX_DEPTH = 3
THREADS = 8
REQUEST_DELAY = 0.3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Strong signals — if any of these appear, it's almost certainly a company
COMPANY_SUFFIX = [
    "ltd", "limited", "pvt", "private", "llp", "llc",
    "industries", "corporation", "corp", "inc",
    "systems", "engineering", "solutions",
    "technologies", "technology", "services", "group",
    "manufacturing", "enterprises", "exports", "imports",
    "international", "global", "co.", "& co", "company"
]

# If any of these appear anywhere in the text, skip it
HARD_BLACKLIST = [
    "contact us", "about us", "careers", "login", "sign in",
    "privacy policy", "terms", "cookie", "copyright",
    "all rights reserved", "follow us", "subscribe",
    "read more", "learn more", "click here", "home",
    "our products", "our services", "get a quote",
    "request a", "download", "newsletter"
]

# Navigation / UI words that are never company names
SKIP_WORDS = {
    "home", "about", "contact", "services", "products", "blog",
    "news", "events", "gallery", "careers", "login", "logout",
    "register", "search", "menu", "back", "next", "previous",
    "submit", "send", "more", "less", "all", "view", "show",
    "hide", "open", "close", "yes", "no", "ok", "cancel",
    "terms", "privacy", "policy", "sitemap", "faq", "help",
    "support", "english", "hindi", "language", "share",
    "facebook", "twitter", "linkedin", "instagram", "youtube"
}

DIRECTORY_HINTS = [
    "member", "directory", "companies", "suppliers",
    "manufacturers", "partners", "vendors", "industries",
    "exhibitors", "listings", "members"
]

BAD_TERMS = [
    "report", "technology transfer", "expo", "copyright",
    "event", "episode", "article", "news", "promise"
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
        score = sum(1 for v in values[:50] if url_pattern.search(v))
        if score > best_score:
            best_score = score
            best_column = col

    if best_column is None:
        raise Exception("No URL column detected in uploaded file.")

    print("Detected URL column:", best_column)
    return best_column


# =========================
# COMPANY DETECTION (REWRITTEN)
# =========================

def is_company(text):
    """
    Multi-signal company name detector.
    Returns True if the text looks like a company name.
    """
    t = text.strip()
    t_lower = t.lower()

    # --- Hard filters (fast rejections) ---

    # Too short or too long
    if len(t) < 4 or len(t) > 100:
        return False

    # Too many words
    words = t_lower.split()
    if len(words) > 8:
        return False

    # Single word that's a known nav/UI term
    if len(words) == 1 and t_lower in SKIP_WORDS:
        return False

    # Contains a full blacklisted phrase
    if any(b in t_lower for b in HARD_BLACKLIST):
        return False

    # Looks like a sentence (has common filler words)
    if re.search(r'\b(is|are|was|were|the|and|for|with|our|your|we|you|it|this|that)\b', t_lower):
        return False

    # Contains URLs or emails
    if re.search(r'(https?://|www\.|@)', t_lower):
        return False

    # Mostly numbers
    digit_ratio = sum(c.isdigit() for c in t) / max(len(t), 1)
    if digit_ratio > 0.5:
        return False

    # Contains special characters that don't belong in company names
    if re.search(r'[<>{}\[\]|\\^`~]', t):
        return False

    # --- Positive signals ---

    # Strong signal: has a known company suffix
    if any(re.search(r'\b' + re.escape(s) + r'\b', t_lower) for s in COMPANY_SUFFIX):
        return True

    # Medium signal: Title Case proper noun phrase (2–5 words)
    # e.g. "Tata Steel", "Reliance Power", "Mahindra Electric"
    if 2 <= len(words) <= 5:
        original_words = t.split()
        capitalized = sum(1 for w in original_words if w and w[0].isupper())
        if capitalized >= len(original_words) - 1:
            non_skip = [w for w in words if w not in SKIP_WORDS]
            if len(non_skip) >= 2:
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
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Remove noise tags before extracting text
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()

        # Extract companies from high-signal tags
        for tag in soup.find_all(["h1", "h2", "h3", "h4", "strong", "b", "td", "li", "span", "a", "p"]):
            text = tag.get_text(separator=" ").strip()
            text = re.sub(r'\s+', ' ', text)
            if is_company(text):
                companies.add(text.title())

        # Extract links
        for a in soup.find_all("a", href=True):
            link = urljoin(url, a["href"])
            link = link.rstrip("/")
            if link.startswith(("mailto:", "tel:", "#")):
                continue
            links.append(link)

    except requests.exceptions.Timeout:
        print(f"[TIMEOUT] {url}")
    except requests.exceptions.SSLError:
        print(f"[SSL ERROR] {url}")
    except requests.exceptions.ConnectionError:
        print(f"[CONNECTION ERROR] {url}")
    except Exception as e:
        print(f"[ERROR] scrape_page({url}): {e}")

    return companies, links


# =========================
# FILTER INTERNAL LINKS
# =========================

def filter_internal_links(base_url, links):
    domain = urlparse(base_url).netloc
    return [link for link in links if domain in urlparse(link).netloc]


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
    base_url = base_url.rstrip("/")
    visited = set()
    queue = deque([(base_url, 0)])
    companies = set()

    while queue and len(visited) < MAX_PAGES_PER_SITE:
        url, depth = queue.popleft()
        url = url.rstrip("/")

        if url in visited:
            continue
        visited.add(url)

        page_companies, links = scrape_page(url)
        companies.update(page_companies)

        if depth < MAX_DEPTH:
            links = filter_internal_links(base_url, links)
            links = prioritize_links(links)
            for link in links[:30]:
                if link not in visited:
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
    print("Total URLs to scrape:", len(urls))

    all_companies = set()
    failed = []

    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = {executor.submit(crawl_site, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                result = future.result()
                all_companies.update(result)
            except Exception as e:
                print(f"[FAILED] {url}: {e}")
                failed.append(url)

    if failed:
        print(f"\n[SUMMARY] {len(failed)} URL(s) failed:")
        for f in failed:
            print(f"  - {f}")

    return pd.DataFrame({"Company Name": sorted(all_companies)})


# =========================
# VERIFIER
# =========================

def run_verifier(df):
    if "Company Name" not in df.columns:
        raise ValueError(
            "The uploaded file must contain a 'Company Name' column. "
            "Please upload the scraper output file, not a raw URL file."
        )

    def check_company(name):
        n = str(name).lower()
        if any(b in n for b in BAD_TERMS):
            return "Likely Wrong"
        if len(n.split()) > 6:
            return "Suspicious"
        return "Likely Company"

    df = df.copy()
    df["Check"] = df["Company Name"].apply(check_company)
    return df