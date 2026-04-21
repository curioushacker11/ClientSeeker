"""
Email discovery scraper using Playwright.

Three capabilities:
1. scrape_social_profile - extract emails, display name, bio links from social profiles
2. scrape_page_emails - extract emails from any webpage
3. google_search - search Google and return results with snippets + emails found
"""

import re
import logging
import random
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# Email regex — matches common email patterns, filters junk
EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)

# Domains that are never real contact emails
JUNK_EMAIL_DOMAINS = {
    "example.com", "example.org", "test.com", "sentry.io",
    "wixpress.com", "w3.org", "schema.org", "googleapis.com",
    "googleusercontent.com", "fbcdn.net", "cdninstagram.com",
    "apple.com", "icloud.com",
}

JUNK_EMAIL_PREFIXES = {
    "noreply", "no-reply", "mailer-daemon", "postmaster",
    "webmaster", "support@tiktok", "support@instagram",
    "support@facebook",
}


def _extract_emails(text):
    """Extract valid-looking emails from text, filter junk."""
    if not text:
        return []
    raw = EMAIL_RE.findall(text)
    seen = set()
    result = []
    for e in raw:
        e = e.lower().strip(".")
        if e in seen:
            continue
        seen.add(e)
        domain = e.split("@")[1]
        if domain in JUNK_EMAIL_DOMAINS:
            continue
        if any(e.startswith(p) for p in JUNK_EMAIL_PREFIXES):
            continue
        # Skip image/asset-like emails
        if any(ext in domain for ext in [".png", ".jpg", ".gif", ".svg"]):
            continue
        result.append(e)
    return result


def _extract_links(text):
    """Extract URLs from text."""
    if not text:
        return []
    return re.findall(r'https?://[^\s"\'<>]+', text)


async def _new_browser_page():
    """Create a Playwright browser and page with stealth settings."""
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=["--disable-dev-shm-usage", "--no-sandbox", "--disable-setuid-sandbox"],
    )
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        java_script_enabled=True,
        locale="en-US",
    )
    page = await context.new_page()
    return pw, browser, page


# ---- 1. Social Profile Scraping ----

async def scrape_social_profile(url, platform):
    """
    Scrape a social media profile page.
    Returns: {emails: [], display_name: str|null, bio: str|null, links: []}
    """
    pw, browser, page = await _new_browser_page()
    result = {"emails": [], "display_name": None, "bio": None, "links": []}

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(3000)  # let JS render

        html = await page.content()
        text = await page.evaluate("() => document.body?.innerText || ''")

        # Extract emails from full page
        result["emails"] = _extract_emails(html)

        if platform == "tiktok":
            result.update(_parse_tiktok(html, text))
        elif platform == "instagram":
            result.update(_parse_instagram(html, text))
        elif platform == "facebook":
            # Facebook About tab often has the email
            result.update(await _parse_facebook(page, html, text, url))

        # Dedupe
        result["emails"] = list(dict.fromkeys(result["emails"]))

    except PlaywrightTimeoutError:
        logger.warning(f"Timeout scraping profile: {url}")
    except Exception as e:
        logger.error(f"Error scraping profile {url}: {e}", exc_info=True)
    finally:
        await browser.close()
        await pw.stop()

    return result


def _parse_tiktok(html, text):
    """Extract TikTok-specific data from page."""
    extra = {"emails": [], "display_name": None, "bio": None, "links": []}

    # Display name from JSON
    m = re.search(r'"nickname"\s*:\s*"([^"]+)"', html)
    if m:
        extra["display_name"] = m.group(1)

    # Bio / signature
    m = re.search(r'"signature"\s*:\s*"([^"]*)"', html)
    if m and m.group(1).strip():
        bio = m.group(1).strip()
        extra["bio"] = bio
        extra["emails"].extend(_extract_emails(bio))

    # Link in bio
    m = re.search(r'"bioLink"\s*:\s*\{[^}]*"link"\s*:\s*"([^"]+)"', html)
    if m:
        extra["links"].append(m.group(1))

    # Also look for linkInBio URL patterns
    for link_match in re.finditer(r'"link"\s*:\s*"(https?://[^"]+)"', html):
        link = link_match.group(1)
        parsed = urlparse(link)
        # Skip TikTok's own internal links
        if "tiktok.com" not in parsed.netloc:
            if link not in extra["links"]:
                extra["links"].append(link)

    return extra


def _parse_instagram(html, text):
    """Extract Instagram-specific data from page."""
    extra = {"emails": [], "display_name": None, "bio": None, "links": []}

    # Display name from meta or JSON
    m = re.search(r'"full_name"\s*:\s*"([^"]+)"', html)
    if m:
        extra["display_name"] = m.group(1)
    if not extra["display_name"]:
        m = re.search(r'<title>([^(]+?)\s*\(', html)
        if m:
            extra["display_name"] = m.group(1).strip()

    # Bio from JSON
    m = re.search(r'"biography"\s*:\s*"([^"]*)"', html)
    if m and m.group(1).strip():
        bio = m.group(1).replace("\\n", "\n").strip()
        extra["bio"] = bio
        extra["emails"].extend(_extract_emails(bio))

    # Business email (IG business accounts)
    m = re.search(r'"business_email"\s*:\s*"([^"]+)"', html)
    if m:
        extra["emails"].append(m.group(1).lower())

    # External URL
    m = re.search(r'"external_url"\s*:\s*"(https?://[^"]+)"', html)
    if m:
        extra["links"].append(m.group(1))

    # Bio link from page text (linktr.ee, etc.)
    for link in _extract_links(text):
        parsed = urlparse(link)
        if "instagram.com" not in parsed.netloc and link not in extra["links"]:
            extra["links"].append(link)
            if len(extra["links"]) >= 5:
                break

    return extra


