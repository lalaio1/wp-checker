import asyncio
import aiofiles
import re
from multiprocessing import Pool
from alive_progress import alive_bar
from func.imports.init import *

url_pattern = re.compile(r'(https?://[^\s:]+):([^:]+):(.+)$')

def process_url_task(args, line):
    match = url_pattern.match(line.strip())
    if match:
        url, username, password = match.groups()
        return process_url(args, url, username, password)
    return None

def process_url_task_with_args(args_and_line):
    args, line = args_and_line
    return process_url_task(args, line)

async def process_file_async(args):
    results = []
    
    async def read_file(file_path):
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as file:
            return await file.readlines()

    try:
        lines = await read_file(args.file)
    except UnicodeDecodeError:
        lines = await read_file(args.file.replace(".txt", "_latin1.txt"))  
    except FileNotFoundError:
        print(f"Erro: O arquivo '{args.file}' não foi encontrado.")
        return []
    except Exception as e:
        print(f"Erro ao abrir o arquivo: {e}")
        return []

    lines = [line for line in lines if line.strip()]

    total_lines = len(lines)
    with Pool(processes=args.threads) as pool:
        with alive_bar(len(lines), title='Processing') as bar:
            results = []
            args_and_lines = [(args, line) for line in lines]
            for result in pool.imap_unordered(process_url_task_with_args, args_and_lines):
                if result:
                    results.append(result)
                bar()

    return results


def process_file(args):
    return asyncio.run(process_file_async(args))
