import os
import time
import json
from google import genai
from google.genai import types
from google.genai.errors import APIError

API_EXCEPTIONS = (APIError, ValueError)

# Initialize gemini client
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

def gemini(prompt: str, json_mode: bool = True, max_retries: int = 3) -> str:    
    config = None
    if json_mode:
        config = types.GenerateContentConfig(response_mime_type="application/json")
        
    delay = 10.0
    for attempt in range(max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=prompt,
                config=config
            )
            if not response.text:
                raise ValueError("Empty or invalid response from Gemini API")
            return response.text
        except API_EXCEPTIONS as e:
            if attempt == max_retries:
                raise e
            print(f"Gemini API call failed (attempt {attempt + 1}/{max_retries + 1}): {e}. Retrying in {delay} seconds...")
            time.sleep(delay)
            delay *= 2.0

def get_classification_prompt(records_to_classify: list) -> str:
    txns_json = json.dumps(records_to_classify, indent=2)
    prompt = f"""You are a financial transaction classifier. Classify the following transactions into one of these exact categories: Food, Shopping, Travel, Transport, Utilities, Cash Withdrawal, Entertainment, or Other.

Return a JSON object where the key is the 'idx' of the transaction (from the input list) and the value is the assigned category name. Do not include any explanation or markdown formatting, return ONLY the raw JSON object.

Transactions to classify:
{txns_json}
"""
    return prompt

def get_summary_prompt(stats: dict) -> str:
    stats_json = json.dumps(stats, indent=2)
    prompt = f"""You are a financial analyst. Produce a narrative summary in JSON format based on the following computed transaction statistics.
The JSON must contain exactly these keys:
- "spending_narrative": a 2-3 sentence spending narrative summarizing the trends and main categories of spending.
- "risk_level": "low", "medium", or "high" (string).

Do not include any explanation or markdown formatting, return ONLY the raw JSON object.

Transaction Statistics:
{stats_json}
"""
    return prompt

