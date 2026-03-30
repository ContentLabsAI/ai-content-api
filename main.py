"""
AI Content Labs - WriteAI API v0.4.0
FastAPI backend with proper authentication and subscription enforcement
"""
from fastapi import FastAPI, HTTPException, Request, Response, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
import pathlib
from pydantic import BaseModel
import os
import json
import time
import secrets
import hashlib
from typing import Optional
from dotenv import load_dotenv
import httpx
import stripe

load_dotenv()

app = FastAPI(title="WriteAI - AI Content Labs", version="0.4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Config
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "change-me-in-production")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

PRICING = {
    "basic": {
        "name": "Basic",
        "price_id": "price_1TGJmG8AZvg3KupHn8bOh4PE",
        "monthly": 9.99,
        "articles": 50,
        "features": [
            "50 AI-generated articles/month",
            "Up to 1000 words each",
            "Blog, marketing & social styles",
            "Standard support",
            "API access"
        ]
    },
    "pro": {
        "name": "Pro",
        "price_id": "price_1TGJnK8AZvg3KupHfXEbvOyl",
        "monthly": 29.99,
        "articles": 200,
        "features": [
            "200 AI-generated articles/month",
            "Up to 2000 words each",
            "6 content styles + custom tones",
            "Priority support",
            "Advanced API access"
        ]
    },
    "enterprise": {
        "name": "Enterprise",
        "price_id": "price_1TGJno8AZvg3KupH7LfjykRe",
        "monthly": 99.99,
        "articles": 1000,
        "features": [
            "1000+ articles/month",
            "Unlimited length",
            "All styles + brand voice",
            "Dedicated support",
            "Custom integrations"
        ]
    }
}

# --- Models ---

class ContentRequest(BaseModel):
    topic: str
    style: str = "blog"
    length: str = "medium"
    tone: str = "professional"

class SubscriptionRequest(BaseModel):
    email: str
    tier: str = "basic"
    name: Optional[str] = None

# --- Database helpers ---

def load_db():
    try:
        with open("database.json", "r") as f:
            return json.load(f)
    except:
        return {"customers": {}, "api_keys": {}}

def save_db(data):
    with open("database.json", "w") as f:
        json.dump(data, f, indent=2)

def generate_api_key() -> str:
    return "wai_" + secrets.token_urlsafe(32)

def get_customer_by_api_key(api_key: str) -> Optional[dict]:
    db = load_db()
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    customer_email = db.get("api_keys", {}).get(key_hash)
    if customer_email:
        return db.get("customers", {}).get(customer_email)
    return None

# --- Auth dependency ---

async def require_api_key(x_api_key: Optional[str] = Header(None)) -> dict:
    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail="API key required. Include X-API-Key header. Get one at /pricing"
        )
    customer = get_customer_by_api_key(x_api_key)
    if not customer:
        raise HTTPException(status_code=403, detail="Invalid API key")
    if customer.get("status") != "active":
        raise HTTPException(status_code=403, detail="Subscription not active")
    return customer

async def require_admin(x_admin_secret: Optional[str] = Header(None)):
    if x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Admin access required")

# --- Content generation ---

async def generate_ai_content(topic: str, style: str = "blog", length: str = "medium", tone: str = "professional") -> str:
    if not OPENROUTER_API_KEY:
        raise HTTPException(status_code=503, detail="Content generation temporarily unavailable")

    length_map = {"short": 300, "medium": 800, "long": 1500}
    max_tokens = length_map.get(length, 800)

    style_instructions = {
        "blog": "a well-structured blog post with an engaging intro, clear sections with H2 headings, and a conclusion",
        "marketing": "persuasive marketing copy with a strong hook, benefit-focused language, and a clear call-to-action",
        "social": "punchy social media content with short paragraphs, emojis where appropriate, and shareable insights",
        "email": "an email newsletter with a compelling subject line suggestion, scannable sections, and clear next steps",
        "product": "a detailed product description highlighting features, benefits, and use cases",
        "seo": "SEO-optimised content with natural keyword usage, meta description suggestion, and clear structure"
    }

    style_desc = style_instructions.get(style, style_instructions["blog"])

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "HTTP-Referer": "https://writeai.contentlabs.ai",
                "X-Title": "WriteAI Content Generator"
            },
            json={
                "model": "openai/gpt-4o-mini",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are an expert content writer. Write high-quality, original content that sounds natural and human. Never mention that you are an AI."
                    },
                    {
                        "role": "user",
                        "content": f"Write {style_desc} about '{topic}' in a {tone} tone. Target length: ~{max_tokens} words. Make it genuinely useful and engaging."
                    }
                ],
                "max_tokens": max_tokens + 200
            }
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

# --- Routes ---

@app.get("/")
async def root():
    static_path = pathlib.Path("landing.html")
    if static_path.exists():
        return FileResponse("landing.html", media_type="text/html")
    return {"name": "WriteAI", "version": "0.4.0", "docs": "/pricing"}

