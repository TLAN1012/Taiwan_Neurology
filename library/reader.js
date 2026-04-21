/* ─────────────────────────────────────────────
 *  Library Reader
 *  - 3 views: auth / shelf / reader
 *  - Multi-repo support via BOOKS metadata (books.js)
 *  - Hash routing: #/ | #/book/<id> | #/book/<id>/<chapter>
 * ───────────────────────────────────────────── */
(function () {
  'use strict';

  const BOOKS = window.BOOKS || [];
  const TOKEN_KEY = 'library_token';
  const THEME_KEY = 'library_theme';
  const LAST_READ_KEY = 'library_last_read';

  let currentBook = null;   // book object when in reader view
  let currentChIdx = -1;    // index in currentBook.chapters; -1 = book TOC landing

  // ── Token / Theme ──
  function getToken() { return localStorage.getItem(TOKEN_KEY) || ''; }
  function setToken(t) { localStorage.setItem(TOKEN_KEY, t); }
  function clearToken() { localStorage.removeItem(TOKEN_KEY); }

  function getTheme() { return localStorage.getItem(THEME_KEY) || 'light'; }
  function setTheme(t) {
    localStorage.setItem(THEME_KEY, t);
    if (t === 'light') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.setAttribute('data-theme', t);
    const iconMap = { dark: '☀️', sepia: '🌙', light: '📖' };
    document.querySelectorAll('#theme-icon, .theme-icon').forEach(el => {
      el.textContent = iconMap[t] || '📖';
    });
  }

  // ── Fetch from GitHub ──
  const cache = new Map();
  async function fetchFile(book, path) {
    const token = getToken();
    if (!token) throw new Error('NO_TOKEN');
    const { owner, name, branch } = book.repo;
    const fullPath = book.pathPrefix ? `${book.pathPrefix}/${path}` : path;
    const url = `https://api.github.com/repos/${owner}/${name}/contents/${encodeURIComponent(fullPath).replace(/%2F/g, '/')}?ref=${branch || 'main'}`;
    const resp = await fetch(url, {
      headers: {
        'Authorization': `Bearer ${token}`,
        'Accept': 'application/vnd.github.v3.raw',
      },
    });
    if (resp.status === 401 || resp.status === 403) throw new Error('AUTH_FAILED');
    if (resp.status === 404) throw new Error('NOT_FOUND');
    if (!resp.ok) throw new Error('HTTP_' + resp.status);
    return await resp.text();
  }
  async function getChapterContent(book, file) {
    const key = `${book.id}/${file}`;
    if (cache.has(key)) return cache.get(key);
    const content = await fetchFile(book, file);
    cache.set(key, content);
    return content;
  }

  // ── View switching ──
  function showView(name) {
    ['auth-screen', 'shelf-screen', 'reader-screen'].forEach(id => {
      document.getElementById(id).classList.add('hidden');
    });
    document.getElementById(name + '-screen').classList.remove('hidden');
  }

  // ── Shelf view ──
  function renderShelf() {
    const grid = document.getElementById('book-grid');
    let html = '';
    BOOKS.forEach(b => {
      html += `
        <div class="book-card" data-book="${b.id}">
          <div class="book-cover">${b.cover || '📖'}</div>
          <div class="book-info">
            <div class="book-title">${b.title}</div>
            <div class="book-desc">${b.subtitle || ''}</div>
            <div class="book-meta">${b.chapters.length} 章</div>
          </div>
        </div>`;
    });
    grid.innerHTML = html;
    grid.querySelectorAll('.book-card').forEach(card => {
      card.addEventListener('click', () => {
        location.hash = `#/book/${card.dataset.book}`;
      });
    });
    showView('shelf');
  }

  // ── Reader view ──
  function openBook(bookId, chapterFile) {
    const book = BOOKS.find(b => b.id === bookId);
    if (!book) { location.hash = '#/'; return; }
    currentBook = book;
    buildToc(book);
    document.getElementById('sidebar-title').textContent = book.title;
    showView('reader');

    if (chapterFile) {
      const idx = book.chapters.findIndex(c => c.file === chapterFile);
      if (idx >= 0) { loadChapter(idx); return; }
    }
    renderBookLanding(book);
  }

  function renderBookLanding(book) {
    currentChIdx = -1;
    const content = document.getElementById('content');
    content.classList.add('landing');
    let html = `
      <h1>${book.title}</h1>
      <p class="subtitle">${book.subtitle || ''}</p>
      <div class="book-grid">
    `;
    book.chapters.forEach((ch, idx) => {
      html += `<div class="book-card" data-idx="${idx}">
        <div class="book-title">${ch.title}</div>
      </div>`;
    });
    html += '</div>';
    content.innerHTML = html;
    content.scrollTop = 0;
    content.querySelectorAll('.book-card').forEach(card => {
      card.addEventListener('click', () => {
        const idx = parseInt(card.dataset.idx, 10);
        location.hash = `#/book/${book.id}/${book.chapters[idx].file}`;
      });
    });
    document.getElementById('title-bar').textContent = book.title;
    updateNavButtons();
    highlightTocActive();
  }

  async function loadChapter(idx) {
    if (!currentBook) return;
    currentChIdx = idx;
    const ch = currentBook.chapters[idx];
    const content = document.getElementById('content');
    content.classList.remove('landing');
    content.innerHTML = '<div class="loader">載入中…</div>';
    content.scrollTop = 0;
    document.getElementById('title-bar').textContent = ch.title;

    // Remember last read
    try {
      localStorage.setItem(LAST_READ_KEY, JSON.stringify({
        book: currentBook.id, file: ch.file,
      }));
    } catch (e) { /* ignore */ }

    try {
      const md = await getChapterContent(currentBook, ch.file);
      const html = DOMPurify.sanitize(marked.parse(md));
      content.innerHTML = html;
      content.scrollTop = 0;
      updateNavButtons();
      highlightTocActive();
      closeSidebar();
    } catch (e) {
      if (e.message === 'NO_TOKEN' || e.message === 'AUTH_FAILED') {
        clearToken();
        location.hash = '#/';
        showAuth('Token 無效或已過期,請重新輸入');
      } else if (e.message === 'NOT_FOUND') {
        content.innerHTML = `<div class="loader">找不到這一章:<br>${ch.file}<br><br>請確認 Token 權限涵蓋 <code>${currentBook.repo.name}</code> 這個 repo。</div>`;
      } else {
        content.innerHTML = `<div class="loader">載入失敗:${e.message}<br><br>請檢查網路連線或 Token 權限。</div>`;
      }
    }
  }

  function updateNavButtons() {
    if (!currentBook) return;
    const prev = document.getElementById('prev-btn');
    const next = document.getElementById('next-btn');
    prev.disabled = currentChIdx <= 0;
    next.disabled = currentChIdx < 0 || currentChIdx >= currentBook.chapters.length - 1;
    if (currentChIdx < 0) prev.textContent = '目錄';
    else if (currentChIdx === 0) prev.textContent = '‹ 目錄';
    else prev.textContent = '‹ 上一章';
  }

  // ── TOC sidebar ──
  function buildToc(book) {
    const toc = document.getElementById('toc');
    let html = '<div class="toc-chapter" data-landing="1">← 回這本書的目錄</div>';
    html += `<div class="toc-book">${book.title}</div>`;
    book.chapters.forEach((c, idx) => {
      html += `<div class="toc-chapter" data-idx="${idx}">${c.title}</div>`;
    });
    toc.innerHTML = html;
    toc.querySelectorAll('.toc-chapter').forEach(el => {
      el.addEventListener('click', () => {
        if (el.dataset.landing) {
          location.hash = `#/book/${book.id}`;
          closeSidebar();
        } else {
          const idx = parseInt(el.dataset.idx, 10);
          location.hash = `#/book/${book.id}/${book.chapters[idx].file}`;
        }
      });
    });
  }
  function highlightTocActive() {
    document.querySelectorAll('#toc .toc-chapter').forEach(el => {
      const idx = el.dataset.idx;
      if (idx !== undefined && parseInt(idx, 10) === currentChIdx) {
        el.classList.add('active');
      } else {
        el.classList.remove('active');
      }
    });
  }

  // ── Sidebar ──
  function openSidebar() {
    document.getElementById('sidebar').classList.remove('hidden');
    document.getElementById('overlay').classList.remove('hidden');
  }
  function closeSidebar() {
    document.getElementById('sidebar').classList.add('hidden');
    document.getElementById('overlay').classList.add('hidden');
  }

  // ── Auth ──
  function showAuth(msg) {
    showView('auth');
    const err = document.getElementById('auth-error');
    if (msg) { err.textContent = msg; err.classList.remove('hidden'); }
    else { err.classList.add('hidden'); }
    document.getElementById('token-input').value = '';
    setTimeout(() => document.getElementById('token-input').focus(), 100);
  }

  async function attemptAuth(token) {
    if (!token) return false;
    setToken(token);
    // Try to fetch README from the first book's repo; if any book fails, we still let the user in
    // (they may only have permission for some books). The per-book errors show at read-time.
    try {
      await fetchFile(BOOKS[0], 'README.md');
      return true;
    } catch (e) {
      // Try next book
      for (let i = 1; i < BOOKS.length; i++) {
        try { await fetchFile(BOOKS[i], 'README.md'); return true; } catch (e2) { /* next */ }
      }
      clearToken();
      return false;
    }
  }

  // ── Theme ──
  const THEMES = ['light', 'sepia', 'dark'];
  function cycleTheme() {
    const cur = getTheme();
    const next = THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length];
    setTheme(next);
  }

  // ── Hash routing ──
  function handleRoute() {
    const hash = location.hash || '#/';
    // Ensure authed first
    if (!getToken()) {
      showAuth();
      return;
    }
    // #/  -> shelf
    if (hash === '#/' || hash === '#' || hash === '') {
      renderShelf();
      return;
    }
    // #/book/<id>(/<chapter>)?
    const m = hash.match(/^#\/book\/([^\/]+)(?:\/(.+))?$/);
    if (m) {
      const bookId = decodeURIComponent(m[1]);
      const chFile = m[2] ? decodeURIComponent(m[2]) : null;
      openBook(bookId, chFile);
      return;
    }
    // Fallback
    renderShelf();
  }

  // ── Init ──
  async function init() {
    setTheme(getTheme());

    // Token submit
    document.getElementById('token-submit').addEventListener('click', async () => {
      const t = document.getElementById('token-input').value.trim();
      if (!t) return;
      const btn = document.getElementById('token-submit');
      btn.disabled = true; btn.textContent = '驗證中…';
      const ok = await attemptAuth(t);
      btn.disabled = false; btn.textContent = '進入圖書館';
      if (ok) {
        // Go to last-read book if any, else shelf
        let target = '#/';
        try {
          const last = JSON.parse(localStorage.getItem(LAST_READ_KEY) || 'null');
          if (last && last.book) {
            target = last.file ? `#/book/${last.book}/${last.file}` : `#/book/${last.book}`;
          }
        } catch (e) { /* ignore */ }
        location.hash = target;
        handleRoute();
      } else {
        showAuth('Token 驗證失敗,請檢查權限設定(要涵蓋所有 repo)');
      }
    });
    document.getElementById('token-input').addEventListener('keydown', e => {
      if (e.key === 'Enter') document.getElementById('token-submit').click();
    });

    // Reader controls
    document.getElementById('back-btn').addEventListener('click', () => {
      location.hash = '#/';
    });
    document.getElementById('menu-btn').addEventListener('click', openSidebar);
    document.getElementById('close-sidebar').addEventListener('click', closeSidebar);
    document.getElementById('overlay').addEventListener('click', closeSidebar);
    document.getElementById('theme-btn').addEventListener('click', cycleTheme);
    document.getElementById('shelf-theme-btn').addEventListener('click', cycleTheme);

    const doLogout = () => {
      if (confirm('確定要清除 Token?下次開啟需重新輸入。')) {
        clearToken();
        cache.clear();
        showAuth();
      }
    };
    document.getElementById('logout-btn').addEventListener('click', doLogout);
    document.getElementById('shelf-logout-btn').addEventListener('click', doLogout);

    document.getElementById('prev-btn').addEventListener('click', () => {
      if (!currentBook) return;
      if (currentChIdx > 0) {
        location.hash = `#/book/${currentBook.id}/${currentBook.chapters[currentChIdx - 1].file}`;
      } else if (currentChIdx === 0) {
        location.hash = `#/book/${currentBook.id}`;
      }
    });
    document.getElementById('next-btn').addEventListener('click', () => {
      if (!currentBook) return;
      if (currentChIdx < currentBook.chapters.length - 1) {
        location.hash = `#/book/${currentBook.id}/${currentBook.chapters[currentChIdx + 1].file}`;
      }
    });

    // Swipe
    let touchStartX = null;
    const contentEl = document.getElementById('content');
    contentEl.addEventListener('touchstart', e => {
      touchStartX = e.touches[0].clientX;
    }, { passive: true });
    contentEl.addEventListener('touchend', e => {
      if (touchStartX == null || !currentBook) return;
      const dx = e.changedTouches[0].clientX - touchStartX;
      touchStartX = null;
      if (Math.abs(dx) < 80) return;
      if (dx < 0 && currentChIdx < currentBook.chapters.length - 1) {
        location.hash = `#/book/${currentBook.id}/${currentBook.chapters[currentChIdx + 1].file}`;
      } else if (dx > 0) {
        if (currentChIdx > 0) {
          location.hash = `#/book/${currentBook.id}/${currentBook.chapters[currentChIdx - 1].file}`;
        } else if (currentChIdx === 0) {
          location.hash = `#/book/${currentBook.id}`;
        }
      }
    }, { passive: true });

    // Hash change -> route
    window.addEventListener('hashchange', handleRoute);

    // First route
    const token = getToken();
    if (!token) {
      showAuth();
    } else {
      const ok = await attemptAuth(token);
      if (ok) handleRoute();
      else showAuth('儲存的 Token 已失效,請重新輸入');
    }
  }

  init();
})();
