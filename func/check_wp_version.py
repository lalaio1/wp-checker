import requests
from bs4 import BeautifulSoup
import re

def check_wp_version(url):
    if not url.startswith(('http://', 'https://')):
        url = 'http://' + url

    # Verificar se a URL contém /login.php
    if '/login.php' in url:
        try:
            login_response = requests.get(url, timeout=5)
            login_response.raise_for_status()
            if 'login.php' in login_response.url:
                print(f"Encontrado login.php em {url}. Tentando pegar informações...")
                soup = BeautifulSoup(login_response.text, 'html.parser')

                # Verificar pela meta tag 'generator' na página de login
                generator_meta = soup.find('meta', attrs={'name': 'generator'})
                if generator_meta and 'content' in generator_meta.attrs:
                    content = generator_meta['content']
                    match = re.search(r'WordPress\s+([\d.]+)', content, re.IGNORECASE)
                    if match:
                        return f"Versão encontrada no login.php: {match.group(1)}."
        except requests.RequestException:
            pass

    # Continuar verificando as páginas padrões se não for login.php
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
    except requests.RequestException as e:
        return None

    soup = BeautifulSoup(response.text, 'html.parser')

    # 1. Procurar pela meta tag 'generator' no cabeçalho
    generator_meta = soup.find('meta', attrs={'name': 'generator'})
    if generator_meta and 'content' in generator_meta.attrs:
        content = generator_meta['content']
        match = re.search(r'WordPress\s+([\d.]+)', content, re.IGNORECASE)
        if match:
            return f"Essa versão é {match.group(1)}."

    # 2. Verificar o arquivo readme.html
    try:
        readme_response = requests.get(url.rstrip('/') + '/readme.html', timeout=5)
        if readme_response.status_code == 200:
            readme_soup = BeautifulSoup(readme_response.text, 'html.parser')
            h1_tag = readme_soup.find('h1')
            if h1_tag and 'WordPress' in h1_tag.text:
                match = re.search(r'WordPress\s+([\d.]+)', h1_tag.text, re.IGNORECASE)
                if match:
                    return f"Versão encontrada no readme: {match.group(1)}."

    except requests.RequestException:
        pass

    # 3. Verificar o arquivo version.php no diretório wp-includes
    try:
        version_php_response = requests.get(url.rstrip('/') + '/wp-includes/version.php', timeout=5)
        if version_php_response.status_code == 200:
            match = re.search(r"\$wp_version\s*=\s*'([\d.]+)';", version_php_response.text)
            if match:
                return f"Achamos a versão direto no código: {match.group(1)}."

    except requests.RequestException:
        pass

    # 4. Verificar o feed RSS para informações do WordPress
    try:
        feed_response = requests.get(url.rstrip('/') + '/feed/', timeout=5)
        if feed_response.status_code == 200:
            feed_soup = BeautifulSoup(feed_response.text, 'xml')
            generator_tag = feed_soup.find('generator')
            if generator_tag and 'wordpress' in generator_tag.text.lower():
                match = re.search(r'wordpress\.org/\?v=([\d.]+)', generator_tag.text, re.IGNORECASE)
                if match:
                    return f"Versão encontrada no feed: {match.group(1)}."

    except requests.RequestException:
        pass
    except Exception as e:
        pass 
    
    return None
