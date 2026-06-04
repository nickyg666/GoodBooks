console.log('[Settings.js] Loaded - initializing');
const userSelector = document.getElementById('user-selector');
const userEditor = document.getElementById('user-editor');
let allUsers = [];
let selectedUserIndex = -1;

function buildFeedEditor(feed, userIndex, feedIndex) {
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
                <select name="user-${userIndex}-feed-${feedIndex}-mode" onchange="updateFeedModeVisibility(${userIndex}, ${feedIndex})">
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
                Auto-send to Kindle
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

function updateFeedModeVisibility(userIndex, feedIndex) {
    const feedWrapper = document.getElementById(`feed-${userIndex}-${feedIndex}`);
    if (!feedWrapper) return;
    const saveDirLabel = feedWrapper.querySelector('label.feed-save-dir');
    const modeSelect = feedWrapper.querySelector(`select[name="user-${userIndex}-feed-${feedIndex}-mode"]`);
    if (modeSelect && saveDirLabel) {
        if (modeSelect.value === 'html') {
            saveDirLabel.classList.remove('hidden');
        } else {
            saveDirLabel.classList.add('hidden');
        }
    }
}

function buildUserEditor(user, index) {
    const container = document.createElement('div');
    container.className = 'card';
    container.style.background = 'var(--surface)';
    container.style.border = '1px solid var(--border)';
    container.style.padding = '1rem';
    
    const kindleType = user.kindle_type || 'paperwhite';
    const hasKindleEmail = !!user.kindle_email;
    const hasNotificationEmail = !!user.notification_email;
    
    container.innerHTML = `
        <h3 style="margin-top: 0;">User Settings</h3>
        <div class="form-grid">
            <label>
                Name
                <input type="text" id="editor-name" value="${user.name || ''}" required>
            </label>
            <label>
                Save Directory
                <input type="text" id="editor-save_dir" value="${user.save_dir || 'downloads'}">
            </label>
            <label>
                Kindle Type
                <select id="editor-kindle_type">
                    <option value="paperwhite" ${kindleType === 'paperwhite' ? 'selected' : ''}>
                        Kindle 8th gen or newer
                    </option>
                    <option value="oasis" ${kindleType === 'oasis' ? 'selected' : ''}>
                        Kindle Scribe/Oasis
                    </option>
                </select>
            </label>
        </div>
        
        <div class="form-grid">
            <label>
                <input type="checkbox" 
                       id="editor-send-to-kindle"
                       ${hasKindleEmail ? 'checked' : ''}>
                Send to Kindle
            </label>
            <label id="editor-kindle-email-label" style="display: ${hasKindleEmail ? 'block' : 'none'};">
                Kindle Email
                <input type="text" 
                       id="editor-kindle_email"
                       value="${user.kindle_email || ''}"
                       placeholder="kindle@kindle.com"
                       ${!hasKindleEmail ? 'disabled' : ''}>
            </label>
        </div>
        
        <div class="form-grid">
            <label>
                <input type="checkbox"
                       id="editor-send-notifications"
                       ${hasNotificationEmail ? 'checked' : ''}>
                Notification Email
            </label>
            <label id="editor-notification-email-label" style="display: ${hasNotificationEmail ? 'block' : 'none'};">
                Notification Email
                <input type="text"
                       id="editor-notification_email"
                       value="${user.notification_email || ''}"
                       placeholder="user@example.com"
                       ${!hasNotificationEmail ? 'disabled' : ''}>
            </label>
        </div>
        
        <h4>Feeds</h4>
        <div class="feeds" id="feeds-editor"></div>
        <div class="actions" style="margin-top: 1rem;">
            <button type="button" class="secondary" onclick="addFeedToEditor()">Add Feed</button>
            <button type="button" class="secondary" onclick="sendGoodBooksToKindle('${user.name}')">
                Send GoodBooks to Kindle
            </button>
            <button type="button" class="primary" onclick="saveCurrentUser(); alert('User details saved to memory. Click Save Settings to persist.');" style="margin-left: 1rem;">
                Save User Details
            </button>
        </div>
    `;

    const feedsContainer = container.querySelector('#feeds-editor');
    (user.feeds || []).forEach((feed, feedIndex) => {
        feedsContainer.appendChild(buildFeedEditor(feed, index, feedIndex));
    });

    // Set up event listeners
    const kindleCheckbox = container.querySelector('#editor-send-to-kindle');
    const kindleEmailLabel = container.querySelector('#editor-kindle-email-label');
    const kindleEmailInput = container.querySelector('#editor-kindle_email');
    
    if (kindleCheckbox) {
        kindleCheckbox.addEventListener('change', (e) => {
            if (e.target.checked) {
                kindleEmailLabel.style.display = 'block';
                kindleEmailInput.disabled = false;
                kindleEmailInput.focus();
            } else {
                kindleEmailLabel.style.display = 'none';
                kindleEmailInput.disabled = true;
                kindleEmailInput.value = '';
            }
        });
    }

    const notifCheckbox = container.querySelector('#editor-send-notifications');
    const notifEmailLabel = container.querySelector('#editor-notification-email-label');
    const notifEmailInput = container.querySelector('#editor-notification_email');
    
    if (notifCheckbox) {
        notifCheckbox.addEventListener('change', (e) => {
            if (e.target.checked) {
                notifEmailLabel.style.display = 'block';
                notifEmailInput.disabled = false;
                notifEmailInput.focus();
            } else {
                notifEmailLabel.style.display = 'none';
                notifEmailInput.disabled = true;
                notifEmailInput.value = '';
            }
        });
    }

    userEditor.innerHTML = '';
    userEditor.appendChild(container);
    userEditor.style.display = 'block';
}

function updateUserSelector() {
    const currentValue = userSelector.value;
    userSelector.innerHTML = '<option value="">-- New User --</option>';
    
    allUsers.forEach((user, idx) => {
        const option = document.createElement('option');
        option.value = idx;
        option.textContent = user.name || `User ${idx + 1}`;
        userSelector.appendChild(option);
    });
    
    userSelector.value = currentValue;
}

function selectUser() {
    const value = userSelector.value;
    
    if (value === '') {
        selectedUserIndex = -1;
        buildUserEditor({feeds: []}, -1);
    } else {
        selectedUserIndex = parseInt(value, 10);
        buildUserEditor(allUsers[selectedUserIndex], selectedUserIndex);
    }
}

function addFeedToEditor() {
    const feedsContainer = userEditor.querySelector('.feeds');
    const feedCount = feedsContainer.children.length;
    feedsContainer.appendChild(buildFeedEditor({filetypes: []}, selectedUserIndex, feedCount));
}

function removeFeed(userIndex, feedIndex) {
    const wrapper = document.getElementById(`feed-${userIndex}-${feedIndex}`);
    if (wrapper) {
        wrapper.classList.add('feed-removed');
        const removedFlag = wrapper.querySelector('.feed-removed-flag');
        if (removedFlag) {
            removedFlag.value = '1';
        }
    }
}

function deleteSelectedUser() {
    if (selectedUserIndex === -1) {
        alert('Please select a user first');
        return;
    }
    
    if (!confirm(`Delete user "${allUsers[selectedUserIndex].name}"?`)) {
        return;
    }
    
    allUsers.splice(selectedUserIndex, 1);
    selectedUserIndex = -1;
    updateUserSelector();
    userSelector.value = '';
    buildUserEditor({feeds: []}, -1);
}

function serializeCurrentUser() {
    if (!userEditor.firstChild) return null;
    
    const feedsContainer = userEditor.querySelector('.feeds');
    const feeds = Array.from(feedsContainer.children)
        .filter(f => !f.classList.contains('feed-removed'))
        .map((feedEl) => ({
            url: feedEl.querySelector('input[name$="-url"]')?.value || '',
            mode: feedEl.querySelector('select[name$="-mode"]')?.value || 'rss',
            filetypes: (feedEl.querySelector('input[name$="-filetypes"]')?.value || '').split(',').map(f => f.trim()).filter(Boolean),
            save_dir: feedEl.querySelector('input[name$="-save_dir"]')?.value || '',
            auto_send_to_kindle: !!feedEl.querySelector('input[name$="-auto_send_to_kindle"]')?.checked,
        }));
    
    return {
        name: userEditor.querySelector('#editor-name')?.value || '',
        save_dir: userEditor.querySelector('#editor-save_dir')?.value || 'downloads',
        kindle_type: userEditor.querySelector('#editor-kindle_type')?.value || 'paperwhite',
        kindle_email: userEditor.querySelector('#editor-kindle_email')?.value || '',
        notification_email: userEditor.querySelector('#editor-notification_email')?.value || '',
        feeds,
    };
}

function saveCurrentUser() {
    const userData = serializeCurrentUser();
    if (!userData) return;
    
    if (!userData.name) {
        alert('User name is required');
        return;
    }
    
    if (selectedUserIndex === -1) {
        allUsers.push(userData);
        selectedUserIndex = allUsers.length - 1;
    } else {
        allUsers[selectedUserIndex] = userData;
    }
    
    updateUserSelector();
    userSelector.value = selectedUserIndex;
}

async function sendGoodBooksToKindle(userName) {
    try {
        const response = await fetch('/send-goodbooks-to-kindle', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({user_name: userName})
        });
        
        const data = await response.json();
        if (response.ok) {
            alert('GoodBooks sent to Kindle!');
        } else {
            alert('Error: ' + (data.error || 'Failed to send'));
        }
    } catch (error) {
        alert('Error: ' + error.message);
    }
}

