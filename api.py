"""
LinkedIn Content Assistant - Multi-User Backend with Admin Panel
Complete version with authentication, admin controls, and cost management
"""

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from typing import List, Optional
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta
import os
import sys
import sqlite3
import json
import hashlib
import secrets

sys.path.insert(0, os.path.dirname(__file__))

from linkedin_assistant import ContentAssistant, TopicSuggestion

app = FastAPI(title="LinkedIn Content Assistant API - Multi-User with Admin")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

scheduler = None
db_path = "content_assistant_multiuser.db"

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserSettings(BaseModel):
    focus_areas: Optional[List[str]] = None
    target_audience: Optional[str] = None
    content_goals: Optional[List[str]] = None
    tone: Optional[str] = None
    monitoring_frequency: Optional[str] = None

class AdminUserUpdate(BaseModel):
    is_active: Optional[bool] = None
    monthly_limit: Optional[int] = None
    is_admin: Optional[bool] = None

class BrainstormRequest(BaseModel):
    topic_id: int
    user_input: str = ""

class TopicResponse(BaseModel):
    id: int
    title: str
    description: str
    relevance_score: int
    sources: List[str]
    key_points: List[str]
    suggested_angle: str
    created_at: str
    status: str

class BrainstormResponse(BaseModel):
    response: str
    topic_id: int

class AdminUserInfo(BaseModel):
    id: int
    email: str
    name: str
    created_at: str
    last_login: Optional[str]
    is_active: bool
    is_admin: bool
    monthly_limit: int
    current_month_usage: int
    monitoring_frequency: str

