from alive_progress import alive_bar
import concurrent.futures
import re

from func.imports.init import *

# -== Falhas previnidas 
def process_file(args):
    results = []
    
    try:
        with open(args.file, 'r', encoding='utf-8') as file:
            lines = file.readlines()
    except UnicodeDecodeError:
        with open(args.file, 'r', encoding='ISO-8859-1') as file:
            lines = file.readlines()
    except FileNotFoundError:
        print(f"Erro: O arquivo '{args.file}' não foi encontrado.")
        return []
    except Exception as e:
        print(f"Erro ao abrir o arquivo: {e}")
        return []

    total_lines = len(lines)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.threads) as executor:
        futures = []
        
        for line in lines:
            match = re.match(r'(https?://[^\s:]+):([^:]+):(.+)$', line.strip())
            if match:
                url, username, password = match.groups()
                futures.append(executor.submit(process_url, args, url, username, password))
        
        with alive_bar(len(futures), title='Processing') as bar:
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())
                bar()

    return results
