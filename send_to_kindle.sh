#!/bin/bash

EPUB_FILE="/usr/local/bin/GoodBooks/GoodBooks.epub"
TO_ADDRESS="nickgelinas_kindle@kindle.com"
FROM_ADDRESS="goodbooksdelivery@gmail.com"

# Create temporary email file
TEMP_EMAIL=$(mktemp)

# Create the email message with MIME attachment
cat > "$TEMP_EMAIL" << EMAIL_EOF
To: $TO_ADDRESS
From: $FROM_ADDRESS
Subject: GoodBooks - Your Personal Ebook Library
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="boundary123"

--boundary123
Content-Type: text/plain; charset=utf-8
Content-Transfer-Encoding: 7bit

Your updated GoodBooks EPUB has been created and is ready to read on your Kindle.

This version includes:
- Simple, user-friendly cover page with clickable link
- Navigation links at the bottom of every page
- Easy-to-read format optimized for Kindle devices
- Your current server address: http://192.168.0.9:5000

Enjoy your reading!

--boundary123
Content-Type: application/epub+zip; name="GoodBooks.epub"
Content-Disposition: attachment; filename="GoodBooks.epub"
Content-Transfer-Encoding: base64

EMAIL_EOF

# Encode and append the EPUB file
base64 "$EPUB_FILE" >> "$TEMP_EMAIL"

# Finish the MIME boundary
echo "" >> "$TEMP_EMAIL"
echo "--boundary123--" >> "$TEMP_EMAIL"

# Send using msmtp with the default account
msmtp -t < "$TEMP_EMAIL"
RESULT=$?

# Cleanup
rm "$TEMP_EMAIL"

# Report result
if [ $RESULT -eq 0 ]; then
    echo "✓ Email sent successfully to $TO_ADDRESS"
    ls -lh "$EPUB_FILE" | awk '{print "✓ File size: " $5}'
    exit 0
else
    echo "[ERROR] Failed to send email (msmtp exit code: $RESULT)"
    exit 1
fi
