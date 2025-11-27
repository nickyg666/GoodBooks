const usersContainer = document.getElementById('users');
const userCountInput = document.getElementById('user-count');

function buildFeed(feed, userIndex, feedIndex) {
    const wrapper = document.createElement('div');
    wrapper.className = 'feed';
    wrapper.innerHTML = `
        <div class="form-grid">
            <label>Feed URL<input type="text" name="user-${userIndex}-feed-${feedIndex}-url" value="${feed.url || ''}" required></label>
            <label>Mode<select name="user-${userIndex}-feed-${feedIndex}-mode">
                <option value="rss" ${feed.mode === 'rss' ? 'selected' : ''}>RSS</option>
                <option value="html" ${feed.mode === 'html' ? 'selected' : ''}>HTML</option>
            </select></label>
            <label>Filetypes (comma separated)<input type="text" name="user-${userIndex}-feed-${feedIndex}-filetypes" value="${(feed.filetypes || []).join(', ')}"></label>
        </div>
    `;
    return wrapper;
}

function buildUser(user, index) {
    const container = document.createElement('div');
    container.className = 'card nested';
    container.innerHTML = `
        <div class="form-grid">
            <label>Name<input type="text" name="user-${index}-name" value="${user.name || ''}" required></label>
            <label>Save Directory<input type="text" name="user-${index}-save_dir" value="${user.save_dir || 'downloads'}"></label>
            <label>Kindle Type<input type="text" name="user-${index}-kindle_type" value="${user.kindle_type || 'paperwhite'}"></label>
            <label>Kindle Email<input type="email" name="user-${index}-kindle_email" value="${user.kindle_email || ''}"></label>
            <label>Notification Email<input type="email" name="user-${index}-notification_email" value="${user.notification_email || ''}"></label>
        </div>
        <div class="feeds" id="feeds-${index}"></div>
        <input type="hidden" name="user-${index}-feed-count" id="user-${index}-feed-count" value="${(user.feeds || []).length}">
        <div class="actions">
            <button type="button" class="secondary" onclick="addFeed(${index})">Add Feed</button>
            <button type="button" class="danger" onclick="removeUser(${index})">Remove User</button>
        </div>
    `;
    usersContainer.appendChild(container);
    const feedsContainer = container.querySelector(`#feeds-${index}`);
    (user.feeds || []).forEach((feed, feedIndex) => {
        feedsContainer.appendChild(buildFeed(feed, index, feedIndex));
    });
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
        }));
        return {
            name: getValue('input[name$="-name"]'),
            save_dir: getValue('input[name$="-save_dir"]'),
            kindle_type: getValue('input[name$="-kindle_type"]'),
            kindle_email: getValue('input[name$="-kindle_email"]'),
            notification_email: getValue('input[name$="-notification_email"]'),
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

function updateCounts() {
    userCountInput.value = usersContainer.children.length;
    Array.from(usersContainer.children).forEach((userCard, index) => {
        const feedCount = userCard.querySelectorAll('.feed').length;
        const feedCountInput = userCard.querySelector(`#user-${index}-feed-count`);
        if (feedCountInput) {
            feedCountInput.value = feedCount;
        }
    });
}

existingUsers.forEach((user, idx) => buildUser(user, idx));
updateCounts();