// Initialize on page load
existingUsers.forEach((user, idx) => {
    allUsers.push({
        name: user.name,
        save_dir: user.save_dir,
        kindle_type: user.kindle_type || 'paperwhite',
        kindle_email: user.kindle_email || '',
        notification_email: user.notification_email || '',
        feeds: (user.feeds || []).map(f => ({
            url: f.url || '',
            mode: f.mode || 'rss',
            filetypes: Array.isArray(f.filetypes) ? f.filetypes : (f.filetypes || '').split(',').map(ft => ft.trim()).filter(Boolean),
            save_dir: f.save_dir || '',
            auto_send_to_kindle: !!f.auto_send_to_kindle,
        }))
    });
});

updateUserSelector();
buildUserEditor({feeds: []}, -1);

console.log('[Settings.js] Initialized, allUsers count:', allUsers.length);

// Override form submission
const settingsForm = document.getElementById('settings-form');
console.log('[Settings.js] Form element found:', !!settingsForm);

if (!settingsForm) {
    console.error('[Settings.js] ERROR: settings-form element not found in DOM!');
} else {
    const saveButton = document.getElementById('save-button');
    console.log('[Settings.js] Save button found:', !!saveButton);
    
    if (saveButton) {
        console.log('[Settings.js] Attaching click listener to save button');
        saveButton.addEventListener('click', function(e) {
            console.log('[Settings.js] Save button clicked EVENT FIRED');
            e.preventDefault();
            console.log('[Settings.js] Dispatching submit event');
            settingsForm.dispatchEvent(new Event('submit'));
        });
    } else {
        console.error('[Settings.js] ERROR: save button not found in DOM!');
    }
    
    settingsForm.addEventListener('submit', function(e) {
        console.log('[Settings.js] Form submit event fired');
        e.preventDefault();
    
        // Collect the system/smtp settings from form inputs
        const formData = new FormData();
        const formInputs = this.querySelectorAll('input, select, textarea');
        
        console.log('[Settings.js] Found', formInputs.length, 'form inputs');
        
        formInputs.forEach(input => {
            if (input.name) {
                if (input.type === 'checkbox') {
                    formData.append(input.name, input.checked ? 'on' : '');
                    console.log('[Settings.js] Checkbox:', input.name, '=', input.checked ? 'on' : '');
                } else {
                    formData.append(input.name, input.value);
                    console.log('[Settings.js] Input:', input.name, '=', input.value);
                }
            }
        });
        
        // Add users from allUsers
        formData.set('user-count', allUsers.length);
        console.log('[Settings.js] User count:', allUsers.length);
        allUsers.forEach((user, idx) => {
            formData.append(`user-${idx}-name`, user.name);
            formData.append(`user-${idx}-save_dir`, user.save_dir);
            formData.append(`user-${idx}-kindle_type`, user.kindle_type);
            formData.append(`user-${idx}-send-to-kindle`, user.kindle_email ? 'on' : '');
            formData.append(`user-${idx}-kindle_email`, user.kindle_email);
            formData.append(`user-${idx}-send-notifications`, user.notification_email ? 'on' : '');
            formData.append(`user-${idx}-notification_email`, user.notification_email);
            formData.append(`user-${idx}-feed-count`, user.feeds.length);
            console.log('[Settings.js] User', idx, ':', user.name, 'feeds:', user.feeds.length);
            
            user.feeds.forEach((feed, feedIdx) => {
                formData.append(`user-${idx}-feed-${feedIdx}-url`, feed.url);
                formData.append(`user-${idx}-feed-${feedIdx}-mode`, feed.mode);
                formData.append(`user-${idx}-feed-${feedIdx}-filetypes`, (feed.filetypes || []).join(', '));
                formData.append(`user-${idx}-feed-${feedIdx}-save_dir`, feed.save_dir);
                formData.append(`user-${idx}-feed-${feedIdx}-auto_send_to_kindle`, feed.auto_send_to_kindle ? 'on' : '');
                formData.append(`user-${idx}-feed-${feedIdx}-removed`, '0');
            });
        });
        
        const saveStatus = document.getElementById('save-status');
        const saveButton = document.getElementById('save-button');
        saveButton.disabled = true;
        saveStatus.style.display = 'block';
        saveStatus.textContent = 'Saving...';
        saveStatus.style.color = 'var(--text-secondary)';
        
        console.log('[Settings.js] Submitting form with', allUsers.length, 'users and form action:', this.action);
        
        fetch(this.action || '/settings', {
            method: 'POST',
            body: formData
        })
        .then(response => {
            console.log('[Settings.js] Response status:', response.status);
            if (response.ok) {
                saveStatus.textContent = 'Settings saved! Reloading...';
                saveStatus.style.color = 'var(--success)';
                setTimeout(() => {
                    window.location.href = '/settings';
                }, 500);
            } else {
                saveStatus.textContent = 'Failed to save settings (HTTP ' + response.status + ')';
                saveStatus.style.color = 'var(--danger)';
                saveButton.disabled = false;
            }
        })
        .catch(err => {
            console.error('Error saving settings:', err);
            saveStatus.textContent = 'Error: ' + err.message;
            saveStatus.style.color = 'var(--danger)';
            saveButton.disabled = false;
        });
    });
}
