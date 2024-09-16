#By: lalaio1

import argparse
import time
from pystyle import Colors, Write

# -=========================== init
from func.imports.init import * 

# -========================== global banner
from banner.banner import banner

# -========================== loggin config
from loggin.logging_config import setup_logging

# -================ Setup loggin
setup_logging()


# -==========================================================================================================================
def main():
    parser = argparse.ArgumentParser(description="Advanced WordPress Credential Checker")
    parser.add_argument("file", help="Path to the file with URLs and credentials")
    parser.add_argument("-v", "--valid", help="File to save valid credentials")
    parser.add_argument("-i", "--invalid", help="File to save invalid credentials")
    parser.add_argument("-off", "--offline", help="File to save offline sites")
    parser.add_argument("-s", "--skip-ping", action="store_true", help="Skip ping check")
    parser.add_argument("-t", "--threads", type=int, default=10, help="Number of threads to use")
    parser.add_argument("-o", "--output", default="report", help="Output file for the full report (without extension)")
    parser.add_argument("-f", "--format", default="csv", choices=['csv', 'json', 'xml', 'yaml', 'sql', 'parquet'],
                        help="Output format for the report")
    parser.add_argument("-d", "--delay", type=float, default=0, help="Delay between requests in seconds")
    args = parser.parse_args()

    start_time = time.time()
    results = process_file(args)
    end_time = time.time()

    output_file = f"{args.output}.{args.format}"
    generate_report(results, output_file, args.format)
    print_proses(results, start_time, end_time, output_file)
  

# -======================= MAIN
if __name__ == "__main__":
    Write.Print(f'{banner}', Colors.blue_to_cyan, interval=0)
    main()

