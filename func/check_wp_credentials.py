import requests
from requests.exceptions import RequestException

def check_wp_credentials(site_url, username, password, timeout=10):
    login_url = f"{site_url}/wp-login.php"
    admin_url = f"{site_url}/wp-admin/"
    
    session = requests.Session()
    try:
        response = session.get(login_url, timeout=timeout)
        response.raise_for_status()
        
        login_data = {
            'log': username,
            'pwd': password,
            'wp-submit': 'Log In',
            'redirect_to': admin_url,
            'testcookie': '1'
        }
        
        response = session.post(login_url, data=login_data, allow_redirects=False, timeout=timeout)
        if response.status_code == 302 and 'wp-admin' in response.headers.get('Location', ''):
            return True
        else:
            return False
    except RequestException:
        return False
    finally:
        session.close()