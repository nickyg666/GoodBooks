# GoodBooks Installer - System Architecture Diagram

## Installation Flow Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                      sudo bash installer.sh                          │
└─────────────────────────┬───────────────────────────────────────────┘
                          │
        ┌─────────────────┴─────────────────┐
        │                                   │
        ▼                                   ▼
┌──────────────────┐             ┌──────────────────────┐
│  System Setup    │             │  Verify User/Perms   │
│  • Check OS      │             │  • Non-root check    │
│  • Install deps  │             │  • Store user info   │
│  • xvfb, calibre │             │                      │
└────────┬─────────┘             └──────────┬───────────┘
         │                                  │
         └──────────────┬───────────────────┘
                        │
                        ▼
        ┌──────────────────────────────────┐
        │ Create Python Virtual Environment│
        │ • python3 -m venv                │
        │ • pip install --upgrade pip      │
        │ • pip install -r requirements    │
        │ • Install critical packages      │
        └────────────┬─────────────────────┘
                     │
                     ▼
        ┌──────────────────────────────┐
        │   Prepare Installation Dir   │
        │ • Create /usr/local/bin/GB   │
        │ • Copy all files             │
        │ • Set permissions            │
        │ • Create service file        │
        └────────────┬─────────────────┘
                     │
                     ▼
        ┌──────────────────────────────┐
        │   ⭐ NEW: Setup Wizard ⭐   │ ◄──────┐
        │                              │       │
        │  setup_wizard.sh             │       │
        │  ├─ Prompt: Library path     │       │
        │  ├─ Prompt: Kindle email     │       │
        │  ├─ Prompt: Server port      │       │
        │  ├─ Prompt: SMTP settings    │       │
        │  ├─ Validate JSON            │       │
        │  └─ Save settings.json       │       │
        │                              │       │
        │  OUTPUT: settings.json ─────────────┘
        └────────────┬─────────────────┘
                     │
                     ▼
        ┌──────────────────────────────┐
        │     Setup Systemd Service    │
        │ • Create service file        │
        │ • Enable service             │
        │ • Start service              │
        └────────────┬─────────────────┘
                     │
                     ▼
        ┌──────────────────────────────┐
        │  ⭐ NEW: Post Install ⭐    │ ◄──────┐
        │                              │       │
        │  post_install.py             │       │
        │  ├─ Load settings.json       │       │
        │  ├─ Find users with Kindle   │       │
        │  ├─ Prompt: Send to Kindle?  │       │
        │  ├─ Detect service IP/port   │       │
        │  ├─ Create EPUB:             │       │
        │  │  goodreads_epub_utils.py  │       │
        │  ├─ Select recipient user    │       │
        │  └─ Send via Kindle email    │       │
        │                              │       │
        │  OUTPUT: EPUB sent ─────────────────┘
        └────────────┬─────────────────┘
                     │
                     ▼
        ┌──────────────────────────────┐
        │   Show Installation Summary  │
        │   • Installation directory   │
        │   • Service commands         │
        │   • Next steps               │
        │   • Troubleshooting          │
        └────────────┬─────────────────┘
                     │
                     ▼
        ┌──────────────────────────────┐
        │   ✅ Installation Complete  │
        │   • GoodBooks running        │
        │   • User configured          │
        │   • Kindle shortcut sent     │
        │   • Ready to use!            │
        └──────────────────────────────┘
```

## Component Interaction Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                          GoodBooks Installer                         │
└──────────────────────────────────────────────────────────────────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        │                          │                          │
        ▼                          ▼                          ▼
  ┌──────────────┐         ┌──────────────┐         ┌──────────────┐
  │ setup_       │         │ post_        │         │ goodreads_   │
  │ wizard.sh    │         │ install.py   │         │ epub_utils.py│
  │              │         │              │         │              │
  │ Interactive  │         │ Orchestrate  │         │ Create EPUB  │
  │ config       │         │ Kindle send  │         │ 3.0 files    │
  └──────┬───────┘         └──────┬───────┘         └──────┬───────┘
         │                        │                        │
         │ Reads/Writes          │ Calls               Imports
         │                        │                        │
         ▼                        ▼                        ▼
  ┌──────────────┐         ┌──────────────┐         ┌──────────────┐
  │ settings.    │         │ send_kindle_ │         │ zipfile      │
  │ json         │         │ email() from │         │ (Python std) │
  │              │         │ app.py       │         │              │
  │ User config  │         │              │         │ ZIP handling │
  │ SMTP creds   │         │ Existing     │         └──────────────┘
  │ Library path │         │ GoodBooks    │
  │ Kindle email │         │ function     │
  └──────────────┘         │              │
                           │ Sends EPUB   │
                           │ to Kindle    │
                           │ via email    │
                           └──────────────┘
```

## Data Flow Diagram

