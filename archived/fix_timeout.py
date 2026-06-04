# Add this to the top of the download function to set socket timeout

import socket

# Set global socket timeout to 20 seconds
socket.setdefaulttimeout(20)

print("Socket timeout set to 20 seconds globally")
