from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import logging
import asyncio

# Import the scraper function (adjust path if necessary)
try:
    from gmaps_scraper_server.scraper import scrape_google_maps
except ImportError:
    logging.error("Could not import scrape_google_maps from scraper.py")
    def scrape_google_maps(*args, **kwargs):
        raise ImportError("Scraper function not available.")

from gmaps_scraper_server.email_scraper import (
    scrape_social_profile, scrape_page_emails, google_search
)

# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = FastAPI(
    title="Google Maps Scraper API",
    description="API to trigger Google Maps scraping based on a query.",
    version="0.1.0",
)

@app.post("/scrape", response_model=List[Dict[str, Any]])
async def run_scrape(
    query: str = Query(..., description="The search query for Google Maps (e.g., 'restaurants in New York')"),
    max_places: Optional[int] = Query(None, description="Maximum number of places to scrape. Scrapes all found if None."),
    lang: str = Query("en", description="Language code for Google Maps results (e.g., 'en', 'es')."),
    headless: bool = Query(True, description="Run the browser in headless mode (no UI). Set to false for debugging locally."),
    concurrency: int = Query(5, description="Number of concurrent tabs for scraping details. Default is 5.")
):
    """
    Triggers the Google Maps scraping process for the given query.
    """
    logging.info(f"Received scrape request for query: '{query}', max_places: {max_places}, lang: {lang}, "
                 f"headless: {headless}, concurrency: {concurrency}")
    try:
        # Run the potentially long-running scraping task with timeout
        # Note: For production, consider running this in a background task queue (e.g., Celery)
        # to avoid blocking the API server for long durations.
        results = await asyncio.wait_for(
            scrape_google_maps(
                query=query,
                max_places=max_places,
                lang=lang,
                headless=headless,
                concurrency=concurrency
            ),
            timeout=300  # 5 minutes timeout
        )
        logging.info(f"Scraping finished for query: '{query}'. Found {len(results)} results.")
        return results
    except asyncio.TimeoutError:
        logging.error(f"Scraping timeout for query '{query}' after 300 seconds")
        raise HTTPException(status_code=504, detail="Scraping request timed out after 5 minutes")
    except ImportError as e:
         logging.error(f"ImportError during scraping for query '{query}': {e}")
         raise HTTPException(status_code=500, detail="Server configuration error: Scraper not available.")
    except Exception as e:
        logging.error(f"An error occurred during scraping for query '{query}': {e}", exc_info=True)
        # Consider more specific error handling based on scraper exceptions
        raise HTTPException(status_code=500, detail=f"An internal error occurred during scraping: {str(e)}")

@app.get("/scrape-get", response_model=List[Dict[str, Any]])
async def run_scrape_get(
    query: str = Query(..., description="The search query for Google Maps (e.g., 'restaurants in New York')"),
    max_places: Optional[int] = Query(None, description="Maximum number of places to scrape. Scrapes all found if None."),
    lang: str = Query("en", description="Language code for Google Maps results (e.g., 'en', 'es')."),
    headless: bool = Query(True, description="Run the browser in headless mode (no UI). Set to false for debugging locally."),
    concurrency: int = Query(5, description="Number of concurrent tabs for scraping details. Default is 5.")
):
    """
    Triggers the Google Maps scraping process for the given query via GET request.
    """
    logging.info(f"Received GET scrape request for query: '{query}', max_places: {max_places}, lang: {lang}, "
                 f"headless: {headless}, concurrency: {concurrency}")
    try:
        # Run the potentially long-running scraping task with timeout
        # Note: For production, consider running this in a background task queue (e.g., Celery)
        # to avoid blocking the API server for long durations.
        results = await asyncio.wait_for(
            scrape_google_maps(
                query=query,
                max_places=max_places,
                lang=lang,
                headless=headless,
                concurrency=concurrency
            ),
            timeout=300  # 5 minutes timeout
        )
        logging.info(f"Scraping finished for query: '{query}'. Found {len(results)} results.")
        return results
    except asyncio.TimeoutError:
        logging.error(f"Scraping timeout for query '{query}' after 300 seconds")
        raise HTTPException(status_code=504, detail="Scraping request timed out after 5 minutes")
    except ImportError as e:
         logging.error(f"ImportError during scraping for query '{query}': {e}")
         raise HTTPException(status_code=500, detail="Server configuration error: Scraper not available.")
    except Exception as e:
        logging.error(f"An error occurred during scraping for query '{query}': {e}", exc_info=True)
        # Consider more specific error handling based on scraper exceptions
        raise HTTPException(status_code=500, detail=f"An internal error occurred during scraping: {str(e)}")


# --- Email Discovery Endpoints ---

class ProfileRequest(BaseModel):
    url: str
    platform: str  # tiktok, instagram, facebook

class PageRequest(BaseModel):
    url: str

class SearchRequest(BaseModel):
    query: str
    max_results: int = 5

@app.post("/scrape-profile")
async def scrape_profile(req: ProfileRequest):
    """Scrape a social media profile for email, display name, bio links."""
    try:
        result = await asyncio.wait_for(
            scrape_social_profile(req.url, req.platform),
            timeout=30,
        )
        return result
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Profile scrape timed out")
    except Exception as e:
        logging.error(f"Profile scrape error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/scrape-page")
async def scrape_page(req: PageRequest):
    """Scrape a webpage for email addresses."""
    try:
        result = await asyncio.wait_for(
            scrape_page_emails(req.url),
            timeout=30,
        )
        return result
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Page scrape timed out")
    except Exception as e:
        logging.error(f"Page scrape error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/google-search")
async def search_google(req: SearchRequest):
    """Google search and return results with snippets."""
    try:
        result = await asyncio.wait_for(
            google_search(req.query, req.max_results),
            timeout=30,
        )
        return result
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Google search timed out")
    except Exception as e:
        logging.error(f"Google search error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# Basic root endpoint for health check or info
@app.get("/")
async def read_root():
    return {"message": "Google Maps Scraper API is running."}