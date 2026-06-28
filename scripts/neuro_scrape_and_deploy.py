#!/usr/bin/env python3
"""
neuro_scrape_and_deploy.py
每週日由 GitHub Actions 自動執行（見 .github/workflows/neuro-update.yml）：
1. 爬取 neuro.org.tw 教育活動（智慧停止：連續2頁無未來活動才停，最多15頁）
2. 過濾今日起3個月內的活動
3. 產生 index.html（含地區/類別/積分 filter）
4. 更新 index.html → 由 workflow commit + push 到 TLAN1012/Taiwan_Neurology

執行環境：
- GitHub Actions（GITHUB_ACTIONS=true）：只把 index.html 寫回 repo 根目錄，
  交由 workflow 用內建 GITHUB_TOKEN 負責 commit/push。
- 本機手動執行：走 git_deploy（clone 到 /tmp 後自行 push）。
"""

import subprocess, re, json, sys, os
from datetime import date, datetime, timedelta
from pathlib import Path

# ── 設定 ──────────────────────────────────────────
TODAY      = date.today()
CUTOFF     = TODAY + timedelta(days=92)   # ~3個月
MAX_PAGES  = 15
REPO_URL   = "https://github.com/TLAN1012/Taiwan_Neurology.git"
REPO_DIR   = Path("/tmp/Taiwan_Neurology_deploy")
BASE_URL   = "https://www.neuro.org.tw/active"
LIST_URL   = f"{BASE_URL}/other_list.asp"

# ── 爬蟲 ──────────────────────────────────────────
def fetch_page(page_num):
    url = LIST_URL if page_num == 1 else f"{LIST_URL}?page={page_num}"
    r = subprocess.run(
        ["curl", "-s", "--max-time", "30", "-L",
         "-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
         url],
        capture_output=True, text=True
    )
    return r.stdout

def parse_events(html):
    """從一頁 HTML 解析活動列表，回傳 list of dict
    
    實際表格結構（5欄）：
      <td>SID</td>
      <td>日期（獨立行）</td>
      <td><a href="other_toApply_OK.asp?SID=XXX">主辦單位</a><br>活動名稱</td>
      <td>申請</td>
      <td><ul>積分狀態</ul></td>
    """
    events = []
    # 直接找所有含 other_toApply_OK.asp 的行，往前後抓日期和積分
    # 格式：每個 SID 對應的 <a href="other_toApply_OK.asp?SID=NNN"> 只出現一次
    # 往前掃最近的日期行，往後掃積分
    
    all_links = list(re.finditer(
        r'<a href="(other_toApply_OK\.asp\?SID=(\d+))"[^>]*>([^<]*)</a><br>?([^<\n\r]*)',
        html
    ))
    
    for m in all_links:
        href      = m.group(1)
        sid       = m.group(2)
        organizer = m.group(3).strip()
        title     = m.group(4).strip()
        if not title:
            title = organizer
        
        url = f"{BASE_URL}/{href}"
        
        # 往前找最近的日期（在連結前 300 字元內）
        before = html[max(0, m.start()-300):m.start()]
        date_match = re.findall(r'(\d{4}/\d{1,2}/\d{1,2})', before)
        if not date_match:
            continue
        date_str = date_match[-1]  # 最近的日期
        
        # 往後找積分（在連結後 400 字元內）
        after = html[m.end():m.end()+400]
        credits = 0
        ul_match = re.search(r'<ul>(.*?)</ul>', after, re.DOTALL)
        if ul_match:
            credits_text = re.sub(r'<[^>]+>', '', ul_match.group(1)).strip()
            num = re.search(r'\d+', credits_text)
            if num:
                credits = int(num.group())
        
        # 解析日期
        try:
            parts = date_str.split('/')
            d = date(int(parts[0]), int(parts[1]), int(parts[2]))
        except Exception:
            continue
        
        events.append({
            "date":     date_str,
            "date_obj": d,
            "title":    title,
            "url":      url,
            "location": organizer,
            "credits":  credits,
        })
    return events

