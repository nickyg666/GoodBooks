#!/bin/bash
# Test if momot.rs is still blocking your IP

echo "Testing momot.rs connectivity..."
curl -s -I "https://momot.rs/d3/y/1765324155/10000/g1/zlib2/test" -w "\nStatus: %{http_code}\n" --connect-timeout 5

echo ""
echo "If you get 403: IP is still blocked, wait another 24 hours"
echo "If you get 200 or redirect: IP might be unblocked, try downloads again"
