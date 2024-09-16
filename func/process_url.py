from func.imports.init import *

def process_url(args, url, username, password):
    result = {
        'url': url,
        'username': username,
        'password': password,
        'status': 'Unknown',
        'wp_version': 'Unknown'
    }

    online = True if args.skip_ping else ping_site(url)
    
    if online:
        result['status'] = 'Online'
        success = check_wp_credentials(url, username, password)
        result['wp_version'] = check_wp_version(url)
        
        if success:
            result['status'] = 'Valid'
            save_to_file(args.valid, result) 
        else:
            result['status'] = 'Invalid'
            save_to_file(args.invalid, result)  
    else:
        result['status'] = 'Offline'
        save_to_file(args.offline, result) 
    
    return result