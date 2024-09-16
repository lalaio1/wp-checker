from pystyle import Colors, Colorate, Center

def print_proses(results, start_time, end_time, output_file):
    total_sites = len(results)
    valid_creds = sum(1 for r in results if r['status'] == 'Valid')
    invalid_creds = sum(1 for r in results if r['status'] == 'Invalid')
    offline_sites = sum(1 for r in results if r['status'] == 'Offline')

    box_width = 56  
    divider = "─" * (box_width - 3)
    
    def format_line(text, value):
        line = f"│  {text}: {value}"
        return line + ' ' * (box_width - len(line) - 2) + '│'

    pross = f"""
    ┌{divider}┐
    {format_line('Processing completed in', f'{end_time - start_time:.2f} seconds')}
    {format_line('Total sites processed', total_sites)}
    {format_line('Valid credentials', valid_creds)}
    {format_line('Invalid credentials', invalid_creds)}
    {format_line('Offline sites', offline_sites)}
    └{divider}┘
    """

    pross = Colorate.Horizontal(Colors.blue_to_cyan, pross)
    print(Center.XCenter(pross))
    print(Center.XCenter(f"📁 Full report saved to: {output_file}\n"))
