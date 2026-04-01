"""
WriteAI – AI Content Labs
FastAPI backend v0.5.0
Auth: email + password. No API key exposure to end users.
"""
from fastapi import FastAPI, HTTPException, Request, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import pathlib
from pydantic import BaseModel
import os, json, time, secrets, hashlib
from typing import Optional
from dotenv import load_dotenv
import httpx
import stripe

load_dotenv()

app = FastAPI(title="WriteAI", version="0.5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MANAGEMENT_KEY = os.getenv("OPENROUTER_MANAGEMENT_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_KEYS_URL = "https://openrouter.ai/api/v1/keys"
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "change-me-in-production")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

# Credit limits per plan (USD) — generous buffer above actual cost
PLAN_CREDIT_LIMITS = {
    "basic": 3.0,       # 50 articles x $0.0003 = $0.015 actual cost, $3 cap = 200x safety margin
    "pro": 10.0,        # 200 articles x $0.0003 = $0.06 actual, $10 cap
    "enterprise": 35.0  # 1000 articles x $0.0003 = $0.30 actual, $35 cap
}

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

PRICING = {
    "basic": {
        "name": "Basic",
        "price_id": "price_1TGzMb60lIeZHMRec2NgrTGr",
        "monthly": 9.99,
        "articles": 50,
        "features": ["50 articles/month", "Up to 1,000 words each", "All content types", "API access", "Standard support"]
    },
    "pro": {
        "name": "Pro",
        "price_id": "price_1TGzMc60lIeZHMRec7ZEbaWp",
        "monthly": 29.99,
        "articles": 200,
        "features": ["200 articles/month", "Up to 2,000 words each", "All content types", "Custom tones", "Priority support"]
    },
    "enterprise": {
        "name": "Enterprise",
        "price_id": "price_1TGzMc60lIeZHMReaaTwKsRK",
        "monthly": 99.99,
        "articles": 1000,
        "features": ["1,000+ articles/month", "Unlimited length", "Brand voice", "Custom integrations", "Dedicated support"]
    }
}

# ── Models ──

class ContentRequest(BaseModel):
    topic: str
    style: str = "blog"
    length: str = "medium"
    tone: str = "professional"

class SubscriptionRequest(BaseModel):
    email: str
    tier: str = "basic"
    name: Optional[str] = None

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = None

class LoginRequest(BaseModel):
    email: str
    password: str

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

# ── Database ──

DB_PATH = "database.json"

def load_db():
    try:
        with open(DB_PATH) as f:
            return json.load(f)
    except:
        return {"users": {}, "sessions": {}}

def save_db(data):
    with open(DB_PATH, "w") as f:
        json.dump(data, f, indent=2)

def hash_password(password: str) -> str:
    salt = "writeai_salt_2026"  # in production use bcrypt
    return hashlib.sha256(f"{salt}{password}".encode()).hexdigest()

def make_session_token() -> str:
    return secrets.token_urlsafe(32)

def get_user_by_email(email: str) -> Optional[dict]:
    db = load_db()
    return db.get("users", {}).get(email.lower())

def get_user_by_session(token: str) -> Optional[dict]:
    db = load_db()
    session = db.get("sessions", {}).get(token)
    if not session:
        return None
    if session.get("expires_at", 0) < time.time():
        return None
    email = session.get("email")
    return db.get("users", {}).get(email)

# ── Auth dependency ──

async def require_session(x_auth_token: Optional[str] = Header(None)) -> dict:
    if not x_auth_token:
        raise HTTPException(status_code=401, detail="Not logged in")
    user = get_user_by_session(x_auth_token)
    if not user:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    if user.get("status") != "active":
        raise HTTPException(status_code=403, detail="No active subscription. Please subscribe to continue.")
    return user

async def require_admin(x_admin_secret: Optional[str] = Header(None)):
    if x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

# ── OpenRouter key provisioning ──

