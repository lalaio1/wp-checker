
def save_to_file(filename, content):
    if filename:
        with open(filename, 'a') as file:
            file.write(f"URL: {content['url']}\n")
            file.write(f"Username: {content['username']}\n")
            file.write(f"Password: {content['password']}\n")
            file.write(f"Status: {content['status']}\n")
            file.write(f"WP Version: {content['wp_version']}\n")
            file.write('-' * 40 + '\n') 