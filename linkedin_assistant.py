"""
LinkedIn Content Assistant for UK VC & Scaling Businesses
Core backend implementation with Claude API integration
"""

import os
import json
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import anthropic
from dataclasses import dataclass, asdict
from enum import Enum

# Configuration
CLAUDE_MODEL = "claude-sonnet-4-5-20250929"

@dataclass
class TopicSuggestion:
    id: Optional[int]
    title: str
    description: str
    relevance_score: int  # 1-10
    sources: List[str]
    key_points: List[str]
    suggested_angle: str
    created_at: str
    status: str  # 'new', 'reviewed', 'drafted', 'published', 'archived'

@dataclass
class Post:
    id: Optional[int]
    topic_id: Optional[int]
    content: str
    version: int
    created_at: str
    status: str

class ContentAssistant:
    """Main class for LinkedIn content assistant"""
    
    def __init__(self, api_key: str, db_path: str = "content_assistant.db"):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.db_path = db_path
        self.init_database()
        
        # User profile - customize this
        self.user_profile = {
            "focus_areas": [
                "UK venture capital landscape",
                "Scaling businesses from Series A to IPO",
                "European vs US IPO markets",
                "Deeptech startups globally",
                "Growth strategies for tech companies"
            ],
            "target_audience": "Founders and leaders of scaling businesses (Series A+)",
            "content_goals": [
                "Provide actionable insights from market data",
                "Share comparative analysis of European vs US markets",
                "Highlight trends in deeptech funding",
                "Offer practical advice for scaling challenges"
            ],
            "tone": "Professional but accessible, data-driven, thoughtful",
            "avoid": [
                "Overly promotional content",
                "Generic startup advice",
                "Topics already extensively covered in past month"
            ]
        }
    
    def init_database(self):
        """Initialize SQLite database"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                relevance_score INTEGER,
                sources TEXT,
                key_points TEXT,
                suggested_angle TEXT,
                created_at TEXT,
                status TEXT DEFAULT 'new'
            )
        ''')
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic_id INTEGER,
                content TEXT,
                version INTEGER DEFAULT 1,
                created_at TEXT,
                status TEXT DEFAULT 'draft',
                FOREIGN KEY (topic_id) REFERENCES topics (id)
            )
        ''')
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS monitoring_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT,
                topics_found INTEGER,
                search_queries TEXT,
                notes TEXT
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def monitor_industry_news(self) -> List[TopicSuggestion]:
        """
        Daily monitoring job - searches for relevant news and generates topic suggestions
        Uses Claude with web search tool
        """
        search_queries = [
            "UK venture capital funding news",
            "European tech IPO 2025",
            "deeptech startup funding",
            "Series B Series C funding Europe",
            "UK tech scaleup news",
            "European vs US IPO comparison"
        ]
        
        monitoring_prompt = f"""You are monitoring industry news for a LinkedIn content creator focused on:
- UK venture capital
- Scaling businesses (Series A to IPO)
- European vs US IPO markets
- Deeptech businesses globally

Target audience: Founders and leaders of scaling businesses

Your task:
1. Search for recent news (past week) on these topics
2. Identify 3-5 stories that would make compelling LinkedIn posts
3. For each story, provide:
   - A catchy title
   - Why it's relevant to scaling business leaders
   - Key data points or insights
   - A suggested angle for the post
   - Relevance score (1-10)

Focus on stories with:
- New data or market insights
- Contrarian or surprising findings
- Actionable takeaways
- Comparative analysis opportunities

Search queries to use: {', '.join(search_queries)}