async def provision_openrouter_key(email: str, tier: str) -> dict:
    """Create a per-customer OpenRouter sub-key with a spending limit."""
    if not OPENROUTER_MANAGEMENT_KEY:
        return {"error": "No management key configured"}

    limit = PLAN_CREDIT_LIMITS.get(tier, 3.0)
    label = f"WriteAI:{email}:{tier}"

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            OPENROUTER_KEYS_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_MANAGEMENT_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "name": label,
                "label": label,
                "limit": limit,
                "limit_reset": "monthly"
            }
        )
        resp.raise_for_status()
        data = resp.json()
        # OpenRouter returns: {"key": "sk-or-v1-...", "data": {"hash": "...", "label": "sk-or-v1-trunc...", ...}}
        full_key = data.get("key", "")          # full key — only returned once at creation
        key_meta = data.get("data", {})
        return {
            "key": full_key,
            "hash": key_meta.get("hash", ""),
            "limit": limit
        }

async def delete_openrouter_key(key_hash: str):
    """Delete a customer's OpenRouter sub-key (on cancellation)."""
    if not OPENROUTER_MANAGEMENT_KEY or not key_hash:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        await client.delete(
            f"{OPENROUTER_KEYS_URL}/{key_hash}",
            headers={"Authorization": f"Bearer {OPENROUTER_MANAGEMENT_KEY}"}
        )

# ── Content generation ──

async def generate_ai_content(topic: str, style: str = "blog", length: str = "medium", tone: str = "professional", customer_key: str = None) -> str:
    api_key = customer_key or OPENROUTER_API_KEY
    if not api_key:
        raise HTTPException(status_code=503, detail="Content generation temporarily unavailable")

    length_map = {"short": 300, "medium": 800, "long": 1500}
    max_tokens = length_map.get(length, 800)

    style_instructions = {
        "blog": "a blog post with a strong opening, clear sections, and a definite point of view",
        "marketing": "marketing copy with a direct hook, specific benefit language, and a clear call to action",
        "social": "social media content that is punchy, direct, and shareable. Short paragraphs. No filler",
        "email": "an email newsletter with a subject line suggestion, scannable sections, and a clear next step",
        "product": "a product description that focuses on concrete features, real benefits, and who it is for",
        "seo": "an SEO article with a clear structure, natural keyword usage, and a meta description suggestion"
    }

    style_desc = style_instructions.get(style, style_instructions["blog"])

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://writehq.app",
                "X-Title": "WriteAI"
            },
            json={
                "model": "openai/gpt-4o-mini",
                "messages": [

                    {
                        "role": "system",
                        "content": (
                            "You are a professional human writer with genuine opinions and real experience. "
                            "Write exactly how a skilled journalist or blogger writes, not how a language model writes.\n\n"
                            "BANNED WORDS (never use): delve, tapestry, nuanced, multifaceted, robust, seamless, leverage, utilize, facilitate, landscape (metaphorical), ecosystem, realm, crucial, vital, groundbreaking, transformative, revolutionary, streamline, empower, unlock, spearhead, foster, paradigm, synergy, holistic, scalable\n\n"
                            "BANNED OPENERS: 'Notably,', 'Importantly,', 'Certainly,', 'Interestingly,', 'Of course,', 'Ultimately,', 'That said,', 'Moreover,', 'Furthermore,', 'Building on this,', 'It is worth noting', 'It is important to', 'When it comes to', 'At the end of the day', 'In conclusion', 'In summary', 'To summarize'\n\n"
                            "BANNED PUNCTUATION: no em dashes, no semicolons for rhythm\n\n"
                            "BANNED STRUCTURES: no rhetorical question openers, no inspirational closing sentences, no 'In conclusion' paragraphs, no sandwich paragraphs that restate the opener\n\n"
                            "REQUIRED:\n"
                            "- Take a clear position. Have an opinion. Do not hedge unless genuinely warranted\n"
                            "- Vary sentence length dramatically. Short sentences land hard. Longer ones build rhythm\n"
                            "- Use specific concrete detail: real numbers, real scenarios. Not 'many businesses' but 'a shop with five staff'\n"
                            "- Write 'use' not 'utilize', 'show' not 'demonstrate', 'help' not 'facilitate'\n"
                            "- Leave some things implicit. Do not explain every point twice\n"
                            "- Contractions are fine (it's, don't, you're, they've)\n"
                            "- Paragraphs can be one sentence if that's what fits\n"
                            "- No preemptive disclaimers. No excessive both-sidesing\n"
                            "- Never mention AI or that this was generated"
                        )
                    },
                    {
                        "role": "user",
                        "content": f"Write {style_desc} about '{topic}' in a {tone} tone. Target length: roughly {max_tokens} words. Open with something specific and concrete, not a definition or a statement about the topic's importance."
                    }
                ],
                "max_tokens": max_tokens + 200
            }
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

