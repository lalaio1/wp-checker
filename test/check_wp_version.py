import requests
from bs4 import BeautifulSoup
import re

def obter_versao_wordpress(url):
    # Garantir que a URL comece com http:// ou https://
    if not url.startswith(('http://', 'https://')):
        url = 'http://' + url

    try:
        # Fazer a requisição HTTP para a página principal
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Erro ao acessar {url}: {e}")
        return None

    # Analisar o conteúdo HTML da página
    soup = BeautifulSoup(response.text, 'html.parser')

    # 1. Verificar a meta tag 'generator' no cabeçalho
    generator_meta = soup.find('meta', attrs={'name': 'generator'})
    if generator_meta and 'content' in generator_meta.attrs:
        content = generator_meta['content']
        match = re.search(r'WordPress\s+([\d.]+)', content, re.IGNORECASE)
        if match:
            return match.group(1)

    # 2. Verificar o arquivo readme.html padrão do WordPress
    try:
        readme_response = requests.get(url.rstrip('/') + '/readme.html', timeout=10)
        if readme_response.status_code == 200:
            readme_soup = BeautifulSoup(readme_response.text, 'html.parser')
            h1_tag = readme_soup.find('h1')
            if h1_tag and 'WordPress' in h1_tag.text:
                match = re.search(r'WordPress\s+([\d.]+)', h1_tag.text, re.IGNORECASE)
                if match:
                    return match.group(1)
    except requests.RequestException:
        pass

    # 3. Verificar o arquivo version.php no diretório wp-includes
    try:
        version_php_response = requests.get(url.rstrip('/') + '/wp-includes/version.php', timeout=10)
        if version_php_response.status_code == 200:
            match = re.search(r"\$wp_version\s*=\s*'([\d.]+)';", version_php_response.text)
            if match:
                return match.group(1)
    except requests.RequestException:
        pass

    # 4. Verificar o feed RSS para a meta tag 'generator'
    try:
        feed_response = requests.get(url.rstrip('/') + '/feed/', timeout=10)
        if feed_response.status_code == 200:
            feed_soup = BeautifulSoup(feed_response.text, 'xml')  # Certifique-se de ter lxml instalado
            generator_tag = feed_soup.find('generator')
            if generator_tag and 'wordpress' in generator_tag.text.lower():
                match = re.search(r'wordpress\.org/\?v=([\d.]+)', generator_tag.text, re.IGNORECASE)
                if match:
                    return match.group(1)
    except requests.RequestException:
        pass
    except Exception as e:
        print(f"Erro ao processar o feed RSS: {e}")

    print(f"Não foi possível determinar a versão do WordPress para {url}")
    return None

def testing():
    url = input('WP site > ')
    versao = obter_versao_wordpress(url)
    if versao:
        print(f"O site {url} está rodando o WordPress versão {versao}.")
    else:
        print(f"Não foi possível determinar a versão do WordPress para o site {url}.")    

if __name__ == '__main__':
    testing()
