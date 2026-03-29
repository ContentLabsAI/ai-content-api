# AI Content API
## SaaS Platform for AI-Powered Content Generation

### Quick Start
```bash
# Local development
cd ai-content-api
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py

# API will run on http://localhost:8000
```

### Features
1. **Content Generation API** - POST /generate (OpenRouter AI integration)
2. **Subscription Management** - POST /subscription (Stripe Checkout)
3. **Payment Webhooks** - POST /stripe-webhook (Stripe event handling)
4. **Pricing API** - GET /pricing (Plan details)
5. **Health Monitoring** - GET /health

### Environment Variables
Create `.env` file:
```bash
OPENROUTER_API_KEY=your_openrouter_key
STRIPE_PUBLISHABLE_KEY=your_stripe_publishable_key
STRIPE_SECRET_KEY=your_stripe_secret_key
STRIPE_WEBHOOK_SECRET=your_webhook_secret
```

### Deployment to Railway
1. Push to GitHub
2. Connect to Railway
3. Set environment variables
4. Deploy

### Business Model
- **Basic:** £9.99/month - 50 articles
- **Pro:** £29.99/month - 200 articles  
- **Enterprise:** £99.99/month - 1000+ articles
- **Pay-per-use:** £0.99 per article

### Tech Stack
- FastAPI (Python)
- Stripe (Payments)
- OpenRouter (AI Generation)
- Railway (Hosting)
- HTML/CSS (Landing Page)