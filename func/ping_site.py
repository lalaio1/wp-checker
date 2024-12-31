import socket
from urllib.parse import urlparse

def ping_site(site_url, timeout=4):
    parsed_url = urlparse(site_url)
    host = parsed_url.netloc
    port = 443 if parsed_url.scheme == 'https' else 80  

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, socket.error):
        return False