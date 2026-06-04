const usersContainer = document.getElementById('users');
const userCountInput = document.getElementById('user-count');

function buildFeed(feed, userIndex, feedIndex) {
    const wrapper = document.createElement('div');
    wrapper.className = 'feed';
    wrapper.id = `feed-${userIndex}-${feedIndex}`;
    const saveDir = feed.save_dir || '';
    const autoSend = !!feed.auto_send_to_kindle;
    wrapper.innerHTML = `
        <hr class="feed-divider">
        <div class="form-grid">
            <label>
                Feed URL
                <input type="text"
                       name="user-${userIndex}-feed-${feedIndex}-url"
                       value="${feed.url || ''}"
                       required>
            </label>
            <label>
                Mode
                <select name="user-${userIndex}-feed-${feedIndex}-mode">
                    <option value="rss" ${feed.mode === 'rss' ? 'selected' : ''}>RSS</option>
                    <option value="html" ${feed.mode === 'html' ? 'selected' : ''}>HTML</option>
                </select>
            </label>
            <label>
                Filetypes (comma separated)
                <input type="text"
                       name="user-${userIndex}-feed-${feedIndex}-filetypes"
                       value="${(feed.filetypes || []).join(', ')}">
            </label>
        </div>


        <div class="form-grid">
            <label class="feed-save-dir ${feed.mode === 'html' ? '' : 'hidden'}">
                Save directory (HTML feeds only)
                <input type="text"
                       name="user-${userIndex}-feed-${feedIndex}-save_dir"
                       value="${saveDir}">
            </label>
            <label>
                <input type="checkbox"
                       name="user-${userIndex}-feed-${feedIndex}-auto_send_to_kindle"
                       ${autoSend ? 'checked' : ''}>
                Auto-send to Kindle for this feed
            </label>
        </div>
        
        <input type="hidden"
               name="user-${userIndex}-feed-${feedIndex}-removed"
               value="0"
               class="feed-removed-flag">
        <div class="actions" style="margin-top: 0.5rem;">
            <button type="button"
                    class="danger"
                    onclick="removeFeed(${userIndex}, ${feedIndex})">
                Remove Feed
            </button>
        </div>
    `;
    return wrapper;
}

function buildUser(user, index) {
    const container = document.createElement('div');
    container.className = 'card nested';
    const kindleType = user.kindle_type || 'paperwhite';
    const autoSend = !!user.auto_send_to_kindle;
    const hasKindleEmail = !!user.kindle_email;
    const hasNotificationEmail = !!user.notification_email;
    
    container.innerHTML = `
        <div class="form-grid">
            <label>
                Name
                <input type="text" name="user-${index}-name" value="${user.name || ''}" required>
            </label>
            <label>
                Save Directory
                <input type="text" name="user-${index}-save_dir" value="${user.save_dir || 'downloads'}">
            </label>
            <label>
                Kindle Type
                <select name="user-${index}-kindle_type">
                    <option value="paperwhite" ${kindleType === 'paperwhite' ? 'selected' : ''}>
                        Kindle 8th gen or newer
                    </option>
                    <option value="oasis" ${kindleType === 'oasis' ? 'selected' : ''}>
                        Kindle Scribe/Oasis
                    </option>
                </select>
            </label>
            <label class="kindle-email-toggle">
                <input type="checkbox" 
                       class="kindle-email-checkbox"
                       name="user-${index}-send-to-kindle"
                       ${hasKindleEmail ? 'checked' : ''}>
                <span>Send to Kindle</span>
            </label>
            <label class="kindle-email-field" style="display: ${hasKindleEmail ? 'block' : 'none'};">
                Kindle Email(s)
                <input type="text" 
                       class="kindle-email-input"
                       name="user-${index}-kindle_email" 
                       value="${user.kindle_email || ''}"
                       placeholder="kindle@kindle.com"
                       ${!hasKindleEmail ? 'disabled' : ''}>
            </label>
            <label class="notification-email-toggle">
                <input type="checkbox"
                       class="notification-email-checkbox"
                       name="user-${index}-send-notifications"
                       ${hasNotificationEmail ? 'checked' : ''}>
                <span>Notification Email</span>
            </label>
            <label class="notification-email-field" style="display: ${hasNotificationEmail ? 'block' : 'none'};">
                Notification Email(s)
                <input type="text"
                       class="notification-email-input"
                       name="user-${index}-notification_email"
                       value="${user.notification_email || ''}"
                       placeholder="user@example.com"
                       ${!hasNotificationEmail ? 'disabled' : ''}>
            </label>
            <label>
                <input type="checkbox"
                        name="user-${index}-auto_send_to_kindle"
                        ${autoSend ? 'checked' : ''}>
                Auto-send to Kindle by default
            </label>
        </div>
        <div class="feeds" id="feeds-${index}"></div>
        <input type="hidden"
               name="user-${index}-feed-count"
               id="user-${index}-feed-count"
               value="${(user.feeds || []).length}">
        <div class="actions">
            <button type="button" class="secondary" onclick="addFeed(${index})">Add Feed</button>
            <button type="button" class="secondary" onclick="sendGoodBooksToKindle('${user.name}', ${index})" id="goodbooks-btn-${index}">
                Send GoodBooks to Kindle
            </button>
            <button type="button" class="danger" onclick="removeUser(${index})">Remove User</button>
        </div>
    `;

    const feedsContainer = container.querySelector(`#feeds-${index}`);
    (user.feeds || []).forEach((feed, feedIndex) => {
        feedsContainer.appendChild(buildFeed(feed, index, feedIndex));
    });

    // Add event listeners for show/hide toggle
    const kindleCheckbox = container.querySelector('.kindle-email-checkbox');
    const kindleField = container.querySelector('.kindle-email-field');
    const kindleInput = container.querySelector('.kindle-email-input');
    if (kindleCheckbox) {
        kindleCheckbox.addEventListener('change', (e) => {
            if (e.target.checked) {
                kindleField.style.display = 'block';
                kindleInput.disabled = false;
                kindleInput.focus();
            } else {
                kindleField.style.display = 'none';
                kindleInput.disabled = true;
                kindleInput.value = '';
            }
        });
    }

    const notifCheckbox = container.querySelector('.notification-email-checkbox');
    const notifField = container.querySelector('.notification-email-field');
    const notifInput = container.querySelector('.notification-email-input');
    if (notifCheckbox) {
        notifCheckbox.addEventListener('change', (e) => {
            if (e.target.checked) {
                notifField.style.display = 'block';
                notifInput.disabled = false;
                notifInput.focus();
            } else {
                notifField.style.display = 'none';
                notifInput.disabled = true;
                notifInput.value = '';
            }
        });
    }

    usersContainer.appendChild(container);
}