Return your findings as a JSON array with this structure:
[
  {{
    "title": "Story headline",
    "description": "2-3 sentence summary",
    "relevance_score": 8,
    "sources": ["url1", "url2"],
    "key_points": ["point 1", "point 2", "point 3"],
    "suggested_angle": "How to position this for maximum value"
  }}
]
"""
        
        # Make API call with web search enabled
        message = self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4000,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search"
            }],
            messages=[{
                "role": "user",
                "content": monitoring_prompt
            }]
        )
        
        # Extract suggestions from response
        suggestions = self._parse_monitoring_response(message)
        
        # Save to database
        for suggestion in suggestions:
            self._save_topic(suggestion)
        
        # Log the monitoring run
        self._log_monitoring_run(len(suggestions), search_queries)
        
        return suggestions
    
    def _parse_monitoring_response(self, message) -> List[TopicSuggestion]:
        """Parse Claude's response and extract topic suggestions"""
        suggestions = []
        
        # Extract text content from response
        full_text = ""
        for block in message.content:
            if block.type == "text":
                full_text += block.text
        
        # Try to extract JSON from the response
        try:
            # Look for JSON array in the response
            start = full_text.find('[')
            end = full_text.rfind(']') + 1
            if start != -1 and end > start:
                json_str = full_text[start:end]
                data = json.loads(json_str)
                
                for item in data:
                    suggestion = TopicSuggestion(
                        id=None,
                        title=item.get('title', ''),
                        description=item.get('description', ''),
                        relevance_score=item.get('relevance_score', 5),
                        sources=item.get('sources', []),
                        key_points=item.get('key_points', []),
                        suggested_angle=item.get('suggested_angle', ''),
                        created_at=datetime.now().isoformat(),
                        status='new'
                    )
                    suggestions.append(suggestion)
        except json.JSONDecodeError:
            print("Could not parse JSON from response")
        
        return suggestions
    
    def _save_topic(self, topic: TopicSuggestion):
        """Save topic suggestion to database"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''
            INSERT INTO topics (title, description, relevance_score, sources, 
                              key_points, suggested_angle, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            topic.title,
            topic.description,
            topic.relevance_score,
            json.dumps(topic.sources),
            json.dumps(topic.key_points),
            topic.suggested_angle,
            topic.created_at,
            topic.status
        ))
        
        conn.commit()
        conn.close()
    
    def _log_monitoring_run(self, topics_found: int, queries: List[str]):
        """Log monitoring run to database"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''
            INSERT INTO monitoring_log (run_date, topics_found, search_queries, notes)
            VALUES (?, ?, ?, ?)
        ''', (
            datetime.now().isoformat(),
            topics_found,
            json.dumps(queries),
            f"Found {topics_found} relevant topics"
        ))
        
        conn.commit()
        conn.close()
    
    def get_weekly_digest(self, days: int = 7) -> List[TopicSuggestion]:
        """Get topic suggestions from the past week"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
        
        c.execute('''
            SELECT * FROM topics 
            WHERE created_at > ? AND status = 'new'
            ORDER BY relevance_score DESC, created_at DESC
        ''', (cutoff_date,))
        
        rows = c.fetchall()
        conn.close()
        
        suggestions = []
        for row in rows:
            suggestion = TopicSuggestion(
                id=row[0],
                title=row[1],
                description=row[2],
                relevance_score=row[3],
                sources=json.loads(row[4]) if row[4] else [],
                key_points=json.loads(row[5]) if row[5] else [],
                suggested_angle=row[6],
                created_at=row[7],
                status=row[8]
            )
            suggestions.append(suggestion)
        
        return suggestions
    
    def brainstorm_post(self, topic_id: int, user_input: str = "") -> str:
        """
        Interactive brainstorming session for a specific topic
        """
        # Get topic from database
        topic = self._get_topic(topic_id)
        
        brainstorm_prompt = f"""You are a LinkedIn content strategist helping create a post.

TOPIC: {topic.title}

CONTEXT:
{topic.description}

KEY POINTS:
{chr(10).join('- ' + point for point in topic.key_points)}

SUGGESTED ANGLE:
{topic.suggested_angle}

USER PROFILE:
- Focus: UK VC, scaling businesses, European IPO markets, deeptech
- Audience: Founders and leaders of scaling businesses
- Tone: {self.user_profile['tone']}
- Goals: {', '.join(self.user_profile['content_goals'])}

USER INPUT: {user_input if user_input else "Help me draft a compelling LinkedIn post on this topic"}

Please help draft a LinkedIn post that:
1. Hooks the reader in the first line
2. Provides valuable insights backed by data
3. Offers actionable takeaways
4. Maintains a professional but engaging tone
5. Is ~150-250 words (optimal LinkedIn length)
6. Ends with a thought-provoking question or call-to-action

Include your reasoning about the approach you're taking."""
        
        message = self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": brainstorm_prompt
            }]
        )
        
        response = message.content[0].text
        return response
    
    def _get_topic(self, topic_id: int) -> TopicSuggestion:
        """Retrieve topic from database"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('SELECT * FROM topics WHERE id = ?', (topic_id,))
        row = c.fetchone()
        conn.close()
        
        if not row:
            raise ValueError(f"Topic {topic_id} not found")
        
        return TopicSuggestion(
            id=row[0],
            title=row[1],
            description=row[2],
            relevance_score=row[3],
            sources=json.loads(row[4]) if row[4] else [],
            key_points=json.loads(row[5]) if row[5] else [],
            suggested_angle=row[6],
            created_at=row[7],
            status=row[8]
        )
    
    def save_post(self, topic_id: int, content: str, status: str = "draft") -> int:
        """Save a post draft"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Get current version number
        c.execute('SELECT MAX(version) FROM posts WHERE topic_id = ?', (topic_id,))
        result = c.fetchone()
        version = (result[0] or 0) + 1
        
        c.execute('''
            INSERT INTO posts (topic_id, content, version, created_at, status)
            VALUES (?, ?, ?, ?, ?)
        ''', (topic_id, content, version, datetime.now().isoformat(), status))
        
        post_id = c.lastrowid
        conn.commit()
        conn.close()
        
        return post_id


# Example usage
if __name__ == "__main__":
    # Initialize assistant
    api_key = os.getenv("ANTHROPIC_API_KEY")
    assistant = ContentAssistant(api_key)
    
    # Simulate daily monitoring (you'd run this on a schedule)
    print("Running daily news monitoring...")
    suggestions = assistant.monitor_industry_news()
    print(f"Found {len(suggestions)} topic suggestions")
    
    # Get weekly digest
    print("\nWeekly digest:")
    digest = assistant.get_weekly_digest()
    for i, topic in enumerate(digest, 1):
        print(f"\n{i}. {topic.title} (Score: {topic.relevance_score}/10)")
        print(f"   {topic.description}")
    
    # Brainstorm on a topic
    if digest:
        print("\n" + "="*60)
        print("Brainstorming on top topic...")
        response = assistant.brainstorm_post(
            digest[0].id,
            "I want to emphasize the data points and make it actionable for Series B founders"
        )
        print(response)