```
Input (User Answers):
┌─────────────────────┐
│ Library path        │
│ Kindle email        │
│ Server port         │
│ SMTP settings       │
└──────────┬──────────┘
           │
           ▼
    setup_wizard.sh
           │
           ▼
    ┌──────────────────────┐
    │  settings.json       │ ◄─── Stored on disk
    │  {                   │
    │    library_root: ... │
    │    smtp: {...}       │
    │    users: [{...}]    │
    │  }                   │
    └──────────┬───────────┘
               │
               ▼
    GoodBooks Service
    (app.py)
               │
        ┌──────┴──────┐
        │             │
        ▼             ▼
    Web UI       Kindle Delivery
    (Flask)      (send_kindle_email)
                      │
    post_install.py ──┤
         │            │
         ▼            │
    Service Detection │
    (systemctl)       │
         │            │
         ▼            │
    EPUB Creation     │
    (goodreads_       │
     epub_utils)      │
         │            │
         ▼            ▼
    ┌──────────────────────────┐
    │  Send EPUB via SMTP      │
    │  to user@kindle.com      │
    └───────────┬──────────────┘
                │
                ▼
    ┌──────────────────────────┐
    │  Book arrives on Kindle  │
    │  with clickable link to  │
    │  GoodBooks web UI        │
    └──────────────────────────┘
```

## Service Detection Logic Flowchart

```
post_install.py starts
         │
         ▼
    Is service running?
         │
      ┌──┴──┐
      │     │
     No    Yes
      │     │
      ▼     ▼
   Fail   systemctl status goodbooks
          --no-pager
          │
          ├─ Parse for port ──┐
          │                   │
          ▼                   │
      Not found?             │
          │                  │
         Yes                 │
          │                  │
          ▼                  │
      journalctl -u          │
      goodbooks -n 50        │
          │                  │
      ┌───┴────────┐         │
      │            │         │
     Found        Not       Found
      │            Found     │
      ▼            │         ▼
    Parse IP  Use localhost  Got IP+Port
      and        or 127.0.0.1 │
     port        │           │
      │          │           │
      └──┬───────┴──────┬────┘
         │              │
         ▼              ▼
    Is it loopback?  Extract values
    (127.0.0.1 etc)
         │
      ┌──┴──┐
      │     │
     Yes    No
      │     │
      ▼     ▼
    Use      Use IP
    hostname  and port
    -I        from service
      │       │
      └───┬───┘
          │
          ▼
    Construct URL:
    http://IP:PORT
          │
          ▼
    Create EPUB
    with this URL
          │
          ▼
    Send to Kindle
```

## File Creation Timeline

```
Timeline of file creation:
═══════════════════════════════════════════════════════════════

installer.sh (existing)
│
├─ Copy goodreads_epub_utils.py
├─ Copy setup_wizard.sh
├─ Copy post_install.py
│
└─ Execute setup_wizard.sh ────┐ (Interactive: ~2-3 min)
   │ Prompts for config         │
   │                            │
   ├─ library_root             │
   ├─ kindle_email             │
   ├─ server_port              │
   └─ smtp_* settings           │
                                │
                    ┌───────────┘
                    │
                    ▼ Generates
           data/settings.json
                    │
                    └─┬─────────────────┐
                      │                 │
                      ▼ Loads           ▼ Sends
                   app.py            GoodBooks
                      │              Service
                      │                │
                      ▼                │
                  Web UI ready         ▼ Detects
                      │
                      │
                      └───────────────────────┐
                                              │
                    Execute                  │
                    post_install.py ◄────────┘
                    │
                    ├─ Load settings.json
                    │
                    ├─ Find Kindle users
                    │
                    ├─ Prompt for delivery
                    │
                    ├─ Create EPUB
                    │
                    └─ Send to Kindle ──┐
                                        │
                                    ┌───┘
                                    │
                                    ▼
                          Book arrives on
                          Kindle device
                                    │
                                    ▼
                          User taps link to
                          access web UI
```

## EPUB Structure Visualization

```
GoodBooks_WebUI_Shortcut.epub (ZIP file)
│
├─ [mimetype]  (uncompressed, must be first)
│   application/epub+zip
│
├─ [META-INF]
│   └─ container.xml  (Package document reference)
│      <?xml version="1.0"?>
│      <container>
│        <rootfiles>
│          <rootfile full-path="OEBPS/content.opf"/>
│        </rootfiles>
│      </container>
│
├─ [OEBPS]
│   ├─ content.opf  (Package metadata + manifest)
│   │   <?xml version="1.0"?>
│   │   <package>
│   │     <metadata>
│   │       <dc:title>GoodBooks Web Interface</dc:title>
│   │     </metadata>
│   │     <manifest>
│   │       <item id="ncx" href="toc.ncx"/>
│   │       <item id="chapter1" href="chapter1.xhtml"/>
│   │       <item id="style" href="style.css"/>
│   │     </manifest>
│   │     <spine toc="ncx">
│   │       <itemref idref="chapter1"/>
│   │     </spine>
│   │   </package>
│   │
│   ├─ toc.ncx  (Table of contents)
│   │   <?xml version="1.0"?>
│   │   <ncx>
│   │     <navMap>
│   │       <navPoint>
│   │         <navLabel>Open GoodBooks</navLabel>
│   │       </navPoint>
│   │     </navMap>
│   │   </ncx>
│   │
│   ├─ chapter1.xhtml  (Main content)
│   │   <!DOCTYPE html>
│   │   <html>
│   │     <body>
│   │       <h1>🎉 Welcome to GoodBooks!</h1>
│   │       <a href="http://192.168.1.100:5000">
│   │         📖 Open GoodBooks Web Interface
│   │       </a>
│   │       <p>Or copy: http://192.168.1.100:5000</p>
│   │       <ul>
│   │         <li>📚 Browse your book library</li>
│   │         <li>🔍 Search Anna's Archive</li>
│   │         <li>📧 Send books to Kindle</li>
│   │         <li>⚙️ Manage feeds</li>
│   │         <li>📱 Access from any device</li>
│   │       </ul>
│   │     </body>
│   │   </html>
│   │
│   └─ style.css  (Styling)
│       body {
│         font-family: Georgia, serif;
│         margin: 2em;
│         text-align: center;
│       }
│       .button-link {
│         padding: 1em 2em;
│         background-color: #4CAF50;
│         color: white;
│         border-radius: 4px;
│       }
│
└─ [Total size: ~10-15 KB]
```

