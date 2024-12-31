import requests
from requests.exceptions import RequestException
from bs4 import BeautifulSoup
import datetime

def login(site_url, username, password, timeout=10):
    url = f"{site_url}/wp-login.php"
    session = requests.Session()
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        data = {'log': username, 'pwd': password, 'wp-submit': 'Log In', 'redirect_to': f"{site_url}/wp-admin/", 'testcookie': '1'}
        resp = session.post(url, data=data, allow_redirects=False, timeout=timeout)
        if resp.status_code == 302 and 'wp-admin' in resp.headers.get('Location', ''):
            return session
        else:
            print("[ERRO] Login falhou!")
            return None
    except RequestException as e:
        print(f"[ERRO] Conexão falhou: {e}")
        return None

def plugins_ativos(session, site_url, timeout=10):
    start_time = datetime.datetime.now()  # Inicia a medição de tempo
    url = f"{site_url}/wp-admin/plugins.php"
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        active_plugins = soup.find_all(string=lambda text: text and 'Ativado' in text)
        if active_plugins:
            print(f"[INFO] {len(active_plugins)} plugins ativados.")
        else:
            print("[INFO] Nenhum plugin ativado.")
    except RequestException as e:
        print(f"[ERRO] Falha ao acessar plugins: {e}")
    end_time = datetime.datetime.now()  # Finaliza a medição de tempo
    print(f"[INFO] Tempo de execução (Plugins Ativos): {end_time - start_time}")

def add_plugin_btn(session, site_url, timeout=10):
    start_time = datetime.datetime.now()  # Inicia a medição de tempo
    url = f"{site_url}/wp-admin/plugins.php"
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        add_btn = soup.find('a', string=lambda text: text and 'Adicionar Novo' in text)
        if add_btn:
            print("[INFO] Permissão para adicionar plugins.")
        else:
            print("[INFO] Não encontrado o botão plugin 'Adicionar Novo'.")
    except RequestException as e:
        print(f"[ERRO] Falha ao verificar o botão: {e}")
    end_time = datetime.datetime.now()  # Finaliza a medição de tempo
    print(f"[INFO] Tempo de execução (Adicionar Novo Plugin): {end_time - start_time}")

def acessar_plugins(site_url, username, password, timeout=10):
    start_time = datetime.datetime.now()  # Inicia a medição de tempo
    print("[INFO] Tentando logar...")
    session = login(site_url, username, password, timeout)
    if session:
        plugins_ativos(session, site_url, timeout)
        add_plugin_btn(session, site_url, timeout)
    end_time = datetime.datetime.now()  # Finaliza a medição de tempo
    print(f"[INFO] Tempo total de execução: {end_time - start_time}")

if __name__ == "__main__":
    site_url = input("Site URL: ")
    username = input("Usuário: ")
    password = input("Senha: ")
    acessar_plugins(site_url, username, password)