"""Read-only startup preflight: never replace or terminate an existing service."""
import socket


def occupied_ports(ports):
    occupied = []
    for port in ports:
        with socket.socket() as probe:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(('0.0.0.0', port))
            except OSError:
                occupied.append(port)
    return occupied


def main():
    conflicts = occupied_ports((8000, 5173))
    if not conflicts:
        return 0
    print('[ERROR] Existing service occupies port(s): ' + ', '.join(map(str, conflicts)))
    print('Close the old J280 Showcase Backend / Frontend windows, then run START_SHOWCASE.bat again.')
    print('Keep the SG21 / SA21 Host window open. Refreshing the webpage does not reload Python.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
