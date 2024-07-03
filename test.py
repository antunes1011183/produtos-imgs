import requests
import json

BING_API_KEY = 'fd94e4427d7c4622919f8ac561818e94'
search_query = '7891000244449'
url = f"https://api.bing.microsoft.com/v7.0/images/search?q={search_query}"
headers = {
    'Ocp-Apim-Subscription-Key': BING_API_KEY
}

response = requests.get(url, headers=headers)

if response.status_code == 200:
    print("Request successful!")
    results = response.json()
    print(json.dumps(results, indent=4))
else:
    print("Request failed with status code:", response.status_code)
    print("Response:", response.text)
