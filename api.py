"""
Scheduler and FastAPI implementation for LinkedIn Content Assistant
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import schedule
import time
import threading
from datetime import datetime

# Import the ContentAssistant from previous file
# from linkedin_assistant import ContentAssistant, TopicSuggestion

app = FastAPI(title="LinkedIn Content Assistant API")

# Enable CORS for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize assistant (in production, use environment variables)
assistant = None

# Pydantic models for API requests/responses
class BrainstormRequest(BaseModel):
    topic_id: int
    user_input: str = ""

class SavePostRequest(BaseModel):
    topic_id: int
    content: str
    status: str = "draft"

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

# API Endpoints

@app.on_event("startup")
async def startup_event():
    """Initialize the assistant on startup"""
    global assistant
    import os
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable not set")
    
    # Import here to avoid circular dependency
    from linkedin_assistant import ContentAssistant
    assistant = ContentAssistant(api_key)
    
    # Start background scheduler
    start_scheduler()

@app.get("/")
async def root():
    return {
        "message": "LinkedIn Content Assistant API",
        "version": "1.0",
        "endpoints": {
            "digest": "/digest",
            "brainstorm": "/brainstorm",
            "save_post": "/posts",
            "manual_monitoring": "/monitor"
        }
    }

@app.get("/digest", response_model=List[TopicResponse])
async def get_digest(days: int = 7):
    """Get topic suggestions from the past week"""
    try:
        suggestions = assistant.get_weekly_digest(days)
        return [
            TopicResponse(
                id=s.id,
                title=s.title,
                description=s.description,
                relevance_score=s.relevance_score,
                sources=s.sources,
                key_points=s.key_points,
                suggested_angle=s.suggested_angle,
                created_at=s.created_at,
                status=s.status
            )
            for s in suggestions
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/brainstorm", response_model=BrainstormResponse)
async def brainstorm(request: BrainstormRequest):
    """Start an interactive brainstorming session on a topic"""
    try:
        response = assistant.brainstorm_post(
            request.topic_id,
            request.user_input
        )
        return BrainstormResponse(
            response=response,
            topic_id=request.topic_id
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/posts")
async def save_post(request: SavePostRequest):
    """Save a post draft"""
    try:
        post_id = assistant.save_post(
            request.topic_id,
            request.content,
            request.status
        )
        return {
            "message": "Post saved successfully",
            "post_id": post_id,
            "topic_id": request.topic_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/monitor")
async def manual_monitoring():
    """Manually trigger news monitoring (useful for testing)"""
    try:
        suggestions = assistant.monitor_industry_news()
        return {
            "message": "Monitoring completed",
            "topics_found": len(suggestions),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/topics/{topic_id}/status")
async def update_topic_status(topic_id: int, status: str):
    """Update the status of a topic (new, reviewed, drafted, published, archived)"""
    valid_statuses = ['new', 'reviewed', 'drafted', 'published', 'archived']
    if status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
        )
    
    try:
        import sqlite3
        conn = sqlite3.connect(assistant.db_path)
        c = conn.cursor()
        c.execute('UPDATE topics SET status = ? WHERE id = ?', (status, topic_id))
        conn.commit()
        conn.close()
        
        return {"message": "Status updated", "topic_id": topic_id, "status": status}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Background Scheduler

def run_daily_monitoring():
    """Function to run daily monitoring"""
    print(f"[{datetime.now()}] Running scheduled monitoring...")
    try:
        suggestions = assistant.monitor_industry_news()
        print(f"[{datetime.now()}] Found {len(suggestions)} topics")
    except Exception as e:
        print(f"[{datetime.now()}] Error during monitoring: {str(e)}")

def schedule_runner():
    """Run the scheduler in a separate thread"""
    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute

def start_scheduler():
    """Start the background scheduler"""
    # Schedule daily monitoring at 8 AM
    schedule.every().day.at("08:00").do(run_daily_monitoring)
    
    # Start scheduler in background thread
    scheduler_thread = threading.Thread(target=schedule_runner, daemon=True)
    scheduler_thread.start()
    print("Background scheduler started - daily monitoring at 08:00")


# Alternative: Production-ready scheduler using APScheduler
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

def start_production_scheduler():
    scheduler = BackgroundScheduler()
    
    # Run monitoring every day at 8 AM
    scheduler.add_job(
        run_daily_monitoring,
        CronTrigger(hour=8, minute=0),
        id='daily_monitoring',
        name='Daily industry news monitoring',
        replace_existing=True
    )
    
    scheduler.start()
    print("Production scheduler started")
"""


if __name__ == "__main__":
    import uvicorn
    
    # Run the API server
    # In production, use: uvicorn api:app --host 0.0.0.0 --port 8000
    uvicorn.run(app, host="0.0.0.0", port=8000)