def scrape_all():
    """智慧爬取：連續2頁無未來活動就停，最多8頁；自動去重複 SID"""
    all_events = []
    seen_sids  = set()
    consecutive_no_future = 0
    consecutive_no_new    = 0  # 連續頁無新 SID（分頁失效偵測）

    for page in range(1, MAX_PAGES + 1):
        print(f"  爬取第 {page} 頁...", flush=True)
        html = fetch_page(page)
        events = parse_events(html)

        if not events:
            print(f"  第 {page} 頁無資料，停止")
            break

        # 去重複
        new_events = [e for e in events if e["url"] not in seen_sids]
        if not new_events:
            print(f"  第 {page} 頁無新資料（分頁可能無效），停止")
            break
        for e in new_events:
            seen_sids.add(e["url"])

        future_on_page = [e for e in new_events if TODAY <= e["date_obj"] <= CUTOFF]
        all_events.extend(new_events)

        print(f"    → {len(new_events)} 筆（新），其中 {len(future_on_page)} 筆在未來3個月")

        if len(future_on_page) == 0:
            consecutive_no_future += 1
            if consecutive_no_future >= 2:
                print(f"  連續2頁無未來活動，停止（共掃 {page} 頁）")
                break
        else:
            consecutive_no_future = 0

    return all_events

# ── 地區分類 ──────────────────────────────────────
REGION_KEYWORDS = {
    "north":   ["台北","臺北","新北","板橋","新莊","桃園","新竹","基隆","宜蘭","林口","淡水",
                "三重","中和","永和","台大","臺大","北榮","馬偕","北醫","長庚","林口長庚",
                "台北慈濟","北部場","北部"],
    "central": ["台中","臺中","彰化","南投","雲林","嘉義市","苗栗","集思台中",
                "中榮","中國醫","中醫大","中部場","中部","中區","雲嘉"],
    "south":   ["台南","臺南","成大","奇美","嘉義","南部場","南區","南部"],
    "kaoping": ["高雄","屏東","高醫","高長","高榮","高市","高屏","高雄長庚"],
    "east":    ["花蓮","台東","臺東","慈濟醫院協力","東部"],
    "online":  ["線上","視訊","webex","Webex","WEBEX","teams","Teams","zoom","Zoom","直播"],
}

def classify_region(location, title=""):
    """從主辦單位（location）和標題推斷地區"""
    combined = location + " " + title
    for region, keywords in REGION_KEYWORDS.items():
        for kw in keywords:
            if kw in combined:
                return region
    return "other"

# ── 類別分類 ──────────────────────────────────────
CAT_KEYWORDS = {
    "movement": ["動作障礙","巴金森","帕金森","Movement Disorder","Parkinson"],
    "dementia": ["失智","AD treatment","Alzheimer","Dementia","Lewy","阿茲海默"],
    "stroke":   ["腦中風","中風","Stroke","抗栓","Vascular","血管"],
    "epilepsy": ["癲癇","Epilep","伊比力斯","AED","發作"],
    "sleep":    ["睡眠","Sleep","失眠","安眠"],
    "neuro":    ["神經","Neuro","neuro","肌無力","NF1","神經影像","臨床神經","急症","住院醫師"],
}

def classify_cat(title):
    for cat, keywords in CAT_KEYWORDS.items():
        for kw in keywords:
            if kw in title:
                return cat
    return "other"

