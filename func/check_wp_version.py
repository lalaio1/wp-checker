from bs4 import BeautifulSoup
import requests
from requests.exceptions import RequestException

def check_wp_version(site_url, timeout=5):
    try:
        response = requests.get(site_url, timeout=timeout)
        soup = BeautifulSoup(response.text, 'html.parser')
        meta_tag = soup.find('meta', attrs={'name': 'generator'})
        if meta_tag and 'WordPress' in meta_tag.get('content', ''):
            return meta_tag['content']
        return "Unknown"
    except RequestException:
        return "Error"