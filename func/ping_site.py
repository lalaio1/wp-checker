from requests.exceptions import RequestException
import requests

def ping_site(site_url, timeout=4):
    try:
        response = requests.head(site_url, timeout=timeout, allow_redirects=False)
        return response.status_code == 200
    except RequestException:
        return False