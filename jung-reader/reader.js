/* ─────────────────────────────────────────────
 *  Jung Trilogy Reader
 *  - Fetches private GitHub repo via user's PAT
 *  - Token stored in localStorage (client-side only)
 * ───────────────────────────────────────────── */
(function () {
  'use strict';

  const REPO_OWNER = 'TLAN1012';
  const REPO_NAME = 'jung_trilogy';
  const BRANCH = 'main';
  const TOKEN_KEY = 'jung_reader_token';
  const THEME_KEY = 'jung_reader_theme';

  const BOOKS = [
    {
      id: '01-taiwan-folktales',
      title: '📘 台灣民間故事的心理學',
      subtitle: '從榮格的眼睛，看我們從小聽的那些故事',
      chapters: [
        { file: '00-preface.md', title: '前言 · 我們也有我們的格林' },
        { file: '01-鄭成功開台傳說.md', title: '1. 鄭成功開台傳說' },
        { file: '02-虎姑婆.md', title: '2. 虎姑婆' },
        { file: '03-林投姐.md', title: '3. 林投姐' },
        { file: '04-蛇郎君.md', title: '4. 蛇郎君' },
        { file: '05-廖添丁.md', title: '5. 廖添丁' },
        { file: '06-田都元帥.md', title: '6. 田都元帥' },
        { file: '07-媽祖.md', title: '7. 媽祖' },
        { file: '08-雷公電母.md', title: '8. 雷公電母' },
        { file: '09-周成過台灣.md', title: '9. 周成過台灣' },
        { file: '10-魯班公.md', title: '10. 魯班公' },
      ],
    },
    {
      id: '02-jinyong-heroes',
      title: '📗 金庸群俠的心理學',
      subtitle: '不從主角切入，從配角與反派',
      chapters: [
        { file: '00-preface.md', title: '前言 · 那些不是主角的人' },
        { file: '01-喬峰.md', title: '1. 喬峰' },
        { file: '02-李莫愁.md', title: '2. 李莫愁' },
        { file: '03-歐陽鋒.md', title: '3. 歐陽鋒' },
        { file: '04-岳不群.md', title: '4. 岳不群' },
        { file: '05-胡青牛.md', title: '5. 胡青牛' },
        { file: '06-虛竹.md', title: '6. 虛竹' },
        { file: '07-梅超風.md', title: '7. 梅超風' },
        { file: '08-慕容復.md', title: '8. 慕容復' },
        { file: '09-段譽.md', title: '9. 段譽' },
        { file: '10-任盈盈.md', title: '10. 任盈盈' },
      ],
    },
    {
      id: '03-ghibli-supporting',
      title: '📙 吉卜力的配角心理學',
      subtitle: '第一主角退場，第二主角登台',
      chapters: [
        { file: '00-preface.md', title: '前言 · 第二主角的祕密' },
        { file: '01-卡西法.md', title: '1. 卡西法' },
        { file: '02-ムタ.md', title: '2. ムタ / 流氓貓' },
        { file: '03-無臉男.md', title: '3. 無臉男' },
        { file: '04-湯婆婆錢婆婆.md', title: '4. 湯婆婆與錢婆婆' },
        { file: '05-貓公車.md', title: '5. 貓公車' },
        { file: '06-釜爺.md', title: '6. 釜爺' },
        { file: '07-莫洛.md', title: '7. 莫洛' },
        { file: '08-吉吉.md', title: '8. 黑貓吉吉' },
        { file: '09-克羅托瓦.md', title: '9. 克羅托瓦' },
        { file: '10-山獸神.md', title: '10. 山獸神' },
      ],
    },
  ];

  // Flatten chapter list for prev/next navigation
  const ALL_CHAPTERS = [];
  BOOKS.forEach(b => b.chapters.forEach(c => {
    ALL_CHAPTERS.push({ bookId: b.id, bookTitle: b.title, file: c.file, title: c.title });
  }));

  let currentIndex = -1;  // index into ALL_CHAPTERS; -1 = landing page

  // ── Auth ──
  function getToken() { return localStorage.getItem(TOKEN_KEY) || ''; }
  function setToken(t) { localStorage.setItem(TOKEN_KEY, t); }
  function clearToken() { localStorage.removeItem(TOKEN_KEY); }

  // ── Theme ──
  function getTheme() { return localStorage.getItem(THEME_KEY) || 'light'; }
  function setTheme(t) {
    localStorage.setItem(THEME_KEY, t);
    if (t === 'light') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.setAttribute('data-theme', t);
    const icon = document.getElementById('theme-icon');
    if (icon) icon.textContent = t === 'dark' ? '☀️' : t === 'sepia' ? '🌙' : '📖';
  }

  // ── GitHub API ──
  async function fetchFile(path) {
    const token = getToken();
    if (!token) throw new Error('NO_TOKEN');
    const url = `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/contents/${encodeURIComponent(path)}?ref=${BRANCH}`;
    const resp = await fetch(url, {
      headers: {
        'Authorization': `Bearer ${token}`,
        'Accept': 'application/vnd.github.v3.raw',
      },
    });
    if (resp.status === 401 || resp.status === 403) {
      throw new Error('AUTH_FAILED');
    }
    if (resp.status === 404) {
      throw new Error('NOT_FOUND');
    }
    if (!resp.ok) throw new Error('HTTP_' + resp.status);
    return await resp.text();
  }

  // ── Cache ──
  const cache = new Map();
  async function getChapterContent(bookId, file) {
    const key = `${bookId}/${file}`;
    if (cache.has(key)) return cache.get(key);
    const content = await fetchFile(key);
    cache.set(key, content);
    return content;
  }

  // ── Render ──
  function renderLanding() {
    const content = document.getElementById('content');
    content.classList.add('landing');
    let html = `
      <h1>榮格三書</h1>
      <p class="subtitle">童話、武俠、動畫裡的靈魂地圖</p>
      <div class="book-grid">
    `;
    BOOKS.forEach((b, idx) => {
      html += `
        <div class="book-card" data-jump="${b.chapters[0].file}" data-book="${b.id}">
          <div class="book-title">${b.title}</div>
          <div class="book-desc">${b.subtitle}</div>
        </div>`;
    });
    html += '</div>';
    content.innerHTML = html;
    content.scrollTop = 0;
    content.querySelectorAll('.book-card').forEach(card => {
      card.addEventListener('click', () => {
        const bookId = card.dataset.book;
        const file = card.dataset.jump;
        const idx = ALL_CHAPTERS.findIndex(c => c.bookId === bookId && c.file === file);
        if (idx >= 0) loadChapter(idx);
      });
    });
    document.getElementById('title-bar').textContent = '榮格三書';
    updateNavButtons();
    highlightTocActive();
  }

  async function loadChapter(idx) {
    currentIndex = idx;
    const ch = ALL_CHAPTERS[idx];
    const content = document.getElementById('content');
    content.classList.remove('landing');
    content.innerHTML = '<div class="loader">載入中…</div>';
    content.scrollTop = 0;
    document.getElementById('title-bar').textContent = ch.title;
    try {
      const md = await getChapterContent(ch.bookId, ch.file);
      const html = DOMPurify.sanitize(marked.parse(md));
      content.innerHTML = html;
      content.scrollTop = 0;
      updateNavButtons();
      highlightTocActive();
      closeSidebar();
    } catch (e) {
      if (e.message === 'NO_TOKEN' || e.message === 'AUTH_FAILED') {
        clearToken();
        showAuth('Token 無效或已過期，請重新輸入');
      } else {
        content.innerHTML = `<div class="loader">載入失敗：${e.message}<br><br>請檢查網路連線或 Token 權限。</div>`;
      }
    }
  }

  function updateNavButtons() {
    const prev = document.getElementById('prev-btn');
    const next = document.getElementById('next-btn');
    prev.disabled = currentIndex <= 0;
    next.disabled = currentIndex < 0 || currentIndex >= ALL_CHAPTERS.length - 1;
    if (currentIndex < 0) prev.textContent = '首頁';
    else prev.textContent = '‹ 上一章';
  }

  // ── TOC ──
  function buildToc() {
    const toc = document.getElementById('toc');
    let html = '<div class="toc-chapter" data-landing="1">← 回首頁</div>';
    BOOKS.forEach(b => {
      html += `<div class="toc-book">${b.title}</div>`;
      b.chapters.forEach(c => {
        const globalIdx = ALL_CHAPTERS.findIndex(x => x.bookId === b.id && x.file === c.file);
        html += `<div class="toc-chapter" data-idx="${globalIdx}">${c.title}</div>`;
      });
    });
    toc.innerHTML = html;
    toc.querySelectorAll('.toc-chapter').forEach(el => {
      el.addEventListener('click', () => {
        if (el.dataset.landing) {
          currentIndex = -1;
          renderLanding();
          closeSidebar();
        } else {
          loadChapter(parseInt(el.dataset.idx, 10));
        }
      });
    });
  }
  function highlightTocActive() {
    document.querySelectorAll('#toc .toc-chapter').forEach(el => {
      const idx = el.dataset.idx;
      if (idx !== undefined && parseInt(idx, 10) === currentIndex) {
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

  // ── Auth screen ──
  function showAuth(msg) {
    document.getElementById('auth-screen').classList.remove('hidden');
    document.getElementById('main-screen').classList.add('hidden');
    const err = document.getElementById('auth-error');
    if (msg) { err.textContent = msg; err.classList.remove('hidden'); }
    else { err.classList.add('hidden'); }
    document.getElementById('token-input').value = '';
    document.getElementById('token-input').focus();
  }

  function showMain() {
    document.getElementById('auth-screen').classList.add('hidden');
    document.getElementById('main-screen').classList.remove('hidden');
  }

  async function attemptAuth(token) {
    if (!token) return false;
    setToken(token);
    // Verify by trying to fetch README
    try {
      await fetchFile('README.md');
      return true;
    } catch (e) {
      clearToken();
      return false;
    }
  }

  // ── Theme cycling ──
  const THEMES = ['light', 'sepia', 'dark'];
  function cycleTheme() {
    const cur = getTheme();
    const next = THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length];
    setTheme(next);
  }

  // ── Init ──
  async function init() {
    setTheme(getTheme());
    buildToc();

    // Event bindings
    document.getElementById('token-submit').addEventListener('click', async () => {
      const t = document.getElementById('token-input').value.trim();
      if (!t) return;
      document.getElementById('token-submit').disabled = true;
      document.getElementById('token-submit').textContent = '驗證中…';
      const ok = await attemptAuth(t);
      document.getElementById('token-submit').disabled = false;
      document.getElementById('token-submit').textContent = '進入閱讀';
      if (ok) {
        showMain();
        renderLanding();
      } else {
        showAuth('Token 驗證失敗，請檢查權限設定');
      }
    });
    document.getElementById('token-input').addEventListener('keydown', e => {
      if (e.key === 'Enter') document.getElementById('token-submit').click();
    });

    document.getElementById('menu-btn').addEventListener('click', openSidebar);
    document.getElementById('close-sidebar').addEventListener('click', closeSidebar);
    document.getElementById('overlay').addEventListener('click', closeSidebar);
    document.getElementById('theme-btn').addEventListener('click', cycleTheme);
    document.getElementById('logout-btn').addEventListener('click', () => {
      if (confirm('確定要清除 Token？下次開啟需重新輸入。')) {
        clearToken();
        cache.clear();
        showAuth();
      }
    });
    document.getElementById('prev-btn').addEventListener('click', () => {
      if (currentIndex > 0) loadChapter(currentIndex - 1);
      else if (currentIndex === 0) { currentIndex = -1; renderLanding(); }
    });
    document.getElementById('next-btn').addEventListener('click', () => {
      if (currentIndex < ALL_CHAPTERS.length - 1) loadChapter(currentIndex + 1);
    });

    // Swipe navigation (touchstart / touchend)
    let touchStartX = null;
    document.getElementById('content').addEventListener('touchstart', e => {
      touchStartX = e.touches[0].clientX;
    }, { passive: true });
    document.getElementById('content').addEventListener('touchend', e => {
      if (touchStartX == null) return;
      const dx = e.changedTouches[0].clientX - touchStartX;
      touchStartX = null;
      if (Math.abs(dx) < 80) return;
      if (dx < 0 && currentIndex < ALL_CHAPTERS.length - 1) loadChapter(currentIndex + 1);
      else if (dx > 0) {
        if (currentIndex > 0) loadChapter(currentIndex - 1);
        else if (currentIndex === 0) { currentIndex = -1; renderLanding(); }
      }
    }, { passive: true });

    // Decide initial screen
    const token = getToken();
    if (!token) {
      showAuth();
    } else {
      // Verify silently
      const ok = await attemptAuth(token);
      if (ok) {
        showMain();
        renderLanding();
      } else {
        showAuth('儲存的 Token 已失效，請重新輸入');
      }
    }
  }

  init();
})();
