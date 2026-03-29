#!/bin/bash
echo "Testing AI Content API MVP"
echo "========================"

# Check Python installation
echo "Python version:"
python3 --version

# Try to run the API
echo -e "\nAttempting to run API..."
python3 main.py &
API_PID=$!

sleep 3

echo -e "\nTesting endpoints:"
curl -s http://localhost:8000/ | python3 -m json.tool

echo -e "\nTesting content generation:"
curl -s -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"topic": "AI content generation", "style": "blog", "length": "medium"}' \
  | python3 -m json.tool

kill $API_PID 2>/dev/null
echo -e "\nTest complete."