@app.post("/generate")
async def generate_content(request: ContentRequest, customer: dict = Depends(require_api_key)):
    """Generate content - requires valid API key from active subscription"""
    start_time = time.time()

    # Check usage limits
    articles_used = customer.get("articles_used", 0)
    articles_limit = customer.get("articles_limit", 50)
    if articles_used >= articles_limit:
        raise HTTPException(
            status_code=429,
            detail=f"Monthly limit reached ({articles_used}/{articles_limit}). Upgrade your plan at /pricing"
        )

    content = await generate_ai_content(
        topic=request.topic,
        style=request.style,
        length=request.length,
        tone=request.tone
    )

    # Track usage
    db = load_db()
    email = customer["email"]
    if email in db.get("customers", {}):
        db["customers"][email]["articles_used"] = articles_used + 1
        save_db(db)

    return {
        "status": "success",
        "content": content,
        "metadata": {
            "topic": request.topic,
            "style": request.style,
            "length": request.length,
            "tone": request.tone
        },
        "usage": {
            "articles_used": articles_used + 1,
            "articles_limit": articles_limit,
            "remaining": articles_limit - articles_used - 1
        },
        "time_taken": round(time.time() - start_time, 2)
    }

@app.post("/subscription")
async def create_subscription(request: SubscriptionRequest):
    """Create a Stripe checkout session for subscription"""
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Payment processing unavailable")

    tier = request.tier.lower()
    if tier not in PRICING:
        tier = "basic"

    tier_info = PRICING[tier]

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "gbp",
                    "product_data": {
                        "name": f"WriteAI {tier_info['name']} Plan",
                        "description": f"{tier_info['articles']} AI-generated articles per month"
                    },
                    "unit_amount": int(tier_info["monthly"] * 100),
                    "recurring": {"interval": "month"},
                },
                "quantity": 1,
            }],
            mode="subscription",
            success_url=f"{os.getenv('BASE_URL', 'https://writeai.contentlabs.ai')}/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{os.getenv('BASE_URL', 'https://writeai.contentlabs.ai')}/pricing",
            customer_email=request.email,
            metadata={"tier": tier, "customer_name": request.name or ""}
        )

        return {
            "status": "success",
            "checkout_url": checkout_session.url,
            "session_id": checkout_session.id
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Payment error: {str(e)}")

@app.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook - creates API key on successful payment"""
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook not configured")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid webhook")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        email = session.get("customer_email", "")
        tier = session.get("metadata", {}).get("tier", "basic")
        tier_info = PRICING.get(tier, PRICING["basic"])

        if email:
            db = load_db()
            api_key = generate_api_key()
            key_hash = hashlib.sha256(api_key.encode()).hexdigest()

            db.setdefault("customers", {})[email] = {
                "email": email,
                "tier": tier,
                "status": "active",
                "stripe_session_id": session.get("id"),
                "articles_used": 0,
                "articles_limit": tier_info["articles"],
                "created_at": time.time(),
                "api_key_hash": key_hash
            }
            db.setdefault("api_keys", {})[key_hash] = email
            save_db(db)

            print(f"✅ New subscriber: {email} ({tier}) - API key: {api_key[:12]}...")
            # TODO: Send welcome email with API key

    elif event["type"] in ["customer.subscription.deleted", "customer.subscription.updated"]:
        # Handle cancellations/changes
        pass

    return {"status": "success"}

@app.get("/pricing")
async def get_pricing():
    return {
        "status": "success",
        "pricing": PRICING,
        "stripe_configured": bool(STRIPE_SECRET_KEY)
    }

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "0.4.0",
        "openrouter": bool(OPENROUTER_API_KEY),
        "stripe": bool(STRIPE_SECRET_KEY)
    }

@app.get("/success")
async def success_page(session_id: str = ""):
    html = """<!DOCTYPE html>
<html><head><title>Welcome to WriteAI</title>
<style>body{font-family:system-ui,sans-serif;max-width:600px;margin:80px auto;text-align:center;color:#1a1a2e}
h1{color:#4f46e5}p{color:#555;line-height:1.6}.btn{background:#4f46e5;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;display:inline-block;margin-top:20px}</style></head>
<body><h1>✅ You're in!</h1>
<p>Your WriteAI subscription is active. Your API key is being generated and will arrive in your inbox within 2 minutes.</p>
<p>Check your email for your API key and getting started guide.</p>
<a href="/pricing" class="btn">View Documentation</a></body></html>"""
    return HTMLResponse(content=html)

# Admin endpoints (protected)
@app.post("/admin/activate")
async def admin_activate(email: str, tier: str, _: None = Depends(require_admin)):
    """Manually activate a customer (admin only)"""
    tier_info = PRICING.get(tier, PRICING["basic"])
    db = load_db()

    api_key = generate_api_key()
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    db.setdefault("customers", {})[email] = {
        "email": email,
        "tier": tier,
        "status": "active",
        "articles_used": 0,
        "articles_limit": tier_info["articles"],
        "created_at": time.time(),
        "api_key_hash": key_hash,
        "manually_activated": True
    }
    db.setdefault("api_keys", {})[key_hash] = email
    save_db(db)

    return {
        "status": "success",
        "email": email,
        "tier": tier,
        "api_key": api_key,
        "limit": tier_info["articles"]
    }

@app.get("/admin/customers")
async def admin_customers(_: None = Depends(require_admin)):
    """List customers (admin only)"""
    db = load_db()
    customers = list(db.get("customers", {}).values())
    # Strip key hashes from response
    for c in customers:
        c.pop("api_key_hash", None)
    return {"status": "success", "customers": customers, "total": len(customers)}

if __name__ == "__main__":
    import uvicorn
    import sys
    port = 8000
    if len(sys.argv) > 1 and sys.argv[1] == "--port" and len(sys.argv) > 2:
        port = int(sys.argv[2])
    elif "PORT" in os.environ:
        port = int(os.environ.get("PORT"))
    uvicorn.run(app, host="0.0.0.0", port=port)
