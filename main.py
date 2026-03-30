"""
AI Content API - MVP with LLM Integration
FastAPI backend for content generation
"""
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import json
import time
from typing import Optional
from dotenv import load_dotenv
import httpx
import stripe
import json

# Load environment variables
load_dotenv()

app = FastAPI(title="AI Content API", version="0.3.0")

# CORS for web interface
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Stripe configuration
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# Pricing configuration
PRICING = {
    "basic": {
        "name": "Basic",
        "price_id": "price_1TGJmG8AZvg3KupHn8bOh4PE",
        "monthly": 9.99,
        "articles": 50,
        "features": [
            "50 AI-generated articles/month",
            "Up to 1000 words each",
            "3 content styles",
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
            "6 content styles",
            "Priority support",
            "Custom tones",
            "Advanced API"
        ]
    },
    "enterprise": {
        "name": "Enterprise",
        "price_id": "price_1TGJno8AZvg3KupH7LfjykRe",
        "monthly": 99.99,
        "articles": 1000,
        "features": [
            "1000+ AI articles/month",
            "Unlimited length",
            "All content styles",
            "Dedicated support",
            "Brand voice training",
            "Custom integrations"
        ]
    }
}

class ContentRequest(BaseModel):
    topic: str
    style: Optional[str] = "blog"
    length: Optional[str] = "medium"
    tone: Optional[str] = "professional"

class SubscriptionRequest(BaseModel):
    email: str
    tier: str = "basic"
    name: Optional[str] = ""

# LLM Content Generator
async def generate_ai_content(topic: str, style: str = "blog", length: str = "medium", tone: str = "professional") -> str:
    """Generate content using OpenRouter API"""
    if not OPENROUTER_API_KEY:
        return "Error: OpenRouter API key not configured"
    
    # Map length to token count
    length_map = {
        "short": 300,
        "medium": 800,
        "long": 1500
    }
    
    max_tokens = length_map.get(length, 800)
    
    prompt = f"""Write a {style} article about '{topic}' in a {tone} tone.
    
    Requirements:
    - Length: {length} ({max_tokens} words)
    - Style: {style}
    - Tone: {tone}
    - Include headings, paragraphs, and conclusion
    - SEO optimized
    - Engaging and informative
    
    Article:"""
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "openai/gpt-3.5-turbo",  # Cheapest option
                    "messages": [
                        {"role": "system", "content": "You are a professional content writer."},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.7
                },
                timeout=30.0
            )
            
            if response.status_code == 200:
                result = response.json()
                return result["choices"][0]["message"]["content"]
            else:
                return f"Error: {response.status_code} - {response.text}"
    
    except Exception as e:
        return f"Error generating content: {str(e)}"

@app.get("/")
async def root():
    return {
        "service": "AI Content API",
        "version": "0.3.0",
        "status": "online",
        "url": "https://ai-content-api.onrender.com",
        "documentation": "Send POST to /generate with JSON: {\"topic\":\"your topic\",\"style\":\"blog\",\"length\":\"medium\"}",
        "endpoints": {
            "/generate": "POST - Generate content",
            "/subscription": "POST - Create subscription",
            "/pricing": "GET - Pricing plans",
            "/health": "GET - Health check",
            "/customers": "GET - List customers",
            "/manual-activate": "POST - Manual activation (admin)"
        },
        "stripe": "Test mode active - use card 4242 4242 4242 4242"
    }

@app.post("/generate")
async def generate_content(request: ContentRequest):
    """Generate content for given topic using AI"""
    start_time = time.time()
    
    if not OPENROUTER_API_KEY:
        return {
            "status": "error",
            "content": "",
            "error": "OpenRouter API key not configured",
            "time_taken": 0
        }
    
    try:
        content = await generate_ai_content(
            topic=request.topic,
            style=request.style,
            length=request.length,
            tone=request.tone
        )
        
        time_taken = round(time.time() - start_time, 2)
        
        if content.startswith("Error"):
            return {
                "status": "error",
                "content": "",
                "error": content,
                "time_taken": time_taken
            }
        
        return {
            "status": "success",
            "content": content,
            "metadata": {
                "topic": request.topic,
                "style": request.style,
                "length": request.length,
                "tone": request.tone,
                "model": "gpt-3.5-turbo",
                "estimated_tokens": len(content.split()) * 1.3
            },
            "time_taken": time_taken
        }
    
    except Exception as e:
        return {
            "status": "error",
            "content": "",
            "error": str(e),
            "time_taken": round(time.time() - start_time, 2)
        }