# ── Routes: Auth ──

@app.post("/auth/register")
async def register(req: RegisterRequest):
    """Register with email + password. Account inactive until payment."""
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    email = req.email.lower().strip()
    db = load_db()

    if email in db.get("users", {}):
        raise HTTPException(status_code=409, detail="An account with that email already exists. Please log in.")

    db.setdefault("users", {})[email] = {
        "email": email,
        "name": req.name or "",
        "password_hash": hash_password(req.password),
        "status": "pending",  # becomes "active" after payment
        "tier": None,
        "articles_used": 0,
        "articles_limit": 0,
        "created_at": time.time(),
        "stripe_session_id": None,
    }
    save_db(db)
    return {"status": "success", "message": "Account created. Complete your subscription to start writing."}

@app.post("/auth/login")
async def login(req: LoginRequest):
    """Log in and receive a session token. No API key needed."""
    email = req.email.lower().strip()
    user = get_user_by_email(email)

    if not user or user.get("password_hash") != hash_password(req.password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    token = make_session_token()
    db = load_db()
    db.setdefault("sessions", {})[token] = {
        "email": email,
        "expires_at": time.time() + (30 * 24 * 3600)  # 30 days
    }
    save_db(db)

    return {
        "status": "success",
        "tok": token,
        "user": {
            "email": user["email"],
            "name": user.get("name", ""),
            "tier": user.get("tier"),
            "status": user.get("status"),
            "articles_used": user.get("articles_used", 0),
            "articles_limit": user.get("articles_limit", 0),
        }
    }

@app.post("/auth/logout")
async def logout(x_auth_token: Optional[str] = Header(None)):
    if x_auth_token:
        db = load_db()
        db.get("sessions", {}).pop(x_auth_token, None)
        save_db(db)
    return {"status": "success"}

@app.get("/auth/me")
async def get_me(user: dict = Depends(require_session)):
    return {
        "email": user["email"],
        "name": user.get("name", ""),
        "tier": user.get("tier"),
        "status": user.get("status"),
        "articles_used": user.get("articles_used", 0),
        "articles_limit": user.get("articles_limit", 0),
        "remaining": user.get("articles_limit", 0) - user.get("articles_used", 0)
    }

# ── Routes: Content ──

@app.get("/history")
async def get_history(user: dict = Depends(require_session)):
    """Return the user's generation history."""
    db = load_db()
    history = db.get("history", {}).get(user["email"], [])
    # Return most recent first, cap at 50
    return {"status": "success", "history": list(reversed(history[-50:]))}

@app.post("/generate")
async def generate_content(request: ContentRequest, user: dict = Depends(require_session)):
    """Generate content. Requires active subscription session."""
    articles_used = user.get("articles_used", 0)
    articles_limit = user.get("articles_limit", 0)

    if articles_used >= articles_limit:
        raise HTTPException(
            status_code=429,
            detail=f"You've used all {articles_limit} articles this month. Upgrade your plan to continue."
        )

    start = time.time()
    # Use main key for all generation (sub-keys require funded management account)
    # Per-customer limits enforced via articles_used counter above
    # Re-read key at runtime in case env var wasn't available at startup
    api_key = OPENROUTER_API_KEY or os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="Content generation temporarily unavailable. Please try again shortly.")
    try:
        content = await generate_ai_content(request.topic, request.style, request.length, request.tone, customer_key=api_key)
    except HTTPException:
        raise
    except Exception as e:
        print(f"ERROR in generate_ai_content: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Generation failed: {type(e).__name__}: {str(e)[:200]}")

    # Update usage and save to history
    db = load_db()
    if user["email"] in db.get("users", {}):
        db["users"][user["email"]]["articles_used"] = articles_used + 1
        # Append to history
        entry = {
            "id": secrets.token_urlsafe(8),
            "topic": request.topic,
            "style": request.style,
            "length": request.length,
            "tone": request.tone,
            "content": content,
            "words": len(content.split()),
            "created_at": time.time()
        }
        db.setdefault("history", {}).setdefault(user["email"], []).append(entry)
        save_db(db)

    return {
        "status": "success",
        "content": content,
        "usage": {
            "articles_used": articles_used + 1,
            "articles_limit": articles_limit,
            "remaining": articles_limit - articles_used - 1
        },
        "time_taken": round(time.time() - start, 2)
    }

