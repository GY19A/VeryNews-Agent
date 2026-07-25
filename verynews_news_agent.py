import sys
import os
from dotenv import load_dotenv
load_dotenv()
"""
verynews_news_agent.py
Multi-agent news authenticity detection main process, supporting 5W1H fact extraction, trusted site search, research report generation, authenticity judgment, and final report output.
"""
import uuid
import time
from datetime import datetime
from typing import Dict, Any, Tuple, List
from utils import get_config_value, google_search_async, deduplicate_and_format_sources
from prompts import (
    PROMPT_TRANSLATE_TO_EN, PROMPT_5W1H, PROMPT_FACT_CHECK, PROMPT_EVIDENCE_AGGREGATION, PROMPT_EXPERT_ANALYSIS,
    PROMPT_TIMELINESS, PROMPT_JUDGEMENT, PROMPT_VISUALIZATION, PROMPT_REPORT_EXPERT
)
import asyncio
import google.generativeai as genai

# Read Gemini API key
GEMINI_API_KEY = os.environ.get("GOOGLE_API_KEY")
MODEL = os.environ.get("MODEL")
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(MODEL)

# 1. News translation to English Agent
def news_translate_to_en(news_content: str) -> str:
    prompt = PROMPT_TRANSLATE_TO_EN.format(news_content=news_content)
    response = model.generate_content(prompt)
    return response.text.strip()

# 2. 5W1H Extraction Agent
def agent_5w1h(news_content: str, current_time: str) -> dict:
    prompt = PROMPT_5W1H.format(news_content=news_content, current_time=current_time)
    response = model.generate_content(prompt)
    try:
        facts = eval(response.text)
        if isinstance(facts, dict):
            return facts
    except Exception:
        pass
    return {}

# 3. Fact-checking Agent
async def agent_fact_check(news_content: str, facts: dict, current_time: str) -> list:
    prompt = PROMPT_FACT_CHECK.format(news_content=news_content, facts=facts, current_time=current_time)
    response = model.generate_content(prompt)
    try:
        search_queries = [item['query'] for item in eval(response.text) if 'query' in item]
    except Exception:
        search_queries = [news_content]
    search_results = await google_search_async(search_queries, max_results=5, include_raw_content=True)
    formatted = deduplicate_and_format_sources(search_results)
    return formatted

# 4. Evidence Aggregation Agent
def agent_evidence_aggregation(search_results: str, current_time: str) -> dict:
    prompt = PROMPT_EVIDENCE_AGGREGATION.format(search_results=search_results, current_time=current_time)
    response = model.generate_content(prompt)
    try:
        evidence = eval(response.text)
        if isinstance(evidence, dict):
            return evidence
    except Exception:
        pass
    return {"key_evidence": [], "contradictions": [], "summary": ""}

# 5. Expert Analysis Agent
def agent_expert_analysis(news_content: str, facts: dict, evidence: dict, current_time: str) -> dict:
    prompt = PROMPT_EXPERT_ANALYSIS.format(news_content=news_content, facts=facts, evidence=evidence, current_time=current_time)
    response = model.generate_content(prompt)
    try:
        analysis = eval(response.text)
        if isinstance(analysis, dict):
            return analysis
    except Exception:
        pass
    return {"analysis": "", "controversy": [], "credibility": ""}

# 6. Timeliness Tracking Agent
def agent_timeliness(news_content: str, facts: dict, evidence: dict, current_time: str) -> Tuple[List, List]:
    prompt = PROMPT_TIMELINESS.format(news_content=news_content, facts=facts, evidence=evidence, current_time=current_time)
    response = model.generate_content(prompt)
    try:
        result = eval(response.text)
        timeline = result.get("timeline", [])
        latest_updates = result.get("latest_updates", [])
        return timeline, latest_updates
    except Exception:
        return [], []

# 7. Judgement Agent
def agent_judgement(news_content: str, facts: dict, evidence: dict, analysis: dict, latest_updates: list, current_time: str) -> dict:
    prompt = PROMPT_JUDGEMENT.format(
        news_content=news_content, facts=facts, evidence=evidence, analysis=analysis, latest_updates=latest_updates, current_time=current_time)
    response = model.generate_content(prompt)
    try:
        judge_json = eval(response.text)
        if isinstance(judge_json, dict):
            return judge_json
    except Exception:
        pass
    return {"result": "Partially True", "reason": "", "sources": [], "timestamp": current_time}

# 8. Visualization Summary Agent
def agent_visualization(news_content: str, facts: dict, evidence: dict, analysis: dict, timeline: list, current_time: str) -> str:
    prompt = PROMPT_VISUALIZATION.format(
        news_content=news_content, facts=facts, evidence=evidence, analysis=analysis, timeline=timeline, current_time=current_time)
    response = model.generate_content(prompt)
    return response.text

# 9. Report Expert Agent
def agent_report_expert(news_content: str, facts: dict, evidence: dict, analysis: dict, judge_json: dict, timeline: list, latest_updates: list, visualization: str, current_time: str) -> str:
    prompt = PROMPT_REPORT_EXPERT.format(
        news_content=news_content, facts=facts, evidence=evidence, analysis=analysis, judge_json=judge_json,
        timeline=timeline, latest_updates=latest_updates, visualization=visualization, current_time=current_time)
    response = model.generate_content(prompt)
    return response.text

# Main process
def verynews_news_judge(news_content: str, config: dict = None) -> dict:
    current_time = datetime.utcnow().isoformat() + "Z"
    news_en = news_translate_to_en(news_content)
    facts = agent_5w1h(news_en, current_time)
    search_results = asyncio.run(agent_fact_check(news_en, facts, current_time))
    evidence = agent_evidence_aggregation(search_results, current_time)
    expert = agent_expert_analysis(news_en, facts, evidence, current_time)
    timeline, latest_updates = agent_timeliness(news_en, facts, evidence, current_time)
    judge = agent_judgement(news_en, facts, evidence, expert, latest_updates, current_time)
    visualization = agent_visualization(news_en, facts, evidence, expert, timeline, current_time)
    markdown_report = agent_report_expert(news_en, facts, evidence, expert, judge, timeline, latest_updates, visualization, current_time)
    return {
        "judge_json": judge,
        "markdown_report": markdown_report
    } 