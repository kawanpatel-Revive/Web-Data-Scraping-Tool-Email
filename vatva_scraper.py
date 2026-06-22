"""
VIA Directory Scraper - vatvaassociation.org
=============================================
Scrapes one letter from the VIA Member Directory using Playwright.

SETUP (run once):
    pip install playwright beautifulsoup4
    playwright install chromium

RUN:
    python vatva_scraper.py --letter A

OUTPUT:
    vatva_directory_A.csv companies for the requested letter
"""

import time
import csv
import re
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

BASE_URL = "https://www.vatvaassociation.org/directory"
CLIENTS_API_URL = "https://www.vatvaassociation.org/xhr/get-clients.php"
VALID_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
NAVIGATION_TIMEOUT_MS = 60000


def load_directory_page(page):
    """Load the directory without waiting for every background request to stop."""
    try:
        page.goto(
            BASE_URL,
            wait_until="domcontentloaded",
            timeout=NAVIGATION_TIMEOUT_MS,
        )
    except PlaywrightTimeoutError:
        if page.url == "about:blank":
            raise
        print("  Warning: page load timed out, continuing with rendered content.")

    page.wait_for_timeout(2000)


def click_letter(page, letter):
    """Click one directory letter using the site's available markup."""
    strategies = [
        lambda: page.get_by_role(
            "link", name=re.compile(rf"^{re.escape(letter)}$")
        ).first.click(),
        lambda: page.locator(f"a:text-is('{letter}')").first.click(),
        lambda: page.get_by_text(letter, exact=True).first.click(),
    ]

    for strategy in strategies:
        try:
            strategy()
            return True
        except Exception:
            continue

    return False


def wait_for_directory_results(page):
    """Give the directory request time to update the rendered results."""
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(2000)


def load_all_directory_results(page, max_scrolls=100):
    """Scroll until infinite pagination stops adding company cards."""
    cards = page.locator(".leading-single")
    previous_count = cards.count()
    stable_rounds = 0

    for scroll_number in range(1, max_scrolls + 1):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2500)

        current_count = cards.count()
        if current_count > previous_count:
            print(
                f"  Pagination {scroll_number}: "
                f"{previous_count} -> {current_count} cards"
            )
            previous_count = current_count
            stable_rounds = 0
        else:
            stable_rounds += 1
            if stable_rounds >= 2:
                print(f"  Pagination complete at {current_count} cards")
                return current_count

    print(f"  Warning: stopped pagination at safety limit ({previous_count} cards)")
    return previous_count


def first_value(record, *keys):
    """Return the first non-empty string from a directory API record."""
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def parse_company_from_api(record):
    """Convert one directory API record to the output schema."""
    address_parts = [
        first_value(record, "sAddress"),
        first_value(record, "sArea"),
        first_value(record, "sCityName"),
        first_value(record, "sStateName"),
        first_value(record, "sPincode"),
    ]

    return {
        "name": first_value(record, "sCompanyName", "title"),
        "phone": first_value(
            record,
            "sPhone1",
            "sPhone2",
            "sPhone3",
            "sMobile",
            "sMobile2",
            "sMobile3",
        ),
        "email": first_value(record, "sEmail", "sEmail2", "sEmail3"),
        "website": first_value(record, "sWebsite", "sWebsite2"),
        "address": ", ".join(dict.fromkeys(part for part in address_parts if part)),
        "category": first_value(
            record,
            "sCategoryStr",
            "sBusinessCategory",
            "sProductStr",
        ),
    }


def fetch_all_companies_from_api(page, letter, max_pages=100):
    """Fetch every API page for one directory letter."""
    companies = []

    for page_number in range(1, max_pages + 1):
        response = page.request.post(
            CLIENTS_API_URL,
            form={"page": str(page_number), "keyword": letter},
        )
        if not response.ok:
            raise RuntimeError(
                f"Directory API page {page_number} returned HTTP {response.status}"
            )

        payload = response.json()
        records = payload.get("data", [])
        if not records:
            print(f"  Pagination complete after {page_number - 1} API pages")
            return companies

        companies.extend(parse_company_from_api(record) for record in records)
        print(
            f"  API page {page_number}: "
            f"{len(records)} records ({len(companies)} total)"
        )

    print(f"  Warning: stopped API pagination at safety limit ({max_pages} pages)")
    return companies


