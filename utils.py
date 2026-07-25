import os
import asyncio
import requests
import random 
import concurrent
import aiohttp
import httpx
import time
import json
import ast
from typing import Annotated, List, TypedDict, Literal, Optional, Dict, Any, Union
from urllib.parse import unquote
import io
from pdfminer.high_level import extract_text
import operator

from bs4 import BeautifulSoup

from langchain_core.tools import tool

from langsmith import traceable

from pydantic import BaseModel, Field

class Section(BaseModel):
    name: str = Field(
        description="Name for this section of the report.",
    )
    description: str = Field(
        description="Brief overview of the main topics and concepts to be covered in this section.",
    )
    research: bool = Field(
        description="Whether to perform web research for this section of the report."
    )
    content: str = Field(
        description="The content of the section."
    )   

class Sections(BaseModel):
    sections: List[Section] = Field(
        description="Sections of the report.",
    )

class SearchQuery(BaseModel):
    search_query: str = Field(None, description="Query for web search.")

class Queries(BaseModel):
    queries: List[SearchQuery] = Field(
        description="List of search queries.",
    )

class Feedback(BaseModel):
    grade: Literal["pass","fail"] = Field(
        description="Evaluation result indicating whether the response meets requirements ('pass') or needs revision ('fail')."
    )
    follow_up_queries: List[SearchQuery] = Field(
        description="List of follow-up search queries.",
    )

class ReportStateInput(TypedDict):
    topic: str

class ReportStateOutput(TypedDict):
    final_report: str

class ReportState(TypedDict):
    topic: str
    feedback_on_report_plan: str
    sections: list[Section]
    completed_sections: Annotated[list, operator.add]
    report_sections_from_research: str
    final_report: str
    structured_output: dict

class SectionState(TypedDict):
    topic: str
    section: Section
    search_iterations: int
    search_queries: list[SearchQuery]
    source_str: str
    report_sections_from_research: str
    completed_sections: list[Section]

class SectionOutputState(TypedDict):
    completed_sections: list[Section]

SITES_TRUSTED_SOURCE = []
site_env = os.environ.get("SITES_TRUSTED_SOURCE")
if site_env:
    try:
        SITES_TRUSTED_SOURCE = ast.literal_eval(site_env)
    except Exception as e:
        print(f"Failed to parse SITES_TRUSTED_SOURCE from .env: {e}")

def get_config_value(value):
    if isinstance(value, str):
        return value
    elif isinstance(value, dict):
        return value
    elif isinstance(value, bool):
        return value
    elif value is None:
        return None
    else:
        return value.value
    
