
from client_package.client import Client
SERVER_IP = "vlsi-zcu216.polito.it"
SERVER_PORT = 9091


if __name__ == "__main__":
    # Replace with actual server IP and port
    client = Client(SERVER_IP, SERVER_PORT)
    client.run()