def init_multiuser_database():
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_login TEXT,
        is_active INTEGER DEFAULT 1,
        is_admin INTEGER DEFAULT 0,
        monthly_limit INTEGER DEFAULT 100)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_profiles (
        user_id INTEGER PRIMARY KEY,
        focus_areas TEXT NOT NULL,
        target_audience TEXT,
        content_goals TEXT,
        tone TEXT,
        monitoring_frequency TEXT DEFAULT 'weekly',
        FOREIGN KEY (user_id) REFERENCES users (id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS topics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        relevance_score INTEGER,
        sources TEXT,
        key_points TEXT,
        suggested_angle TEXT,
        created_at TEXT,
        status TEXT DEFAULT 'new',
        FOREIGN KEY (user_id) REFERENCES users (id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        topic_id INTEGER,
        content TEXT,
        version INTEGER DEFAULT 1,
        created_at TEXT,
        status TEXT DEFAULT 'draft',
        FOREIGN KEY (user_id) REFERENCES users (id),
        FOREIGN KEY (topic_id) REFERENCES topics (id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS usage_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        action_type TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        cost_estimate REAL DEFAULT 0.03,
        FOREIGN KEY (user_id) REFERENCES users (id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS activity_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        details TEXT,
        timestamp TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id))''')
    conn.commit()
    conn.close()

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def generate_token() -> str:
    return secrets.token_urlsafe(32)

def get_user_from_token(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.replace("Bearer ", "")
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''SELECT u.id, u.email, u.name, u.is_active, u.is_admin, u.monthly_limit, s.expires_at
        FROM sessions s JOIN users u ON s.user_id = u.id WHERE s.token = ?''', (token,))
    result = c.fetchone()
    conn.close()
    if not result:
        raise HTTPException(status_code=401, detail="Invalid token")
    user_id, email, name, is_active, is_admin, monthly_limit, expires_at = result
    if datetime.fromisoformat(expires_at) < datetime.now():
        raise HTTPException(status_code=401, detail="Token expired")
    if not is_active:
        raise HTTPException(status_code=403, detail="Account suspended. Contact administrator.")
    return {"id": user_id, "email": email, "name": name, "is_active": bool(is_active), "is_admin": bool(is_admin), "monthly_limit": monthly_limit}

def require_admin(user: dict = Depends(get_user_from_token)) -> dict:
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

def check_user_limit(user_id: int, monthly_limit: int):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    current_month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    c.execute('SELECT COUNT(*) FROM usage_log WHERE user_id = ? AND timestamp >= ?', (user_id, current_month_start.isoformat()))
    count = c.fetchone()[0]
    conn.close()
    if count >= monthly_limit:
        raise HTTPException(status_code=429, detail=f"Monthly limit of {monthly_limit} operations reached. Contact administrator.")

def log_usage(user_id: int, action_type: str, cost_estimate: float = 0.03):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('INSERT INTO usage_log (user_id, action_type, timestamp, cost_estimate) VALUES (?, ?, ?, ?)', (user_id, action_type, datetime.now().isoformat(), cost_estimate))
    conn.commit()
    conn.close()

def log_activity(user_id: int, action: str, details: str = None):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('INSERT INTO activity_log (user_id, action, details, timestamp) VALUES (?, ?, ?, ?)', (user_id, action, details, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_user_profile(user_id: int) -> dict:
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('SELECT focus_areas, target_audience, content_goals, tone, monitoring_frequency FROM user_profiles WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    if not result:
        return None
    return {"focus_areas": json.loads(result[0]), "target_audience": result[1], "content_goals": json.loads(result[2]), "tone": result[3], "monitoring_frequency": result[4]}

@app.on_event("startup")
async def startup_event():
    global scheduler
    init_multiuser_database()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("WARNING: ANTHROPIC_API_KEY not set")
        return
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        print("✓ Claude API key validated")
        scheduler = BackgroundScheduler()
        scheduler.add_job(run_all_user_monitoring, CronTrigger(hour=8, minute=0, timezone='Europe/London'), id='daily_monitoring', name='Daily monitoring for all users', replace_existing=True)
        scheduler.start()
        print("✓ Background scheduler started")
    except Exception as e:
        print(f"ERROR during startup: {str(e)}")

@app.post("/auth/signup")
async def signup(user: UserCreate):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    try:
        c.execute('SELECT COUNT(*) FROM users')
        user_count = c.fetchone()[0]
        is_first_user = user_count == 0
        c.execute('INSERT INTO users (email, password_hash, name, created_at, is_admin) VALUES (?, ?, ?, ?, ?)', (user.email, hash_password(user.password), user.name, datetime.now().isoformat(), 1 if is_first_user else 0))
        user_id = c.lastrowid
        default_profile = {"focus_areas": ["UK venture capital landscape", "Scaling businesses from Series A to IPO", "European vs US IPO markets", "Deeptech startups globally"], "target_audience": "Founders and leaders of scaling businesses", "content_goals": ["Provide actionable insights", "Share data-driven analysis", "Highlight market trends"], "tone": "Professional but accessible, data-driven", "monitoring_frequency": "weekly"}
        c.execute('INSERT INTO user_profiles (user_id, focus_areas, target_audience, content_goals, tone, monitoring_frequency) VALUES (?, ?, ?, ?, ?, ?)', (user_id, json.dumps(default_profile["focus_areas"]), default_profile["target_audience"], json.dumps(default_profile["content_goals"]), default_profile["tone"], default_profile["monitoring_frequency"]))
        conn.commit()
        token = generate_token()
        expires_at = datetime.now() + timedelta(days=30)
        c.execute('INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)', (token, user_id, datetime.now().isoformat(), expires_at.isoformat()))
        conn.commit()
        log_activity(user_id, "signup", f"New user registered: {user.name}")
        return {"token": token, "user": {"id": user_id, "email": user.email, "name": user.name, "is_admin": is_first_user}}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Email already registered")
    finally:
        conn.close()

@app.post("/auth/login")
async def login(credentials: UserLogin):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('SELECT id, name, password_hash, is_active, is_admin FROM users WHERE email = ?', (credentials.email,))
    result = c.fetchone()
    if not result or result[2] != hash_password(credentials.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    user_id, name, _, is_active, is_admin = result
    if not is_active:
        raise HTTPException(status_code=403, detail="Account suspended. Contact administrator.")
    c.execute('UPDATE users SET last_login = ? WHERE id = ?', (datetime.now().isoformat(), user_id))
    token = generate_token()
    expires_at = datetime.now() + timedelta(days=30)
    c.execute('INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)', (token, user_id, datetime.now().isoformat(), expires_at.isoformat()))
    conn.commit()
    conn.close()
    log_activity(user_id, "login", "User logged in")
    return {"token": token, "user": {"id": user_id, "email": credentials.email, "name": name, "is_admin": bool(is_admin)}}

@app.get("/profile")
async def get_profile(user: dict = Depends(get_user_from_token)):
    profile = get_user_profile(user["id"])
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile

@app.put("/profile")
async def update_profile(settings: UserSettings, user: dict = Depends(get_user_from_token)):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    updates = []
    values = []
    if settings.focus_areas is not None:
        updates.append("focus_areas = ?")
        values.append(json.dumps(settings.focus_areas))
    if settings.target_audience is not None:
        updates.append("target_audience = ?")
        values.append(settings.target_audience)
    if settings.content_goals is not None:
        updates.append("content_goals = ?")
        values.append(json.dumps(settings.content_goals))
    if settings.tone is not None:
        updates.append("tone = ?")
        values.append(settings.tone)
    if settings.monitoring_frequency is not None:
        if settings.monitoring_frequency not in ['daily', 'weekly', 'biweekly']:
            raise HTTPException(status_code=400, detail="Invalid frequency")
        updates.append("monitoring_frequency = ?")
        values.append(settings.monitoring_frequency)
    if updates:
        values.append(user["id"])
        query = f"UPDATE user_profiles SET {', '.join(updates)} WHERE user_id = ?"
        c.execute(query, values)
        conn.commit()
    conn.close()
    log_activity(user["id"], "update_profile", "User updated their profile settings")
    return {"message": "Profile updated successfully"}

@app.get("/digest", response_model=List[TopicResponse])
async def get_digest(days: int = 7, user: dict = Depends(get_user_from_token)):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
    c.execute("SELECT * FROM topics WHERE user_id = ? AND created_at > ? AND status = 'new' ORDER BY relevance_score DESC, created_at DESC", (user["id"], cutoff_date))
    rows = c.fetchall()
    conn.close()
    suggestions = []
    for row in rows:
        suggestions.append(TopicResponse(id=row[0], title=row[2], description=row[3], relevance_score=row[4], sources=json.loads(row[5]) if row[5] else [], key_points=json.loads(row[6]) if row[6] else [], suggested_angle=row[7], created_at=row[8], status=row[9]))
    return suggestions

@app.post("/monitor")
async def manual_monitoring(user: dict = Depends(get_user_from_token)):
    check_user_limit(user["id"], user["monthly_limit"])
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="API key not configured")
    
    try:
        profile = get_user_profile(user["id"])
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")
        
        # Call Claude directly instead of using ContentAssistant
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        
        search_queries = [
            "UK venture capital funding news",
            "European tech IPO 2025",
            "deeptech startup funding",
            "Series B Series C funding Europe",
            "UK tech scaleup news",
            "European vs US IPO comparison"
        ]
        
        monitoring_prompt = f"""You are monitoring industry news for a LinkedIn content creator focused on:
- {', '.join(profile['focus_areas'][:4])}

Target audience: {profile['target_audience']}

Your task:
1. Search for recent news (past week) on these topics using web search
2. Identify 3-5 stories that would make compelling LinkedIn posts
3. For each story, provide:
   - A catchy title
   - 2-3 sentence summary with key facts
   - Why it's relevant to the Target audience
   - Key data points or insights (with sources)
   - A suggested angle for the post
   - Relevance score (1-10)
   - IMPORTANT: List all source URLs you found this information from
   - Publication date if available

Return ONLY a JSON array (no other text) with this exact structure:
[
  {{
    "title": "Story headline",
    "description": "2-3 sentence summary",
    "relevance_score": 8,
    "sources": [
      {{"url": "https://...", "title": "Article title", "date": "2026-01-15"}},
      {{"url": "https://...", "title": "Article title", "date": "2026-01-14"}}
    ],
    "key_points": ["point 1 (Source: Publication Name)", "point 2 (Source: Publication Name)"],
    "suggested_angle": "How to position this"
  }}
]

Make sure every key point references its source publication.
"""
        
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": monitoring_prompt}]
        )
        
        # Extract text from response
        full_text = ""
        for block in message.content:
            if block.type == "text":
                full_text += block.text
        
        # Parse JSON
        suggestions = []
        try:
            start = full_text.find('[')
            end = full_text.rfind(']') + 1
            if start != -1 and end > start:
                json_str = full_text[start:end]
                data = json.loads(json_str)
                
                # Save directly to multi-user database
                conn = sqlite3.connect(db_path)
                c = conn.cursor()
                
                for item in data:
                    # Handle both old format (list of strings) and new format (list of objects)
                    sources = item.get('sources', [])
                    sources_json = json.dumps(sources)
                    
                    c.execute('''
                        INSERT INTO topics (user_id, title, description, relevance_score,
                                          sources, key_points, suggested_angle, created_at, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        user["id"],
                        item.get('title', ''),
                        item.get('description', ''),
                        item.get('relevance_score', 5),
                        sources_json,
                        json.dumps(item.get('key_points', [])),
                        item.get('suggested_angle', ''),
                        datetime.now().isoformat(),
                        'new'
                    ))
                    suggestions.append(item)
                
                conn.commit()
                conn.close()
        except json.JSONDecodeError:
            print("Could not parse JSON from response")
        
        log_usage(user["id"], "manual_monitoring")
        log_activity(user["id"], "manual_monitoring", f"Found {len(suggestions)} topics")
        
        return {
            "message": "Monitoring completed",
            "topics_found": len(suggestions),
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"ERROR in monitoring: {error_details}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/brainstorm", response_model=BrainstormResponse)
async def brainstorm(request: BrainstormRequest, user: dict = Depends(get_user_from_token)):
    check_user_limit(user["id"], user["monthly_limit"])
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="API key not configured")
    try:
        profile = get_user_profile(user["id"])
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute('SELECT * FROM topics WHERE id = ? AND user_id = ?', (request.topic_id, user["id"]))
        row = c.fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="Topic not found")
        brainstorm_prompt = f"""You are a LinkedIn content strategist helping create a post.

TOPIC: {row[2]}

CONTEXT: {row[3]}

KEY POINTS:
{chr(10).join('- ' + point for point in json.loads(row[6]))}

SUGGESTED ANGLE: {row[7]}

USER PROFILE:
- Focus: {', '.join(profile['focus_areas'][:3])}
- Audience: {profile['target_audience']}
- Tone: {profile['tone']}

USER INPUT: {request.user_input if request.user_input else "Help me draft a compelling LinkedIn post on this topic"}

Please help draft a LinkedIn post that:
1. Hooks the reader in the first line
2. Provides valuable insights backed by data
3. Offers actionable takeaways
4. Maintains the specified tone
5. Is ~150-250 words (optimal LinkedIn length)
6. Ends with a thought-provoking question or call-to-action"""
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        message = client.messages.create(model="claude-sonnet-4-20250514", max_tokens=2000, messages=[{"role": "user", "content": brainstorm_prompt}])
        response = message.content[0].text
        log_usage(user["id"], "brainstorm", 0.02)
        log_activity(user["id"], "brainstorm", f"Topic: {row[2]}")
        return BrainstormResponse(response=response, topic_id=request.topic_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/topics/{topic_id}/status")
async def update_topic_status(topic_id: int, status: str, user: dict = Depends(get_user_from_token)):
    valid_statuses = ['new', 'reviewed', 'drafted', 'published', 'archived']
    if status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}")
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('UPDATE topics SET status = ? WHERE id = ? AND user_id = ?', (status, topic_id, user["id"]))
    conn.commit()
    conn.close()
    return {"message": "Status updated", "topic_id": topic_id, "status": status}

@app.get("/admin/users", response_model=List[AdminUserInfo])
async def get_all_users(admin: dict = Depends(require_admin)):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    current_month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0)
    c.execute('SELECT u.id, u.email, u.name, u.created_at, u.last_login, u.is_active, u.is_admin, u.monthly_limit, up.monitoring_frequency FROM users u LEFT JOIN user_profiles up ON u.id = up.user_id ORDER BY u.created_at DESC')
    users = []
    for row in c.fetchall():
        user_id = row[0]
        c.execute('SELECT COUNT(*) FROM usage_log WHERE user_id = ? AND timestamp >= ?', (user_id, current_month_start.isoformat()))
        usage_count = c.fetchone()[0]
        users.append(AdminUserInfo(id=row[0], email=row[1], name=row[2], created_at=row[3], last_login=row[4], is_active=bool(row[5]), is_admin=bool(row[6]), monthly_limit=row[7], current_month_usage=usage_count, monitoring_frequency=row[8] or 'weekly'))
    conn.close()
    return users

@app.put("/admin/users/{user_id}")
async def update_user_admin(user_id: int, updates: AdminUserUpdate, admin: dict = Depends(require_admin)):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    update_fields = []
    values = []
    if updates.is_active is not None:
        update_fields.append("is_active = ?")
        values.append(1 if updates.is_active else 0)
    if updates.monthly_limit is not None:
        update_fields.append("monthly_limit = ?")
        values.append(updates.monthly_limit)
    if updates.is_admin is not None:
        update_fields.append("is_admin = ?")
        values.append(1 if updates.is_admin else 0)
    if update_fields:
        values.append(user_id)
        query = f"UPDATE users SET {', '.join(update_fields)} WHERE id = ?"
        c.execute(query, values)
        conn.commit()
    conn.close()
    log_activity(admin["id"], "admin_update_user", f"Updated user {user_id}: {updates.dict(exclude_none=True)}")
    return {"message": "User updated successfully"}

@app.get("/admin/activity")
async def get_activity_log(limit: int = 100, admin: dict = Depends(require_admin)):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('SELECT a.timestamp, u.name, u.email, a.action, a.details FROM activity_log a JOIN users u ON a.user_id = u.id ORDER BY a.timestamp DESC LIMIT ?', (limit,))
    activities = []
    for row in c.fetchall():
        activities.append({"timestamp": row[0], "user_name": row[1], "user_email": row[2], "action": row[3], "details": row[4]})
    conn.close()
    return activities

@app.get("/admin/usage-stats")
async def get_usage_stats(admin: dict = Depends(require_admin)):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    current_month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0)
    c.execute('SELECT COUNT(*) FROM users')
    total_users = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM users WHERE last_login >= ?', (current_month_start.isoformat(),))
    active_users = c.fetchone()[0]
    c.execute('SELECT COUNT(*), SUM(cost_estimate) FROM usage_log WHERE timestamp >= ?', (current_month_start.isoformat(),))
    api_calls, total_cost = c.fetchone()
    c.execute('SELECT action_type, COUNT(*), SUM(cost_estimate) FROM usage_log WHERE timestamp >= ? GROUP BY action_type', (current_month_start.isoformat(),))
    usage_by_type = {}
    for row in c.fetchall():
        usage_by_type[row[0]] = {"count": row[1], "cost": row[2]}
    conn.close()
    return {"total_users": total_users, "active_users_this_month": active_users, "api_calls_this_month": api_calls or 0, "estimated_cost_this_month": round(total_cost or 0, 2), "usage_by_type": usage_by_type}

def run_all_user_monitoring():
    print(f"[{datetime.now()}] Running scheduled monitoring...")
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    today = datetime.now()
    day_of_week = today.weekday()
    current_month_start = today.replace(day=1, hour=0, minute=0, second=0)
    c.execute('SELECT u.id, u.is_active, u.monthly_limit, up.focus_areas, up.target_audience, up.content_goals, up.tone, up.monitoring_frequency FROM users u JOIN user_profiles up ON u.id = up.user_id WHERE u.is_active = 1')
    users = c.fetchall()
    for user_row in users:
        user_id, is_active, monthly_limit, focus_areas, target_audience, content_goals, tone, frequency = user_row
        c.execute('SELECT COUNT(*) FROM usage_log WHERE user_id = ? AND timestamp >= ?', (user_id, current_month_start.isoformat()))
        usage_count = c.fetchone()[0]
        if usage_count >= monthly_limit:
            print(f"  User {user_id}: Monthly limit reached ({usage_count}/{monthly_limit})")
            continue
        should_run = False
        if frequency == 'daily':
            should_run = True
        elif frequency == 'weekly' and day_of_week == 0:
            should_run = True
        elif frequency == 'biweekly' and day_of_week == 0 and today.day <= 14:
            should_run = True
        if not should_run:
            continue
        try:
            profile = {"focus_areas": json.loads(focus_areas), "target_audience": target_audience, "content_goals": json.loads(content_goals), "tone": tone}
            temp_assistant = ContentAssistant(api_key, db_path=":memory:")
            temp_assistant.user_profile = profile
            suggestions = temp_assistant.monitor_industry_news()
            for suggestion in suggestions:
                c.execute('INSERT INTO topics (user_id, title, description, relevance_score, sources, key_points, suggested_angle, created_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (user_id, suggestion.title, suggestion.description, suggestion.relevance_score, json.dumps(suggestion.sources), json.dumps(suggestion.key_points), suggestion.suggested_angle, suggestion.created_at, suggestion.status))
            c.execute('INSERT INTO usage_log (user_id, action_type, timestamp, cost_estimate) VALUES (?, ?, ?, ?)', (user_id, "scheduled_monitoring", datetime.now().isoformat(), 0.03))
            conn.commit()
            print(f"  User {user_id}: Found {len(suggestions)} topics")
        except Exception as e:
            print(f"  Error for user {user_id}: {str(e)}")
    conn.close()

@app.get("/")
async def root():
    return {"message": "LinkedIn Content Assistant API - Multi-User with Admin", "version": "2.0", "features": ["authentication", "personalized_profiles", "admin_panel", "cost_controls"]}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "scheduler_running": scheduler is not None and scheduler.running, "timestamp": datetime.now().isoformat()}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)