def get_search_params(search_api: str, search_api_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    SEARCH_API_PARAMS = {
        "googlesearch": ["max_results"],
        "tavily": ["max_results", "topic"],
    }

    accepted_params = SEARCH_API_PARAMS.get(search_api, [])

    if not search_api_config:
        return {}

    return {k: v for k, v in search_api_config.items() if k in accepted_params}

def deduplicate_and_format_sources(search_response, max_tokens_per_source=5000, include_raw_content=True):
    sources_list = []
    for response in search_response:
        sources_list.extend(response['results'])
    
    unique_sources = {source['url']: source for source in sources_list}

    formatted_text = "Content from sources:\n"
    for i, source in enumerate(unique_sources.values(), 1):
        formatted_text += f"{'='*80}\n"
        formatted_text += f"Source: {source['title']}\n"
        formatted_text += f"{'-'*80}\n"
        formatted_text += f"URL: {source['url']}\n===\n"
        formatted_text += f"Most relevant content from source: {source['content']}\n===\n"
        if include_raw_content:
            char_limit = max_tokens_per_source * 4
            raw_content = source.get('raw_content', '')
            if raw_content is None:
                raw_content = ''
                print(f"Warning: No raw_content found for source {source['url']}")
            if len(raw_content) > char_limit:
                raw_content = raw_content[:char_limit] + "... [truncated]"
            formatted_text += f"Full source content limited to {max_tokens_per_source} tokens: {raw_content}\n\n"
        formatted_text += f"{'='*80}\n\n"
                
    return formatted_text.strip()

def format_sections(sections: list[Section]) -> str:
    formatted_str = ""
    for idx, section in enumerate(sections, 1):
        formatted_str += f"""
{'='*60}
Section {idx}: {section.name}
{'='*60}
Description:
{section.description}
Requires Research: 
{section.research}

Content:
{section.content if section.content else '[Not yet written]'}

"""
    return formatted_str

@traceable
async def google_search_async(search_queries: Union[str, List[str]], max_results: int = 5, include_raw_content: bool = True, min_results: int = 3):
    api_key = os.environ.get("GOOGLE_API_KEY")
    cx = os.environ.get("GOOGLE_CX")
    use_api = bool(api_key and cx)
    if use_api:
        print("Using Google Custom Search API...")
    else:
        print("Using web scraping...")

    def build_site_filter(sites):
        return " OR ".join([f"site:{site}" for site in sites]) if sites else ""

    if isinstance(search_queries, str):
        search_queries = [search_queries]

    site_filter = build_site_filter(SITES_TRUSTED_SOURCE)
    high_queries = [f"{q} {site_filter}" if site_filter else q for q in search_queries]
    high_results = await _google_search_async_inner(high_queries, max_results, include_raw_content, use_api, api_key, cx)

    return high_results

async def _google_search_async_inner(search_queries, max_results, include_raw_content, use_api, api_key, cx):
    def get_useragent():
        lynx_version = f"Lynx/{random.randint(2, 3)}.{random.randint(8, 9)}.{random.randint(0, 2)}"
        libwww_version = f"libwww-FM/{random.randint(2, 3)}.{random.randint(13, 15)}"
        ssl_mm_version = f"SSL-MM/{random.randint(1, 2)}.{random.randint(3, 5)}"
        openssl_version = f"OpenSSL/{random.randint(1, 3)}.{random.randint(0, 4)}.{random.randint(0, 9)}"
        return f"{lynx_version} {libwww_version} {ssl_mm_version} {openssl_version}"
    
    executor = None if use_api else concurrent.futures.ThreadPoolExecutor(max_workers=5)
    
    semaphore = asyncio.Semaphore(5 if use_api else 2)
    
    async def search_single_query(query):
        async with semaphore:
            try:
                results = []
                
                if use_api:
                    for start_index in range(1, max_results + 1, 10):
                        num = min(10, max_results - (start_index - 1))
                        
                        params = {
                            'q': query,
                            'key': api_key,
                            'cx': cx,
                            'start': start_index,
                            'num': num
                        }
                        print(f"Requesting {num} results for '{query}' from Google API...")

                        async with aiohttp.ClientSession() as session:
                            async with session.get('https://www.googleapis.com/customsearch/v1', params=params) as response:
                                if response.status != 200:
                                    error_text = await response.text()
                                    print(f"API error: {response.status}, {error_text}")
                                    break
                                    
                                data = await response.json()
                                
                                for item in data.get('items', []):
                                    result = {
                                        "title": item.get('title', ''),
                                        "url": item.get('link', ''),
                                        "content": item.get('snippet', ''),
                                        "score": None,
                                        "raw_content": item.get('snippet', '')
                                    }
                                    results.append(result)
                        
                        await asyncio.sleep(0.2)
                        
                        if not data.get('items') or len(data.get('items', [])) < num:
                            break
                
                else:
                    await asyncio.sleep(0.5 + random.random() * 1.5)
                    print(f"Scraping Google for '{query}'...")

                    def google_search(query, max_results):
                        try:
                            lang = "en"
                            safe = "active"
                            start = 0
                            fetched_results = 0
                            fetched_links = set()
                            search_results = []
                            
                            while fetched_results < max_results:
                                resp = requests.get(
                                    url="https://www.google.com/search",
                                    headers={
                                        "User-Agent": get_useragent(),
                                        "Accept": "*/*"
                                    },
                                    params={
                                        "q": query,
                                        "num": max_results + 2,
                                        "hl": lang,
                                        "start": start,
                                        "safe": safe,
                                    },
                                    cookies = {
                                        'CONSENT': 'PENDING+987',
                                        'SOCS': 'CAESHAgBEhIaAB',
                                    }
                                )
                                resp.raise_for_status()
                                
                                soup = BeautifulSoup(resp.text, "html.parser")
                                result_block = soup.find_all("div", class_="ezO2md")
                                new_results = 0
                                
                                for result in result_block:
                                    link_tag = result.find("a", href=True)
                                    title_tag = link_tag.find("span", class_="CVA68e") if link_tag else None
                                    description_tag = result.find("span", class_="FrIlee")
                                    
                                    if link_tag and title_tag and description_tag:
                                        link = unquote(link_tag["href"].split("&")[0].replace("/url?q=", ""))
                                        
                                        if link in fetched_links:
                                            continue
                                        
                                        fetched_links.add(link)
                                        title = title_tag.text
                                        description = description_tag.text
                                        
                                        search_results.append({
                                            "title": title,
                                            "url": link,
                                            "content": description,
                                            "score": None,
                                            "raw_content": description
                                        })
                                        
                                        fetched_results += 1
                                        new_results += 1
                                        
                                        if fetched_results >= max_results:
                                            break
                                
                                if new_results == 0:
                                    break
                                    
                                start += 10
                                time.sleep(1)
                            
                            return search_results
                                
                        except Exception as e:
                            print(f"Error in Google search for '{query}': {str(e)}")
                            return []
                    
                    loop = asyncio.get_running_loop()
                    search_results = await loop.run_in_executor(
                        executor, 
                        lambda: google_search(query, max_results)
                    )
                    
                    results = search_results
                
                if include_raw_content and results:
                    content_semaphore = asyncio.Semaphore(3)
                    connector = aiohttp.TCPConnector(limit=20, limit_per_host=5)
                    async with aiohttp.ClientSession(connector=connector) as session:
                        fetch_tasks = []
                        
                        async def fetch_full_content(result, max_pdf_pages=5, max_html_chars=100_000, max_retries=2):
                            url = result['url']
                            headers = {
                                'User-Agent': get_useragent(),
                                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
                            }
                            for attempt in range(max_retries + 1):
                                try:
                                    await asyncio.sleep(0.2 + random.random() * 0.6)
                                    async with session.get(url, headers=headers, timeout=30, ssl=False) as response:
                                        if response.status == 200:
                                            content_type = response.headers.get('Content-Type', '').lower()
                                            if 'application/pdf' in content_type or url.lower().endswith('.pdf'):
                                                try:
                                                    pdf_bytes = await response.content.read(10 * 1024 * 1024)
                                                    text = extract_text(io.BytesIO(pdf_bytes), maxpages=max_pdf_pages)
                                                    result['raw_content'] = text
                                                except Exception as e:
                                                    result['raw_content'] = f"[PDF content extraction failed: {str(e)}]"
                                            elif 'text/html' in content_type:
                                                try:
                                                    html = await response.text(errors='replace')
                                                    soup = BeautifulSoup(html, 'html.parser')
                                                    text = soup.get_text()
                                                    result['raw_content'] = text
                                                except UnicodeDecodeError as ude:
                                                    result['raw_content'] = f"[Could not decode content: {str(ude)}]"
                                            else:
                                                result['raw_content'] = f"[Unsupported content type: {content_type}]"
                                        else:
                                            result['raw_content'] = f"[HTTP error: {response.status}]"
                                    break
                                except asyncio.TimeoutError:
                                    if attempt == max_retries:
                                        result['raw_content'] = "[Content fetch timeout, skipped]"
                                except Exception as e:
                                    if attempt == max_retries:
                                        print(f"Warning: Failed to fetch content for {url}: {str(e)}")
                                        result['raw_content'] = f"[Content fetch failed: {str(e)}]"
                            return result
                        
                        for result in results:
                            fetch_tasks.append(fetch_full_content(result))
                        
                        updated_results = await asyncio.gather(*fetch_tasks)
                        results = updated_results
                        print(f"Fetched full content for {len(results)} results")
                
                return {
                    "query": query,
                    "follow_up_questions": None,
                    "answer": None,
                    "images": [],
                    "results": results
                }
            except Exception as e:
                print(f"Error in Google search for query '{query}': {str(e)}")
                return {
                    "query": query,
                    "follow_up_questions": None,
                    "answer": None,
                    "images": [],
                    "results": []
                }
    
    try:
        search_tasks = [search_single_query(query) for query in search_queries]
        
        search_results = await asyncio.gather(*search_tasks)
        
        return search_results
    finally:
        if executor:
            executor.shutdown(wait=False)