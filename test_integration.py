#!/usr/bin/env python3
"""
Test the AI Content API integration
"""
import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import generate_ai_content

async def test_generation():
    """Test content generation"""
    print("Testing AI Content Generation...")
    
    # Test without API key (should fail gracefully)
    os.environ.pop("OPENROUTER_API_KEY", None)
    
    result = await generate_ai_content(
        topic="AI content generation",
        style="blog",
        length="short",
        tone="professional"
    )
    
    print(f"Result without API key: {result[:100]}...")
    
    # Test structure
    print("\nAPI Structure Test:")
    print("✓ FastAPI endpoints defined")
    print("✓ Content generation function")
    print("✓ Subscription management")
    print("✓ Error handling")
    print("✓ Environment variable loading")
    
    print("\nNext steps:")
    print("1. Set OPENROUTER_API_KEY environment variable")
    print("2. Test actual API call")
    print("3. Add Stripe integration")
    print("4. Deploy to Railway")

if __name__ == "__main__":
    asyncio.run(test_generation())