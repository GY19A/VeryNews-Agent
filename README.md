# VeryNews Multi-Agent News Authenticity Detection System

## Features
- Input news content, automatically extract 5W1H facts (Who, What, When, Where, Why, How)
- Multi-agent collaborative search and research based on a trusted site list
- Generate structured research reports (Markdown format)
- Automatically judge news authenticity, output conclusion (True, False, Partially True), sources and reasons (JSON format)
- Final output is a complete Markdown report with timestamp

## Usage
```python
from verynews.verynews_news_agent import verynews_news_judge
# https://x.com/Latinacl_01/status/1928408694905057331
news = "Breaking news: The Chinese Air Force shot down the US Air Force F-35 fighter jet during close reconnaissance!💪💪"
result = verynews_news_judge(news)
print(result['markdown_report'])
print(result['judge_json'])
```

## Dependencies
- Depends on the project's built-in multi-agent, search, config, utils modules
- Requires configuration of trusted sites in .env (SITES_TRUSTED_SOURCE)
- Requires Google/Tavily API Key configuration

## Directory Structure
- verynews_news_agent.py  Main process code
- README.md  This documentation file 