# ──────────────────────────────────────────────
# STEP 1: Intercept the API call when a letter
#         is clicked, so we know the endpoint.
# ──────────────────────────────────────────────
def discover_api(page, letter):
    """Click the requested letter and capture any XHR/fetch calls made."""
    captured = []

    def on_request(req):
        if req.resource_type in ("xhr", "fetch"):
            captured.append({
                "url":       req.url,
                "method":    req.method,
                "post_data": req.post_data,
                "headers":   dict(req.headers),
            })

    page.on("request", on_request)
    load_directory_page(page)

    click_letter(page, letter)
    wait_for_directory_results(page)
    return captured


# ──────────────────────────────────────────────
# STEP 2: Parse company cards from rendered HTML
# ──────────────────────────────────────────────
def parse_companies_from_html(html: str) -> list[dict]:
    """
    Parse rendered page HTML and extract company info.
    The directory uses IBPhub platform - cards typically have:
      .listing-item / .business-card / .dir-item etc.
    We try multiple selectors and fall back to text parsing.
    """
    soup = BeautifulSoup(html, "html.parser")
    companies = []

    # --- Try common IBPhub card selectors ---
    card_selectors = [
        ".leading-single",
        ".listing-item",
        ".business-card",
        ".dir-item",
        ".company-item",
        ".search-result-item",
        "[class*='listing']",
        "[class*='business']",
        "[class*='company']",
    ]

    cards = []
    for sel in card_selectors:
        cards = soup.select(sel)
        if cards:
            print(f"  Found {len(cards)} cards using selector: {sel}")
            break

    for card in cards:
        company = {}

        # Name - usually in h2/h3/h4 or .title
        name_el = card.select_one(
            ".lead-c-name, h2, h3, h4, .title, .name, "
            "[class*='title'], [class*='name']"
        )
        company["name"] = name_el.get_text(strip=True) if name_el else ""

        # Phone
        phone_el = card.select_one("a[href^='tel:'], .phone, [class*='phone'], [class*='mobile']")
        if phone_el:
            company["phone"] = phone_el.get_text(strip=True)
        else:
            # Try regex on card text
            match = re.search(r"[\+\d][\d\s\-]{8,14}", card.get_text())
            company["phone"] = match.group(0).strip() if match else ""

        # Email
        email_el = card.select_one("a[href^='mailto:'], .email, [class*='email']")
        if email_el:
            company["email"] = email_el.get("href", "").replace("mailto:", "").strip() or email_el.get_text(strip=True)
        else:
            match = re.search(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", card.get_text())
            company["email"] = match.group(0) if match else ""

        # Website
        web_el = card.select_one("a[href^='http']:not([href*='vatvaassociation'])")
        company["website"] = web_el.get("href", "") if web_el else ""

        # Address
        addr_el = card.select_one(
            ".lead-c-city, .address, [class*='address'], [class*='location']"
        )
        company["address"] = addr_el.get_text(strip=True) if addr_el else ""

        # Category / product
        cat_el = card.select_one(
            ".lead-website, .category, .product, "
            "[class*='categ'], [class*='product']"
        )
        company["category"] = cat_el.get_text(strip=True) if cat_el else ""

        if company.get("name"):
            companies.append(company)

    return companies


# ──────────────────────────────────────────────
# STEP 3: Main scraper loop
# ──────────────────────────────────────────────
def scrape_directory(letter):
    all_companies = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,          # Set True to run silently
            slow_mo=300,             # Slows actions - polite to server
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        print("🔍 Discovering API calls...")
        api_calls = discover_api(page, letter)

        if api_calls:
            print(f"✅ Detected {len(api_calls)} XHR/fetch call(s):")
            for c in api_calls:
                print(f"   {c['method']} {c['url']}")
                print(f"   POST data: {c['post_data']}")
        else:
            print("ℹ️  No XHR calls detected - will scrape rendered HTML directly.")

        print(f"\n📂 Scraping letter: {letter}")

        try:
            load_directory_page(page)

            if not click_letter(page, letter):
                print(f"  ⚠️  Could not click letter {letter}.")
            else:
                wait_for_directory_results(page)

                # Infinite scrolling calls this paginated JSON endpoint.
                companies = fetch_all_companies_from_api(page, letter)
                print(f"  ✅ Found {len(companies)} companies")
                all_companies.extend(companies)

                # Be polite - don't hammer the server
                time.sleep(1.5)

        except Exception as e:
            print(f"  ❌ Error on letter {letter}: {e}")

        browser.close()

    return all_companies


def smoke_test(letter):
    """Test browser, website, letter filtering, and extraction."""
    print("Testing Chromium and directory access...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            load_directory_page(page)
            print(f"  PASS Page loaded: {page.title()}")

            if not click_letter(page, letter):
                print(f"  FAIL Could not find or click the letter {letter} filter.")
                return False

            wait_for_directory_results(page)
            companies = parse_companies_from_html(page.content())
            if not companies:
                print("  FAIL Letter A loaded, but the parser found no companies.")
                print("       The site's HTML selectors likely need updating.")
                return False

            print(f"  PASS Extracted {len(companies)} companies for letter {letter}.")
            print(f"  PASS Sample company: {companies[0]['name']}")
            return True
        except Exception as exc:
            print(f"  FAIL {type(exc).__name__}: {exc}")
            return False
        finally:
            browser.close()


# ──────────────────────────────────────────────
# STEP 4: Save results
# ──────────────────────────────────────────────
def save_results(companies: list[dict], letter: str):
    if not companies:
        print("\n⚠️  No companies scraped. Check selectors or the site structure.")
        return

    # Remove duplicates by name
    seen = set()
    unique = []
    for c in companies:
        key = c.get("name", "").lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(c)

    print(f"\n💾 Saving {len(unique)} unique companies...")

    output_csv = f"vatva_directory_{letter}.csv"
    fieldnames = ["name", "phone", "email", "website", "address", "category"]
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(unique)
    print(f"   ✅ CSV saved: {output_csv}")

    # Preview
    print(f"\n📋 Sample output (first 3):")
    for c in unique[:3]:
        print(c)


# ──────────────────────────────────────────────
# BONUS: Inspect mode - run this first to see
#        actual HTML structure of one letter
# ──────────────────────────────────────────────
def inspect_mode(letter):
    """
    Opens the browser visibly, loads the requested letter, and prints
    the raw HTML so you can identify the correct CSS selectors.
    Run this first if parse_companies_from_html returns 0 results.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        load_directory_page(page)

        click_letter(page, letter)
        wait_for_directory_results(page)

        html = page.content()

        # Print the middle section (where results likely are)
        soup = BeautifulSoup(html, "html.parser")
        main = soup.select_one("main, #content, .content, body")
        if main:
            # Show first 3000 chars of main content
            print(str(main)[:3000])
        else:
            print(html[:3000])

        print("\n\n--- All CSS classes found ---")
        classes = set()
        for tag in soup.find_all(True):
            for cls in tag.get("class", []):
                classes.add(cls)
        print(sorted(classes))

        input("\nPress Enter to close browser...")
        browser.close()


# ──────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Scrape the VIA member directory.")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run a smoke test for the required letter.",
    )
    parser.add_argument("--inspect", action="store_true", help="Inspect rendered page markup.")
    parser.add_argument(
        "--letter",
        required=True,
        help="Required letter to scrape, for example: --letter A",
    )
    args = parser.parse_args()

    letter = args.letter.strip().upper()
    if len(letter) != 1 or letter not in VALID_LETTERS:
        parser.error("--letter must be one letter from A to Z")

    if args.test:
        print("VIA Directory Scraper smoke test")
        print("=" * 50)
        sys.exit(0 if smoke_test(letter) else 1)
    elif args.inspect:
        # Run: python vatva_scraper.py --inspect --letter A
        print("🔬 Inspect mode - examining page structure...")
        inspect_mode(letter)
    else:
        print("🚀 Starting VIA Directory Scraper...")
        print("=" * 50)
        companies = scrape_directory(letter)
        save_results(companies, letter)
        print("\n✅ Done!")