## User Selection Interface (Multi-User)

```
post_install.py

Load settings.json
│
├─ Find users
│  └─ nick: kindle_email="nickgelinas_kindle@kindle.com"
│  └─ sage: kindle_email="sagegelinas_kindle@kindle.com"
│
Has Kindle users?
│
├─ No  ──────────────────────┐
│                            │
├─ Yes                       │
│  │                         │
│  ├─ One user only?         │
│  │  │                      │
│  │  ├─ Yes ────────────┐   │
│  │  │                  │   │
│  │  └─ No              │   │
│  │     │               │   │
│  │     ├─ Show list:   │   │
│  │     │  1) nick      │   │
│  │     │  2) sage      │   │
│  │     │               │   │
│  │     └─ Prompt:      │   │
│  │        "Select (1-2):"   │
│  │                      │   │
│  │                      ▼   │
│  │              Use selected user
│  │                      │   │
│  └────────────┬─────────┘   │
│               │             │
│               ▼             │
│         Send EPUB to        │
│         selected user's      │
│         Kindle email         │
│               │             │
│               │             │
│               ▼             ▼
│            SUCCESS        SKIP
│               │             │
│               └─────┬───────┘
│                     │
│                     ▼
│              Installation
│                Complete
```

## Integration Points with GoodBooks Core

```
GoodBooks Application Architecture
═══════════════════════════════════════════════════════════════

┌─────────────────────────────────┐
│      installer.sh               │
│  (NEW: Setup + Post-install)    │
└────────────┬────────────────────┘
             │
    ┌────────┴────────┐
    │                 │
    ▼                 ▼
setup_wizard.sh    post_install.py
    │                 │
    ├─ Generates      ├─ Detects service
    │  settings.json  │  (systemctl)
    │                 │
    │                 ├─ Creates EPUB
    │                 │  (goodreads_
    │                 │   epub_utils)
    │                 │
    │                 └─ Calls app.py:
    │                    send_kindle_email()
    │                    │
    ▼                    ▼
  ┌─────────────────────────────────┐
  │      app.py (Flask app)         │
  │                                 │
  │  Loads settings.json ◄──────────┤
  │                                 │
  │  Uses:                          │
  │  • settings.library_root        │
  │  • settings.users               │
  │  • settings.smtp                │
  │  • settings.server_port         │
  │                                 │
  │  Provides:                      │
  │  • Web UI (Flask)               │
  │  • Library management           │
  │  • Feed processing              │
  │  • Kindle delivery              │
  │  • send_kindle_email()          │
  │                                 │
  └─────────────────────────────────┘
             │
      ┌──────┴──────┐
      │             │
      ▼             ▼
   Web UI        Email System
  (Browser)     (SMTP)
      │             │
      ├─ Access lib ├─ Send EPUB
      ├─ Search     └─ to Kindle
      ├─ Download
      └─ Configure
```

## Performance Timeline

```
Installation Progress
═══════════════════════════════════════════════════════════════

Time   0 sec  ├─ Start installer
              │
       3 sec  ├─ Check OS/user
              │
      20 sec  ├─ Install system packages
              │
       2 min  ├─ Setup Python venv
              │
       2 min  ├─ Install Python packages
              │
       5 min  ├─ Deploy to /usr/local/bin
              │
      10 min  ├─ ⭐ Setup wizard (user input: library, Kindle, port, SMTP)
              │
      20 sec  ├─ Configure systemd service
              │
      10 sec  ├─ Start GoodBooks service
              │
       3 sec  ├─ Verify service is running
              │
       5 min  ├─ ⭐ Post-install wizard (user: yes/no, select user)
              │
       1 sec  ├─ Detect service IP:port
              │
       1 sec  ├─ Create EPUB file
              │
      10 sec  ├─ Send EPUB via Kindle email
              │
       2 sec  ├─ Show installation summary
              │
      35 min  └─ ✅ Installation Complete!
              
Note: Most time is user input in setup wizard
```

---

**Architecture Diagram Version**: 1.0  
**Created**: December 4, 2024  
**For Reference**: INSTALLER_TECHNICAL.md for detailed implementation
