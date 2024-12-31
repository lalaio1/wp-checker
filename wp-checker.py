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

def main():
    parser = argparse.ArgumentParser(
        description="Advanced WordPress Credential Checker - A powerful tool to validate WordPress credentials and check site statuses.",
        formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument("file", 
                        help="Path to the file with URLs and credentials. "
                             "This file should contain URLs in the format 'http://example.com:username:password' "
                             "on each line.")

    parser.add_argument("-v", "--valid", 
                        help="File to save valid credentials in the format: URL, Username, Password. "
                             "If not provided, valid credentials will not be saved.")

    parser.add_argument("-i", "--invalid", 
                        help="File to save invalid credentials. "
                             "If not provided, invalid credentials will not be saved.")

    parser.add_argument("-off", "--offline", 
                        help="File to save URLs of sites that are offline or unreachable. "
                             "If not provided, offline sites will not be saved.")
    
    parser.add_argument("-s", "--skip-ping", action="store_true", 
                        help="Skip the ping check to check site availability faster. "
                             "By default, a ping test is performed to ensure the site is online before checking credentials.")
    
    parser.add_argument("-t", "--threads", type=int, default=10, 
                        help="Number of threads to use for processing the file. "
                             "More threads may speed up the process but will use more system resources. Default is 10.")

    parser.add_argument("-o", "--output", default="report", 
                        help="Name of the output file for the full report (without extension). "
                             "The default output file is 'report'.")
    
    parser.add_argument("-f", "--format", default="csv", choices=['csv', 'json', 'xml', 'yaml', 'sql', 'parquet'],
                        help="Output format for the report. "
                             "You can choose from 'csv', 'json', 'xml', 'yaml', 'sql', or 'parquet'. "
                             "The default format is 'csv'.")
    
    parser.add_argument("-d", "--delay", type=float, default=0, 
                        help="Delay between requests in seconds. "
                             "This can be used to throttle requests and avoid hitting the server too quickly. "
                             "The default is 0 seconds (no delay).")

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