# ── Routes: Subscriptions ──

@app.post("/subscription")
async def create_subscription(request: SubscriptionRequest):
    """Create Stripe checkout. User can be registered or not yet."""
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Payment processing unavailable")

    tier = request.tier.lower()
    if tier not in PRICING:
        tier = "basic"
    tier_info = PRICING[tier]

    # Ensure user account exists (create one if they're going straight to checkout)
    email = request.email.lower().strip()
    db = load_db()
    if email not in db.get("users", {}):
        db.setdefault("users", {})[email] = {
            "email": email,
            "name": request.name or "",
            "password_hash": None,  # will be set when they create password after payment
            "status": "pending",
            "tier": None,
            "articles_used": 0,
            "articles_limit": 0,
            "created_at": time.time(),
            "stripe_session_id": None,
        }
        save_db(db)

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "gbp",
                    "product_data": {
                        "name": f"WriteAI {tier_info['name']} Plan",
                        "description": f"{tier_info['articles']} articles per month"
                    },
                    "unit_amount": int(tier_info["monthly"] * 100),
                    "recurring": {"interval": "month"},
                },
                "quantity": 1,
            }],
            mode="subscription",
            success_url=f"{BASE_URL}/setup?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{BASE_URL}/#pricing",
            customer_email=email,
            metadata={"tier": tier, "customer_name": request.name or ""}
        )
        return {"status": "success", "checkout_url": checkout_session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook not configured")

    payload = await request.body()
    sig = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid webhook")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        email = session.get("customer_email", "").lower()
        tier = session.get("metadata", {}).get("tier", "basic")
        tier_info = PRICING.get(tier, PRICING["basic"])

        if email:
            db = load_db()
            user = db.get("users", {}).get(email, {})

            # Provision a dedicated OpenRouter key for this customer
            or_key_data = await provision_openrouter_key(email, tier)
            customer_or_key = or_key_data.get("key", "")
            customer_or_hash = or_key_data.get("hash", "")

            if or_key_data.get("error"):
                print(f"WARNING: Could not provision OpenRouter key for {email}: {or_key_data['error']}")
                customer_or_key = OPENROUTER_API_KEY  # fallback to shared key

            user.update({
                "email": email,
                "status": "active",
                "tier": tier,
                "articles_limit": tier_info["articles"],
                "articles_used": 0,
                "stripe_session_id": session.get("id"),
                "activated_at": time.time(),
                "openrouter_key": customer_or_key,
                "openrouter_key_hash": customer_or_hash
            })
            db.setdefault("users", {})[email] = user
            save_db(db)
            print(f"Activated: {email} on {tier} | OR key: {customer_or_key[:12] if customer_or_key else 'NONE'}...")

    elif event["type"] == "customer.subscription.deleted":
        # Clean up customer's OpenRouter key on cancellation
        sub = event["data"]["object"]
        customer_email = sub.get("metadata", {}).get("email", "")
        if customer_email:
            db = load_db()
            user = db.get("users", {}).get(customer_email.lower(), {})
            if user.get("openrouter_key_hash"):
                await delete_openrouter_key(user["openrouter_key_hash"])
            user["status"] = "cancelled"
            db["users"][customer_email.lower()] = user
            save_db(db)
            print(f"Cancelled: {customer_email}")

    return {"status": "success"}

@app.get("/pricing")
async def get_pricing():
    return {"status": "success", "pricing": PRICING}

@app.get("/health")
async def health():
    or_key = OPENROUTER_API_KEY or os.getenv("OPENROUTER_API_KEY", "")
    stripe_key = STRIPE_SECRET_KEY or os.getenv("STRIPE_SECRET_KEY", "")
    return {"status": "healthy", "version": "0.6.2", "openrouter": bool(or_key), "stripe": bool(stripe_key)}

# ── Pages ──

@app.get("/")
async def root():
    p = pathlib.Path("landing.html")
    if p.exists():
        return FileResponse("landing.html", media_type="text/html")
    return {"name": "WriteAI", "version": "0.5.0"}

@app.get("/editor")
async def editor():
    p = pathlib.Path("editor.html")
    if p.exists():
        return FileResponse("editor.html", media_type="text/html")
    raise HTTPException(status_code=404)

@app.get("/setup")
async def setup_page(session_id: str = ""):
    """After payment: collect password if account doesn't have one yet."""
    p = pathlib.Path("setup.html")
    if p.exists():
        return FileResponse("setup.html", media_type="text/html")
    # Fallback
    html = """<!DOCTYPE html><html><head><title>Welcome to WriteAI</title>
    <style>body{font-family:system-ui,sans-serif;max-width:500px;margin:80px auto;text-align:center;color:#111}
    h1{font-size:32px;font-weight:800;margin-bottom:12px}p{color:#555;margin-bottom:24px}
    a{background:#a3e635;color:#000;padding:13px 28px;border-radius:8px;text-decoration:none;font-weight:700}</style></head>
    <body><h1>You're in.</h1><p>Check your email — your account details are on their way.</p>
    <a href="/editor">Open the editor</a></body></html>"""
    return HTMLResponse(content=html)

@app.post("/auth/set-password")
async def set_password(request: Request):
    """Called from the setup page after first payment to set a password."""
    body = await request.json()
    email = body.get("email", "").lower().strip()
    session_id = body.get("session_id", "")
    password = body.get("password", "")

    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    db = load_db()
    user = db.get("users", {}).get(email)

    if not user:
        raise HTTPException(status_code=404, detail="Account not found")

    # Verify session_id matches (basic check)
    if user.get("stripe_session_id") and user["stripe_session_id"] != session_id:
        raise HTTPException(status_code=403, detail="Invalid setup link")

    user["password_hash"] = hash_password(password)
    db["users"][email] = user
    save_db(db)

    # Auto-login
    token = make_session_token()
    db.setdefault("sessions", {})[token] = {
        "email": email,
        "expires_at": time.time() + (30 * 24 * 3600)
    }
    save_db(db)

    return {
        "status": "success",
        "tok": token,
        "user": {
            "email": user["email"],
            "name": user.get("name", ""),
            "tier": user.get("tier"),
            "articles_used": user.get("articles_used", 0),
            "articles_limit": user.get("articles_limit", 0),
        }
    }

# ── Admin ──

@app.post("/admin/activate")
async def admin_activate(email: str, tier: str, password: str = None, _: None = Depends(require_admin)):
    tier_info = PRICING.get(tier, PRICING["basic"])
    db = load_db()
    user = db.get("users", {}).get(email.lower(), {})

    # Provision OpenRouter key
    or_key_data = await provision_openrouter_key(email, tier)
    customer_or_key = or_key_data.get("key", "") or OPENROUTER_API_KEY

    temp_pass = password or secrets.token_urlsafe(10)
    user.update({
        "email": email.lower(),
        "status": "active",
        "tier": tier,
        "articles_limit": tier_info["articles"],
        "articles_used": 0,
        "activated_at": time.time(),
        "manually_activated": True,
        "password_hash": hash_password(temp_pass),
        "openrouter_key": customer_or_key,
        "openrouter_key_hash": or_key_data.get("hash", "")
    })
    db.setdefault("users", {})[email.lower()] = user
    save_db(db)
    return {
        "status": "success",
        "email": email,
        "tier": tier,
        "password": temp_pass,
        "openrouter_key_provisioned": bool(or_key_data.get("key"))
    }

@app.get("/admin/users")
async def admin_users(_: None = Depends(require_admin)):
    db = load_db()
    users = [
        {k: v for k, v in u.items() if k not in ("password_hash",)}
        for u in db.get("users", {}).values()
    ]
    return {"status": "success", "users": users, "total": len(users)}

if __name__ == "__main__":
    import uvicorn, sys
    port = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[1] == "--port" else int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
