from flask import Flask, request, jsonify, render_template
from bs4 import BeautifulSoup
import requests
import openai
from urllib.parse import urljoin
import os
import json
from functools import lru_cache
import re
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Import the DynamicScraperAPI class implementation
class DynamicScraperAPI:
    def __init__(self):
        self.openai_client = openai.Client(api_key=os.getenv('OPENAI_API_KEY'))
        self.cached_schemas = {}

    @lru_cache(maxsize=100)
    def analyze_page_structure(self, url):
        """Analyze the structure of a webpage and cache the results."""
        response = requests.get(url)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        structure = {
            'title': soup.title.string if soup.title else None,
            'headings': [h.text for h in soup.find_all(['h1', 'h2', 'h3'])],
            'main_content': self._identify_main_content(soup),
            'navigation': self._extract_navigation(soup),
            'schema': self._generate_page_schema(soup)
        }
        
        return structure

    def _identify_main_content(self, soup):
        main_content = soup.find('main') or soup.find(id='main') or soup.find(class_='main')
        if not main_content:
            content_tags = ['article', 'section', 'div']
            for tag in content_tags:
                candidates = soup.find_all(tag)
                if candidates:
                    main_content = max(candidates, key=lambda x: len(x.get_text()))
        
        return main_content.get_text() if main_content else None

    def _extract_navigation(self, soup):
        nav = soup.find('nav') or soup.find(class_='navigation') or soup.find(id='navigation')
        if nav:
            return [{'text': a.text, 'href': a.get('href')} for a in nav.find_all('a')]
        return None

    def _generate_page_schema(self, soup):
        schema = {
            'elements': {},
            'lists': [],
            'tables': [],
            'forms': []
        }
        
        for element in soup.find_all(class_=True):
            class_name = element.get('class')[0]
            if class_name not in schema['elements']:
                schema['elements'][class_name] = {
                    'count': 1,
                    'sample': element.get_text()[:100]
                }
            else:
                schema['elements'][class_name]['count'] += 1

        for ul in soup.find_all(['ul', 'ol']):
            schema['lists'].append({
                'type': ul.name,
                'items_count': len(ul.find_all('li')),
                'sample': ul.find('li').get_text() if ul.find('li') else None
            })

        for table in soup.find_all('table'):
            headers = [th.get_text() for th in table.find_all('th')]
            schema['tables'].append({
                'headers': headers,
                'rows_count': len(table.find_all('tr')) - 1 if headers else len(table.find_all('tr'))
            })

        return schema

    def generate_api_endpoint(self, url, query):
        structure = self.analyze_page_structure(url)
        prompt = self._create_selector_prompt(structure, query)
        
        response = self.openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are an expert in web scraping and API development."},
                {"role": "user", "content": prompt}
            ]
        )
        
        selectors = json.loads(response.choices[0].message.content)
        endpoint_config = {
            'url': url,
            'selectors': selectors,
            'query': query,
            'method': 'GET',
            'parameters': {
                'format': 'json',
                'cache_timeout': 3600
            }
        }
        
        endpoint_id = self._generate_endpoint_id(url, query)
        self.cached_schemas[endpoint_id] = endpoint_config
        
        return endpoint_id, endpoint_config

    def _create_selector_prompt(self, structure, query):
        return f"""
        Given the following webpage structure and user query, generate appropriate CSS/XPath selectors:

        Webpage Structure:
        {json.dumps(structure, indent=2)}

        User Query:
        {query}

        Return only a JSON object with the following structure:
        {{
            "selectors": [
                {{
                    "type": "css|xpath",
                    "selector": "string",
                    "attribute": "text|href|src|etc",
                    "description": "what this selector extracts"
                }}
            ],
            "preprocessing": [],
            "postprocessing": []
        }}
        """

    def _generate_endpoint_id(self, url, query):
        base = re.sub(r'[^a-zA-Z0-9]', '_', url.split('//')[1])
        query_hash = hash(query) % 10000
        return f"scrape_{base}_{query_hash}"

    def execute_scraping(self, endpoint_id):
        if endpoint_id not in self.cached_schemas:
            raise ValueError("Invalid endpoint ID")
            
        config = self.cached_schemas[endpoint_id]
        response = requests.get(config['url'])
        soup = BeautifulSoup(response.text, 'html.parser')
        
        results = []
        for selector in config['selectors']:
            if selector['type'] == 'css':
                elements = soup.select(selector['selector'])
            else:  # xpath
                elements = soup.find_all(string=re.compile(selector['selector']))
                
            for element in elements:
                if selector['attribute'] == 'text':
                    value = element.get_text().strip()
                else:
                    value = element.get(selector['attribute'])
                    
                if value:
                    results.append({
                        'type': selector['description'],
                        'value': value
                    })
                    
        return results

# Initialize the scraper
scraper = DynamicScraperAPI()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/analyze', methods=['POST'])
def analyze_webpage():
    data = request.json
    url = data.get('url')
    query = data.get('query')
    
    if not url or not query:
        return jsonify({'error': 'Missing url or query'}), 400
        
    endpoint_id, config = scraper.generate_api_endpoint(url, query)
    results = scraper.execute_scraping(endpoint_id)
    
    return jsonify({
        'endpoint': f'/api/scrape/{endpoint_id}',
        'config': config,
        'result': results
    })

@app.route('/api/scrape/<endpoint_id>', methods=['GET'])
def scrape_endpoint(endpoint_id):
    try:
        results = scraper.execute_scraping(endpoint_id)
        return jsonify(results)
    except ValueError as e:
        return jsonify({'error': str(e)}), 404

if __name__ == '__main__':
    app.run(debug=True)