from dotenv import load_dotenv
load_dotenv()
from verynews_news_agent import verynews_news_judge

def main():
    news = """
    Smile（互Fo💯）
    @Latinacl_01
    ·
    May 30
    劲爆消息：中国空军击落了抵近侦查的美国空军F-35战机！💪💪
    Breaking news: The Chinese Air Force shot down the US Air Force F-35 fighter jet during close reconnaissance!💪💪
    """
    result = verynews_news_judge(news)
    print('--- Authenticity Judgement JSON ---')
    print(result['judge_json'])
    print('--- Markdown Research Report ---')
    print(result['markdown_report'])
    # Save Markdown report to local file
    with open('verynews_report.md', 'w', encoding='utf-8') as f:
        f.write(result['markdown_report'])
    print('Markdown report has been saved to verynews_report.md. You can open it with Notepad.')

if __name__ == "__main__":
    main() 