# ── 生成 HTML ─────────────────────────────────────
def build_html(events):
    today_str   = TODAY.strftime("%Y/%m/%d")
    cutoff_str  = CUTOFF.strftime("%Y/%m/%d")
    update_str  = TODAY.strftime("%Y/%m/%d")

    # 轉成 JS 物件字串
    js_items = []
    for e in events:
        d_iso = e["date_obj"].strftime("%Y-%m-%d")
        cat    = classify_cat(e["title"])
        region = classify_region(e["location"], e["title"])
        online = region == "online" or "線上" in e["title"] or "視訊" in e["title"]
        js_items.append(
            f'  {{date:"{d_iso}",title:{json.dumps(e["title"], ensure_ascii=False)},'
            f'location:{json.dumps(e["location"], ensure_ascii=False)},'
            f'credits:{e["credits"]},url:{json.dumps(e["url"], ensure_ascii=False)},'
            f'cat:"{cat}",online:{"true" if online else "false"},region:"{region}"}}'
        )
    events_js = "[\n" + ",\n".join(js_items) + "\n]"

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>神經學相關教育活動（未來三個月）</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Segoe UI', 'PingFang TC', 'Noto Sans TC', sans-serif;
      background: #f0f2f5; color: #222; min-height: 100vh;
    }}
    header {{
      background: linear-gradient(135deg, #1a237e 0%, #0d47a1 50%, #1565c0 100%);
      color: #fff; padding: 36px 24px 28px; text-align: center;
    }}
    header h1 {{ font-size: 1.9em; font-weight: 700; margin-bottom: 8px; letter-spacing: .02em; }}
    header p  {{ font-size: .95em; opacity: .85; }}
    .header-meta {{
      display: inline-flex; gap: 20px; margin-top: 14px;
      background: rgba(255,255,255,.12); border-radius: 30px;
      padding: 8px 20px; font-size: .85em;
    }}
    .container {{ max-width: 1280px; margin: 0 auto; padding: 24px 20px 60px; }}
    .controls {{
      background: #fff; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,.08);
      padding: 18px 20px; margin-bottom: 18px;
      display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
    }}
    .search-box {{
      flex: 1 1 260px; padding: 10px 18px; border: 2px solid #e0e0e0;
      border-radius: 30px; font-size: .95em; outline: none; transition: border-color .2s;
    }}
    .search-box:focus {{ border-color: #1a237e; }}
    .sort-select {{
      padding: 10px 16px; border: 2px solid #e0e0e0; border-radius: 30px;
      font-size: .9em; cursor: pointer; outline: none; background: #fff; transition: border-color .2s;
    }}
    .sort-select:focus {{ border-color: #1a237e; }}
    .pills-bar {{
      background: #fff; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,.08);
      padding: 14px 20px; margin-bottom: 18px;
      display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
    }}
    .pill-label {{ font-size: .85em; color: #555; font-weight: 600; margin-right: 4px; white-space: nowrap; }}
    .pill-divider {{ width: 1px; height: 22px; background: #e0e0e0; margin: 0 4px; }}
    .pill {{
      padding: 6px 14px; border: 2px solid #c5cae9; background: #fff;
      color: #3949ab; border-radius: 20px; font-size: .83em; cursor: pointer;
      transition: all .2s; white-space: nowrap;
    }}
    .pill:hover  {{ background: #e8eaf6; }}
    .pill.active {{ background: #1a237e; color: #fff; border-color: #1a237e; }}
    .pill.region {{ border-color: #a5d6a7; color: #2e7d32; }}
    .pill.region:hover  {{ background: #e8f5e9; }}
    .pill.region.active {{ background: #2e7d32; color: #fff; border-color: #2e7d32; }}
    .stats {{ font-size: .88em; color: #666; margin-bottom: 16px; padding: 0 4px; }}
    .grid {{
      display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 18px;
    }}
    .card {{
      background: #fff; border-radius: 12px; padding: 20px;
      box-shadow: 0 2px 10px rgba(0,0,0,.08); border-left: 5px solid #3949ab;
      transition: transform .25s, box-shadow .25s;
      display: flex; flex-direction: column; gap: 10px;
    }}
    .card:hover {{ transform: translateY(-4px); box-shadow: 0 8px 24px rgba(0,0,0,.13); }}
    .card.cat-movement  {{ border-left-color: #7b1fa2; }}
    .card.cat-dementia  {{ border-left-color: #f57c00; }}
    .card.cat-stroke    {{ border-left-color: #c62828; }}
    .card.cat-epilepsy  {{ border-left-color: #2e7d32; }}
    .card.cat-sleep     {{ border-left-color: #00838f; }}
    .card.cat-neuro     {{ border-left-color: #1565c0; }}
    .card.cat-other     {{ border-left-color: #78909c; }}
    .card-date {{ font-size: .82em; color: #666; display: flex; align-items: center; gap: 6px; }}
    .card-date .weekday {{
      background: #e8eaf6; color: #3949ab; border-radius: 6px;
      padding: 1px 7px; font-size: .9em; font-weight: 600;
    }}
    .card-date .days-left {{
      margin-left: auto; background: #e3f2fd; color: #1565c0;
      border-radius: 10px; padding: 1px 8px; font-size: .85em;
    }}
    .card-date .days-left.soon  {{ background: #fff3e0; color: #e65100; }}
    .card-date .days-left.today {{ background: #fce4ec; color: #c62828; }}
    .card-title {{ font-size: 1.02em; font-weight: 700; color: #1a237e; line-height: 1.45; }}
    .card-tags {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .tag {{ padding: 3px 10px; border-radius: 12px; font-size: .76em; font-weight: 600; white-space: nowrap; }}
    .tag-cat     {{ background: #e8eaf6; color: #3949ab; }}
    .tag-credits {{ background: #e8f5e9; color: #2e7d32; }}
    .tag-online  {{ background: #eceff1; color: #546e7a; }}
    .tag-region  {{ background: #f3e5f5; color: #6a1b9a; }}
    .card-location {{ font-size: .88em; color: #555; display: flex; align-items: flex-start; gap: 5px; }}
    .card-location::before {{ content: "📍"; flex-shrink: 0; }}
    .card-link {{
      display: inline-block; margin-top: 4px; padding: 7px 18px;
      background: #1a237e; color: #fff; text-decoration: none;
      border-radius: 20px; font-size: .83em; font-weight: 600;
      align-self: flex-start; transition: background .2s;
    }}
    .card-link:hover {{ background: #283593; }}
    .no-results {{ grid-column: 1/-1; text-align: center; padding: 60px 20px; color: #999; font-size: 1.1em; }}
    .month-divider {{
      grid-column: 1/-1; display: flex; align-items: center; gap: 14px; margin: 10px 0 2px;
    }}
    .month-divider span {{ font-size: 1em; font-weight: 700; color: #1a237e; white-space: nowrap; }}
    .month-divider::after {{ content: ''; flex: 1; height: 1px; background: #c5cae9; }}
    footer {{ text-align: center; margin-top: 40px; font-size: .8em; color: #999; }}
    footer a {{ color: #3949ab; text-decoration: none; }}
    @media (max-width: 600px) {{
      header h1 {{ font-size: 1.4em; }}
      .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
<header>
  <h1>🧠 神經學相關教育活動</h1>
  <p>台灣神經學學會繼續教育積分活動 — 未來三個月彙整</p>
  <div class="header-meta">
    <span>📅 {today_str} 起</span>
    <span>⏱️ 截至 {cutoff_str}</span>
    <span>🔄 資料來源：neuro.org.tw</span>
  </div>
</header>

<div class="container">
  <div class="controls">
    <input class="search-box" id="search" type="text" placeholder="搜尋活動名稱或地點…">
    <select class="sort-select" id="sortSel">
      <option value="date-asc">📅 日期由近到遠</option>
      <option value="date-desc">📅 日期由遠到近</option>
      <option value="credits-desc">⭐ 積分由高到低</option>
    </select>
  </div>

  <div class="pills-bar">
    <span class="pill-label">類別：</span>
    <button class="pill active" data-cat="all">全部</button>
    <button class="pill" data-cat="movement">動作障礙</button>
    <button class="pill" data-cat="dementia">失智症</button>
    <button class="pill" data-cat="stroke">腦中風</button>
    <button class="pill" data-cat="epilepsy">癲癇</button>
    <button class="pill" data-cat="sleep">睡眠</button>
    <button class="pill" data-cat="neuro">神經科</button>
    <button class="pill" data-cat="other">其他</button>
    <div class="pill-divider"></div>
    <span class="pill-label">地區：</span>
    <button class="pill region active" data-region="all">全台</button>
    <button class="pill region" data-region="north">🏙️ 北部</button>
    <button class="pill region" data-region="central">🏔️ 中部</button>
    <button class="pill region" data-region="south">🌊 南部</button>
    <button class="pill region" data-region="kaoping">🌇 高屏</button>
    <button class="pill region" data-region="east">🌿 東部</button>
    <button class="pill region" data-region="online">💻 線上</button>
  </div>

  <div class="stats" id="stats"></div>
  <div class="grid" id="grid"></div>

  <footer>
    資料擷取自 <a href="https://www.neuro.org.tw/active/other_list.asp" target="_blank">台灣神經學學會 · 其他活動</a>｜
    更新於 {update_str}｜每週日 15:00 自動更新
  </footer>
</div>

<script>
const EVENTS = {events_js};

const WEEKDAYS = ['日','一','二','三','四','五','六'];
const CAT_LABELS = {{
  movement:'動作障礙', dementia:'失智症', stroke:'腦中風',
  epilepsy:'癲癇', sleep:'睡眠', neuro:'神經科', other:'其他'
}};
const REGION_LABELS = {{
  north:'🏙️ 北部', central:'🏔️ 中部', south:'🌊 南部',
  kaoping:'🌇 高屏', east:'🌿 東部', online:'💻 線上', other:'其他'
}};

const today = new Date('{TODAY.strftime("%Y-%m-%d")}');
today.setHours(0,0,0,0);

function daysLeft(dateStr) {{
  const d = new Date(dateStr); d.setHours(0,0,0,0);
  return Math.round((d - today) / 86400000);
}}
function fmtDate(dateStr) {{
  const d = new Date(dateStr);
  return `${{d.getFullYear()}}/${{String(d.getMonth()+1).padStart(2,'0')}}/${{String(d.getDate()).padStart(2,'0')}}`;
}}
function getMonthKey(dateStr) {{
  const d = new Date(dateStr);
  return `${{d.getFullYear()}}年${{d.getMonth()+1}}月`;
}}

let currentCat = 'all', currentRegion = 'all', currentSearch = '', currentSort = 'date-asc';

function getFiltered() {{
  let data = [...EVENTS];
  if (currentCat !== 'all') data = data.filter(e => e.cat === currentCat);
  if (currentRegion !== 'all') data = data.filter(e => e.region === currentRegion);
  if (currentSearch) {{
    const q = currentSearch.toLowerCase();
    data = data.filter(e => e.title.toLowerCase().includes(q) || e.location.toLowerCase().includes(q));
  }}
  data.sort((a,b) => {{
    if (currentSort === 'date-asc')     return a.date.localeCompare(b.date);
    if (currentSort === 'date-desc')    return b.date.localeCompare(a.date);
    if (currentSort === 'credits-desc') return (b.credits||0) - (a.credits||0);
    return 0;
  }});
  return data;
}}

function render() {{
  const data = getFiltered();
  const grid = document.getElementById('grid');
  document.getElementById('stats').textContent = `顯示 ${{data.length}} / ${{EVENTS.length}} 筆活動`;
  if (!data.length) {{
    grid.innerHTML = '<div class="no-results">😔 沒有符合條件的活動</div>';
    return;
  }}
  let html = '', lastMonth = '';
  data.forEach(e => {{
    const mk = getMonthKey(e.date);
    if (currentSort === 'date-asc' && mk !== lastMonth) {{
      html += `<div class="month-divider"><span>📅 ${{mk}}</span></div>`;
      lastMonth = mk;
    }}
    const dl = daysLeft(e.date);
    const wd = WEEKDAYS[new Date(e.date).getDay()];
    let dlClass = 'days-left', dlText = `${{dl}} 天後`;
    if (dl === 0) {{ dlClass += ' today'; dlText = '今天'; }}
    else if (dl <= 7) {{ dlClass += ' soon'; }}
    const creditBadge = e.credits ? `<span class="tag tag-credits">⭐ ${{e.credits}} 積分</span>` : '';
    const onlineBadge = e.online   ? `<span class="tag tag-online">💻 線上</span>` : '';
    const catBadge    = `<span class="tag tag-cat">${{CAT_LABELS[e.cat]||e.cat}}</span>`;
    const regionBadge = `<span class="tag tag-region">${{REGION_LABELS[e.region]||e.region}}</span>`;
    html += `
      <div class="card cat-${{e.cat}}">
        <div class="card-date">
          <span>${{fmtDate(e.date)}}</span>
          <span class="weekday">（${{wd}}）</span>
          <span class="${{dlClass}}">${{dlText}}</span>
        </div>
        <div class="card-title">${{e.title}}</div>
        <div class="card-tags">${{catBadge}}${{regionBadge}}${{creditBadge}}${{onlineBadge}}</div>
        <div class="card-location">${{e.location}}</div>
        <a class="card-link" href="${{e.url}}" target="_blank">詳細資訊 →</a>
      </div>`;
  }});
  grid.innerHTML = html;
}}

document.querySelectorAll('.pill:not(.region)').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.pill:not(.region)').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentCat = btn.dataset.cat;
    render();
  }});
}});
document.querySelectorAll('.pill.region').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.pill.region').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentRegion = btn.dataset.region;
    render();
  }});
}});
document.getElementById('search').addEventListener('input', e => {{
  currentSearch = e.target.value; render();
}});
document.getElementById('sortSel').addEventListener('change', e => {{
  currentSort = e.target.value; render();
}});

render();
</script>
</body>
</html>"""

# ── Git deploy ────────────────────────────────────
def git_deploy(html_content):
    import os
    repo = REPO_DIR

    # clone 或 pull
    if repo.exists():
        subprocess.run(["git", "-C", str(repo), "pull", "--rebase"], check=True)
    else:
        subprocess.run(["git", "clone", REPO_URL, str(repo)], check=True)

    # 設定 git 身份（cron 環境可能沒有）
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "tommy.lan@gmail.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Hermes Auto-Update"], check=True)

    # 寫入 index.html
    (repo / "index.html").write_text(html_content, encoding="utf-8")

    # commit + push
    subprocess.run(["git", "-C", str(repo), "add", "index.html"], check=True)
    result = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached", "--quiet"],
        capture_output=True
    )
    if result.returncode != 0:  # 有變動才 commit
        msg = f"Auto-update: 神經學教育活動 {TODAY.strftime('%Y/%m/%d')}"
        subprocess.run(["git", "-C", str(repo), "commit", "-m", msg], check=True)
        subprocess.run(["git", "-C", str(repo), "push", "origin", "main"], check=True)
        return True, "已更新並推送"
    else:
        return False, "資料無變動，跳過 commit"

def fetch_detail_page(url):
    r = subprocess.run(
        ["curl", "-s", "--max-time", "15", "-L",
         "-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
         url],
        capture_output=True, text=True
    )
    return r.stdout if r.returncode == 0 else ""

# ── 主程式 ────────────────────────────────────────
def main():
    print(f"=== 神經學教育活動自動更新 ===")
    print(f"今天：{TODAY}，截止：{CUTOFF}")
    print(f"開始爬取（最多 {MAX_PAGES} 頁）...")

    all_events = scrape_all()
    future_events = [e for e in all_events if TODAY <= e["date_obj"] <= CUTOFF]
    future_events.sort(key=lambda x: x["date_obj"])

    print(f"\n爬取完畢：共 {len(all_events)} 筆，其中 {len(future_events)} 筆在未來3個月")

    if not future_events:
        print("無未來活動，不更新（可能是來源網站無法存取）")
        # 在 GitHub Actions 以非零結束，讓 GitHub 寄信通知 → 避免像舊排程那樣靜默失敗
        sys.exit(1 if os.environ.get("GITHUB_ACTIONS") == "true" else 0)

    print("更新未來活動的學分資訊...")
    for idx, e in enumerate(future_events):
        print(f"  [{idx+1}/{len(future_events)}] 讀取學分: {e['title'][:30]}...", end="", flush=True)
        detail_html = fetch_detail_page(e["url"])
        credits = 0
        if detail_html:
            m = re.search(r'<th>學\s*分</th>\s*<td>\s*<u>\s*(\d+)\s*</u>', detail_html)
            if m:
                credits = int(m.group(1))
        e["credits"] = credits
        print(f" {credits} 分")

    print("產生 HTML...")
    html = build_html(future_events)

    if os.environ.get("GITHUB_ACTIONS") == "true":
        # GitHub Actions：只寫檔到 repo 根目錄，commit/push 交給 workflow
        # （workflow 已用內建 GITHUB_TOKEN 設好推送權限，毋須再 clone）。
        out_path = Path(__file__).resolve().parent.parent / "index.html"
        out_path.write_text(html, encoding="utf-8")
        changed, msg = True, f"已寫入 {out_path}（交由 workflow commit/push）"
        print(f"[CI] {msg}")
    else:
        print("部署到 GitHub...")
        changed, msg = git_deploy(html)
        print(f"部署結果：{msg}")

    # 輸出摘要（由 cron 系統推送到 Telegram）
    summary_lines = [f"🧠 神經學教育活動已更新 ({TODAY.strftime('%Y/%m/%d')})"]
    summary_lines.append(f"📊 共 {len(future_events)} 筆活動（未來3個月）")
    summary_lines.append(f"🔗 https://tlan1012.github.io/Taiwan_Neurology/")
    summary_lines.append(f"{'✅ 已推送更新' if changed else '⏭️ 資料無變動'}")

    # 列出最近5筆
    summary_lines.append("\n📌 近期活動：")
    for e in future_events[:5]:
        credit_str = f" ⭐{e['credits']}分" if e['credits'] else ""
        summary_lines.append(f"• {e['date']} {e['title'][:25]}...{credit_str}")

    print("\n" + "\n".join(summary_lines))

if __name__ == "__main__":
    main()