@app.post("/subscription")
async def create_subscription(request: SubscriptionRequest):
    """Create a subscription with Stripe Checkout"""
    if not STRIPE_SECRET_KEY:
        return {
            "status": "error",
            "error": "Stripe not configured",
            "message": "Please set STRIPE_SECRET_KEY environment variable"
        }
    
    tier = request.tier.lower()
    if tier not in PRICING:
        tier = "basic"
    
    tier_info = PRICING[tier]
    
    try:
        # Create Stripe Checkout Session
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'gbp',
                    'product_data': {
                        'name': f'AI Content Labs - {tier_info["name"]} Plan',
                        'description': f'{tier_info["articles"]} AI-generated articles per month'
                    },
                    'unit_amount': int(tier_info['monthly'] * 100),  # Convert to pence
                    'recurring': {
                        'interval': 'month',
                    },
                },
                'quantity': 1,
            }],
            mode='subscription',
            success_url='https://ai-content-api.up.railway.app/success?session_id={CHECKOUT_SESSION_ID}',
            cancel_url='https://ai-content-api.up.railway.app/cancel',
            customer_email=request.email,
            metadata={
                'tier': tier,
                'customer_name': request.name or '',
                'plan_name': tier_info['name']
            }
        )
        
        return {
            "status": "success",
            "checkout_url": checkout_session.url,
            "session_id": checkout_session.id,
            "customer": {
                "email": request.email,
                "name": request.name,
                "tier": tier
            },
            "pricing": {
                "monthly": tier_info["monthly"],
                "articles": tier_info["articles"]
            }
        }
        
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "message": "Failed to create Stripe checkout session"
        }

@app.get("/pricing")
async def get_pricing():
    """Get available pricing plans"""
    return {
        "status": "success",
        "pricing": PRICING,
        "stripe_configured": bool(STRIPE_SECRET_KEY),
        "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY if STRIPE_PUBLISHABLE_KEY else None
    }

@app.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events"""
    if not STRIPE_WEBHOOK_SECRET:
        return {"error": "Webhook secret not configured"}
    
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        return {"error": "Invalid payload"}
    except stripe.error.SignatureVerificationError as e:
        return {"error": "Invalid signature"}
    
    # Handle the event
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        print(f"Payment succeeded for session: {session.id}")
        # TODO: Create user account, set subscription status
        # TODO: Send welcome email
        
    elif event['type'] == 'customer.subscription.created':
        subscription = event['data']['object']
        print(f"Subscription created: {subscription.id}")
        
    elif event['type'] == 'customer.subscription.deleted':
        subscription = event['data']['object']
        print(f"Subscription cancelled: {subscription.id}")
    
    return {"status": "success"}

# Simple database functions
def load_db():
    try:
        with open("database.json", "r") as f:
            return json.load(f)
    except:
        return {"customers": [], "subscriptions": [], "content_usage": {}}

def save_db(data):
    with open("database.json", "w") as f:
        json.dump(data, f, indent=2)

@app.post("/manual-activate")
async def manual_activate(email: str, tier: str, session_id: str):
    """Manual activation for testing (bypass webhook)"""
    db = load_db()
    
    customer = {
        "email": email,
        "tier": tier,
        "session_id": session_id,
        "activated_at": time.time(),
        "articles_used": 0,
        "articles_limit": PRICING[tier]["articles"] if tier in PRICING else 50
    }
    
    db["customers"].append(customer)
    save_db(db)
    
    return {
        "status": "success",
        "message": f"Customer {email} activated on {tier} plan",
        "limit": customer["articles_limit"],
        "api_key": f"test_key_{session_id[-8:]}"
    }

@app.get("/customers")
async def list_customers():
    """List all customers"""
    db = load_db()
    return {"status": "success", "customers": db["customers"]}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": "2026-03-29T14:00:00Z"}

# Catch-all for potential path prefix issues
@app.get("/{full_path:path}")
async def catch_all(full_path: str):
    if full_path == "" or full_path == "/":
        return await root()
    return {
        "error": "Endpoint not found",
        "requested_path": full_path,
        "available_endpoints": [
            "/generate (POST)",
            "/subscription (POST)", 
            "/pricing (GET)",
            "/health (GET)",
            "/customers (GET)",
            "/manual-activate (POST)"
        ]
    }

if __name__ == "__main__":
    import uvicorn
    import os
    # Use PORT environment variable for Render/Heroku compatibility
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)