function addUser() {
    const index = usersContainer.children.length;
    buildUser({feeds: []}, index);
    updateCounts();
}

function serializeUsers() {
    return Array.from(usersContainer.children).map((card) => {
        const getValue = (selector) => (card.querySelector(selector)?.value || '');
        const feedsContainer = card.querySelector('.feeds');
        const feeds = Array.from(feedsContainer.children).map((feedEl) => ({
            url: feedEl.querySelector('input[name$="-url"]')?.value || '',
            mode: feedEl.querySelector('select[name$="-mode"]')?.value || 'rss',
            filetypes: (feedEl.querySelector('input[name$="-filetypes"]')?.value || ''),
            save_dir: feedEl.querySelector('input[name$="-save_dir"]')?.value || '',
            auto_send_to_kindle: !!feedEl.querySelector('input[name$="-auto_send_to_kindle"]')?.checked,
        }));
        return {
            name: getValue('input[name$="-name"]'),
            save_dir: getValue('input[name$="-save_dir"]'),
            kindle_type: getValue('select[name$="-kindle_type"]') || 'paperwhite',
            kindle_email: getValue('input[name$="-kindle_email"]'),
            notification_email: getValue('input[name$="-notification_email"]'),
            auto_send_to_kindle: !!card.querySelector('input[name$="-auto_send_to_kindle"]')?.checked,
            feeds,
        };
    });
}

function rebuildUsers(users) {
    usersContainer.innerHTML = '';
    users.forEach((user, idx) => {
        buildUser({
            ...user,
            feeds: (user.feeds || []).map((feed) => ({
                url: feed.url || '',
                mode: feed.mode || 'rss',
                filetypes: Array.isArray(feed.filetypes) ? feed.filetypes : (feed.filetypes || '').split(',').map((f) => f.trim()).filter(Boolean),
            })),
        }, idx);
    });
    updateCounts();
}

function removeUser(index) {
    const users = serializeUsers();
    users.splice(index, 1);
    rebuildUsers(users);
}

function addFeed(userIndex) {
    const feedsContainer = document.querySelector(`#feeds-${userIndex}`);
    const feedCountInput = document.getElementById(`user-${userIndex}-feed-count`);
    const feedIndex = feedsContainer.children.length;
    feedsContainer.appendChild(buildFeed({filetypes: []}, userIndex, feedIndex));
    feedCountInput.value = feedIndex + 1;
}
function removeFeed(userIndex, feedIndex) {
    const wrapper = document.getElementById(`feed-${userIndex}-${feedIndex}`);
    if (!wrapper) return;

    wrapper.classList.add('feed-removed');
    const removedFlag = wrapper.querySelector('.feed-removed-flag');
    if (removedFlag) {
        removedFlag.value = '1';
    }
    updateCounts();
}

function updateCounts() {
    userCountInput.value = usersContainer.children.length;
    Array.from(usersContainer.children).forEach((userCard, index) => {
        const feeds = Array.from(userCard.querySelectorAll('.feed'));
        const activeFeeds = feeds.filter(
            (f) => !f.classList.contains('feed-removed')
        );
        const feedCount = activeFeeds.length;
        const feedCountInput = userCard.querySelector(`#user-${index}-feed-count`);
        if (feedCountInput) {
            feedCountInput.value = feedCount;
        }
    });
}

async function sendGoodBooksToKindle(userName, userIndex) {
    const btn = document.getElementById(`goodbooks-btn-${userIndex}`);
    const originalText = btn.textContent;
    
    try {
        btn.disabled = true;
        btn.textContent = 'Sending...';
        
        const response = await fetch('/send-goodbooks-to-kindle', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ user_name: userName })
        });
        
        const data = await response.json();
        
        if (response.ok) {
            btn.textContent = '✓ Sent!';
            setTimeout(() => {
                btn.textContent = originalText;
                btn.disabled = false;
            }, 2000);
        } else {
            alert('Error: ' + (data.error || 'Failed to send GoodBooks'));
            btn.textContent = originalText;
            btn.disabled = false;
        }
    } catch (error) {
        console.error('Error sending GoodBooks:', error);
        alert('Error: ' + error.message);
        btn.textContent = originalText;
        btn.disabled = false;
    }
}
existingUsers.forEach((user, idx) => buildUser(user, idx));
updateCounts();
