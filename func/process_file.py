from alive_progress import alive_bar
import concurrent.futures
import re

from func.imports.init import *

def process_file(args):
    total_lines = sum(1 for _ in open(args.file, 'r', encoding='utf-8'))
    results = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.threads) as executor:
        futures = []
        
        with open(args.file, 'r') as file:
            for line in file:
                match = re.match(r'(https?://[^\s:]+):([^:]+):(.+)$', line.strip())
                if match:
                    url, username, password = match.groups()
                    futures.append(executor.submit(process_url, args, url, username, password))
        
        with alive_bar(len(futures), title='Processing') as bar:
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())
                bar()

    return results