async def _parse_facebook(page, html, text, url):
    """Extract Facebook-specific data. May navigate to About tab."""
    extra = {"emails": [], "display_name": None, "bio": None, "links": []}

    # Display name from og:title or page title
    m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html)
    if m:
        name = m.group(1).strip()
        # Clean "- Facebook" suffix
        name = re.sub(r'\s*[-|]\s*Facebook.*$', '', name).strip()
        if name:
            extra["display_name"] = name
    if not extra["display_name"]:
        m = re.search(r'<title>([^<]+)</title>', html)
        if m:
            name = re.sub(r'\s*[-|]\s*Facebook.*$', '', m.group(1)).strip()
            if name:
                extra["display_name"] = name

    # Try to navigate to About tab for more info
    try:
        about_url = url.rstrip("/") + "/about"
        await page.goto(about_url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(3000)
        about_html = await page.content()
        about_text = await page.evaluate("() => document.body?.innerText || ''")

        extra["emails"].extend(_extract_emails(about_html))
        extra["emails"].extend(_extract_emails(about_text))

        # Look for website links in about page
        for link in _extract_links(about_text):
            parsed = urlparse(link)
            if "facebook.com" not in parsed.netloc and link not in extra["links"]:
                extra["links"].append(link)
                if len(extra["links"]) >= 5:
                    break
    except Exception as e:
        logger.debug(f"Could not load Facebook About tab: {e}")

    # Also check main page emails
    extra["emails"].extend(_extract_emails(text))

    return extra


# ---- 2. Page Email Scraping ----

async def scrape_page_emails(url):
    """
    Scrape a webpage for email addresses.
    Tries the given URL, plus /contact and /contacto pages.
    Returns: {emails: [], pages_checked: []}
    """
    pw, browser, page = await _new_browser_page()
    all_emails = []
    pages_checked = []

    try:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        # Pages to check: given URL + common contact pages
        urls_to_check = [url]
        if parsed.path in ("", "/", "/index.html"):
            # Only add contact pages if we're on the homepage
            for suffix in ["/contact", "/contacto", "/about", "/nosotros",
                           "/contact-us", "/contactenos"]:
                urls_to_check.append(base + suffix)

        for check_url in urls_to_check:
            try:
                resp = await page.goto(check_url, wait_until="domcontentloaded", timeout=15000)
                if resp and resp.status < 400:
                    await page.wait_for_timeout(2000)
                    html = await page.content()
                    text = await page.evaluate("() => document.body?.innerText || ''")
                    emails = _extract_emails(html + " " + text)
                    all_emails.extend(emails)
                    pages_checked.append(check_url)
                    if all_emails:
                        break  # Found emails, no need to check more pages
            except Exception:
                continue

    except Exception as e:
        logger.error(f"Error scraping page {url}: {e}", exc_info=True)
    finally:
        await browser.close()
        await pw.stop()

    return {
        "emails": list(dict.fromkeys(all_emails)),
        "pages_checked": pages_checked,
    }


# ---- 3. Google Search ----

async def google_search(query, max_results=5):
    """
    Search Google and return results with titles, URLs, snippets, and any emails found.
    Returns: {results: [{title, url, snippet, emails}], emails_in_snippets: []}
    """
    pw, browser, page = await _new_browser_page()
    results = []
    snippet_emails = []

    try:
        search_url = f"https://www.google.com/search?q={query}&num={max_results}&hl=en"
        await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(2000)

        # Handle consent popup if it appears
        try:
            accept_btn = page.locator("button:has-text('Accept all'), button:has-text('Aceptar todo')")
            if await accept_btn.count() > 0:
                await accept_btn.first.click()
                await page.wait_for_timeout(1000)
        except Exception:
            pass

        # Extract search results
        result_elements = await page.query_selector_all("div.g")
        for elem in result_elements[:max_results]:
            try:
                title_el = await elem.query_selector("h3")
                link_el = await elem.query_selector("a")
                snippet_el = await elem.query_selector("div.VwiC3b, span.aCOpRe, div[data-sncf]")

                title = await title_el.inner_text() if title_el else ""
                href = await link_el.get_attribute("href") if link_el else ""
                snippet = await snippet_el.inner_text() if snippet_el else ""

                # Extract emails from snippet
                emails = _extract_emails(snippet)
                snippet_emails.extend(emails)

                if href and href.startswith("http"):
                    results.append({
                        "title": title,
                        "url": href,
                        "snippet": snippet,
                        "emails": emails,
                    })
            except Exception:
                continue

        # Also check page text for emails (sometimes in featured snippets, etc.)
        page_text = await page.evaluate("() => document.body?.innerText || ''")
        snippet_emails.extend(_extract_emails(page_text))

    except PlaywrightTimeoutError:
        logger.warning(f"Google search timeout for: {query}")
    except Exception as e:
        logger.error(f"Google search error: {e}", exc_info=True)
    finally:
        await browser.close()
        await pw.stop()

    return {
        "results": results,
        "emails_in_snippets": list(dict.fromkeys(snippet_emails)),
    }
