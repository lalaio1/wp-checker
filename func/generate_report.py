import csv
import json
import xml.etree.ElementTree as ET
import yaml
import sqlite3
import pyarrow as pa
import pyarrow.parquet as pq

def generate_report(results, output_file, format='csv'):
    if format == 'csv':
        with open(output_file, 'w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(['URL', 'Username', 'Password', 'Status', 'WordPress Version'])
            for result in results:
                writer.writerow([result['url'], result['username'], result['password'], result['status'], result['wp_version']])
    
    elif format == 'json':
        with open(output_file, 'w') as file:
            json.dump(results, file, indent=2)
    
    elif format == 'xml':
        root = ET.Element("results")
        for result in results:
            site = ET.SubElement(root, "site")
            for key, value in result.items():
                ET.SubElement(site, key).text = str(value)
        tree = ET.ElementTree(root)
        tree.write(output_file, encoding='utf-8', xml_declaration=True)
    
    elif format == 'yaml':
        with open(output_file, 'w') as file:
            yaml.dump(results, file)
    
    elif format == 'sql':
        conn = sqlite3.connect(output_file)
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS wp_credentials
                          (url TEXT, username TEXT, password TEXT, status TEXT, wp_version TEXT)''')
        cursor.executemany('INSERT INTO wp_credentials VALUES (?,?,?,?,?)',
                           [(r['url'], r['username'], r['password'], r['status'], r['wp_version']) for r in results])
        conn.commit()
        conn.close()
    
    elif format == 'parquet':
        table = pa.Table.from_pylist(results)
        pq.write_table(table, output_file)
