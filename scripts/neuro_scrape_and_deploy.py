#!/usr/bin/env python3
"""
neuro_scrape_and_deploy.py
每週日由 GitHub Actions 自動執行（見 .github/workflows/neuro-update.yml）：
1. 爬取 11 個學會/平台的教育活動（多來源，單一來源失敗不影響其他來源）
2. 過濾今日起3個月內的活動、跨來源去重（同日期+相似標題，學會官網優先）
3. 產生 index.html（含類別/地區[北中南東線上]/來源 filter）
4. 更新 index.html → 由 workflow commit + push 到 TLAN1012/Taiwan_Neurology

資料來源：
  neuro     台灣神經學學會        neuro.org.tw/active/other_list.asp
  stroke    台灣腦中風學會        stroke.org.tw/news/knowledge_list.asp（僅 HTTP）
  tmds      台灣動作障礙學會      tmds.org.tw/class/class_list.asp
  tncs      台灣神經重症醫學會    tncs.org.tw/active/active_list.asp
  tnms      台灣神經免疫醫學會    member.tnms.com.tw/active/list.asp
  pmr       台灣復健醫學會        pmr.org.tw/active_news/active.asp
  tssm      台灣睡眠醫學學會      tssm.org.tw/learn_list.php（含學分欄位）
  tsim      台灣內科醫學會        tsim.org.tw/ehc-tsim/...（民國年；神經相關或線上）
  headache  台灣頭痛學會          WordPress REST API（JSON）
  epilepsy  台灣癲癇醫學會        Drupal RSS feed
  tma       醫師公會全聯會        tma.tw/credit/index_06.asp（全國總表；神經相關或線上）
  tafm      台灣家庭醫學醫學會    tafm.org.tw 首頁精選課程（免驗證碼；含詳細頁欄位）

註：家醫科醫學會（TAFM）的「完整課程查詢」頁有伺服器端驗證碼無法直接爬，
    改抓其首頁免驗證碼渲染的近期精選課程（學會主打的年會/認證課程），
    其餘申請西醫師積分之家醫課程另由全聯會總表（tma）按主辦單位涵蓋。

執行環境：
- GitHub Actions（GITHUB_ACTIONS=true）：只把 index.html 寫回 repo 根目錄，
  交由 workflow 用內建 GITHUB_TOKEN 負責 commit/push。
- 本機手動執行：走 git_deploy（clone 到 /tmp 後自行 push）。
"""

import subprocess, re, json, sys, os, html as htmllib
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path

# ── 設定 ──────────────────────────────────────────
TODAY      = date.today()
CUTOFF     = TODAY + timedelta(days=92)   # ~3個月
REPO_URL   = "https://github.com/TLAN1012/Taiwan_Neurology.git"
REPO_DIR   = Path("/tmp/Taiwan_Neurology_deploy")
UA         = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")

SRC_LABELS = {
    "neuro":    "神經學學會",
    "stroke":   "腦中風學會",
    "tmds":     "動作障礙學會",
    "tncs":     "神經重症醫學會",
    "tnms":     "神經免疫醫學會",
    "pmr":      "復健醫學會",
    "tssm":     "睡眠醫學學會",
    "tsim":     "內科醫學會",
    "headache": "頭痛學會",
    "epilepsy": "癲癇醫學會",
    "tma":      "醫師公會全聯會",
    "tafm":     "家庭醫學醫學會",
}
# 去重優先序：數字小者優先保留（學會官網 > 睡眠 > 內科 > 全聯會總表）
SRC_PRIORITY = {"neuro": 1, "stroke": 1, "tmds": 1, "tncs": 1, "tnms": 1,
                "pmr": 1, "headache": 1, "epilepsy": 1, "tafm": 1,
                "tssm": 2, "tsim": 3, "tma": 4}

# ── 共用工具 ──────────────────────────────────────
def fetch(url, extra_args=None, timeout=40, attempts=3):
    """curl 抓網頁（帶瀏覽器 UA；TLS 偶發失敗自動重試）"""
    for i in range(attempts):
        cmd = ["curl", "-s", "--max-time", str(timeout), "-L", "-A", UA]
        if extra_args:
            cmd += extra_args
        cmd.append(url)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
    return ""

def clean_text(s):
    s = htmllib.unescape(s)
    s = re.sub(r"<!--|-->", " ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def parse_date(y, m, d):
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None

def make_event(d, title, source, url="", organizer="", location="",
               credits=0, credit_text=""):
    title = clean_text(title)
    if not title or d is None:
        return None
    return {
        "date":      d.strftime("%Y/%m/%d"),
        "date_obj":  d,
        "title":     title,
        "url":       url,
        "organizer": clean_text(organizer),
        "location":  clean_text(location),
        "credits":   credits,
        "credit_text": clean_text(credit_text),
        "source":    source,
    }

def in_window(e):
    return e and TODAY <= e["date_obj"] <= CUTOFF

# ── 神經相關性過濾（僅套用於 tma / tsim 兩個「全國總表」型來源）──
RELEVANT_KW = [
    "神經", "腦", "中風", "癲癇", "失智", "認知", "巴金森", "帕金森", "動作障礙",
    "頭痛", "偏頭痛", "睡眠", "疼痛", "復健", "眩暈", "暈眩", "顫抖", "肌無力",
    "多發性硬化", "脊髓", "周邊神經", "週邊神經", "肌電圖", "腦電圖", "EEG",
    "電生理", "神智", "譫妄", "帶狀疱疹後神經痛",
    # 糖尿病照護網（共照網認證/展延學分課程）
    "糖尿病", "血糖", "胰島素", "腸泌素", "共同照護", "共照網", "糖心腎",
    "DKD", "Diabetes", "diabetes", "DIABETES",
    "Neuro", "neuro", "NEURO", "Stroke", "stroke", "Epilep", "epilep",
    "Parkinson", "parkinson", "Dementia", "dementia", "Alzheimer",
    "Headache", "headache", "Migraine", "migraine", "Sleep", "Pain",
    "Cognitive", "cognitive", "Rehab", "rehab", "Spine", "spine",
]
# 主辦單位屬目標學會者一律納入（家醫/內科課程經此涵蓋）
RELEVANT_ORG = ["神經", "腦中風", "癲癇", "動作障礙", "失智", "頭痛", "睡眠",
                "疼痛", "復健", "家庭醫學", "內科醫學會"]

def is_relevant(title, organizer="", location=""):
    """神經相關，或線上課程（線上不受地點限制，一律列入方便蒐集積分）"""
    if is_online(title + " " + location):
        return True
    for kw in RELEVANT_ORG:
        if kw in organizer:
            return True
    for kw in RELEVANT_KW:
        if kw in title:
            return True
    return False

# ── 各來源爬蟲 ────────────────────────────────────
def src_neuro():
    """台灣神經學學會（原有來源；?page=N，另抓詳細頁學分）"""
    base = "https://www.neuro.org.tw/active"
    events, seen, no_future = [], set(), 0
    for page in range(1, 16):
        url = f"{base}/other_list.asp" + ("" if page == 1 else f"?page={page}")
        h = fetch(url)
        rows = list(re.finditer(
            r'<a href="(other_toApply_OK\.asp\?SID=(\d+))"[^>]*>([^<]*)</a><br>?([^<\n\r]*)', h))
        new = 0
        future_on_page = 0
        for m in rows:
            sid = m.group(2)
            if sid in seen:
                continue
            seen.add(sid)
            new += 1
            organizer = m.group(3).strip()
            title = m.group(4).strip() or organizer
            before = h[max(0, m.start() - 300):m.start()]
            dm = re.findall(r"(\d{4})/(\d{1,2})/(\d{1,2})", before)
            if not dm:
                continue
            d = parse_date(*dm[-1])
            e = make_event(d, title, "neuro",
                           url=f"{base}/{m.group(1)}", organizer=organizer)
            if in_window(e):
                events.append(e)
                future_on_page += 1
        if not rows or new == 0:
            break
        no_future = no_future + 1 if future_on_page == 0 else 0
        if no_future >= 2:
            break
    # 詳細頁抓學分與活動地點
    for e in events:
        h = fetch(e["url"], timeout=15, attempts=2)
        m = re.search(r"<th>學\s*分</th>\s*<td>\s*<u>\s*(\d+)\s*</u>", h)
        if m:
            e["credits"] = int(m.group(1))
        m = re.search(r"<th>活動地點</th>\s*<td>(.*?)</td>", h, re.DOTALL)
        if m:
            e["location"] = clean_text(m.group(1))[:80]
    return events

def src_stroke():
    """台灣腦中風學會（HTTPS 憑證失效，僅 HTTP；日期由近到遠）"""
    base = "http://www.stroke.org.tw/news"
    events = []
    for page in range(1, 6):
        url = f"{base}/knowledge_list.asp" + ("" if page == 1 else f"?/{page}.html")
        h = fetch(url)
        rows = re.findall(
            r"<span>活動日期：</span>\s*(\d{4})-(\d{1,2})-(\d{1,2})</li>\s*"
            r'<li[^>]*>\s*<a href="(knowledge_info\.asp\?/\d+\.html)">(.*?)</a>',
            h, re.DOTALL)
        if not rows:
            break
        page_events = [make_event(parse_date(y, m_, d), t, "stroke",
                                  url=f"{base}/{href}", organizer="台灣腦中風學會")
                       for y, m_, d, href, t in rows]
        events += [e for e in page_events if in_window(e)]
        # 列表由新到舊：整頁都早於今天就不用再往後翻
        if all(e and e["date_obj"] < TODAY for e in page_events):
            break
    return events

def src_tmds():
    """台灣動作障礙學會（會議及活動報名）"""
    base = "https://www.tmds.org.tw/class"
    events = []
    for page in range(1, 8):
        url = f"{base}/class_list.asp" + ("" if page == 1 else f"?/{page}.html")
        h = fetch(url)
        rows = re.findall(
            r"<span>上課日期：</span>(\d{4})/(\d{1,2})/(\d{1,2})</li>"
            r'<li><a href="(class_info\.asp\?/\d+\.html)">(.*?)</a>',
            h, re.DOTALL)
        if not rows:
            break
        page_events = [make_event(parse_date(y, m_, d), t, "tmds",
                                  url=f"{base}/{href}", organizer="台灣動作障礙學會")
                       for y, m_, d, href, t in rows]
        events += [e for e in page_events if in_window(e)]
        if all(e and e["date_obj"] < TODAY for e in page_events):
            break
    return events

def src_tncs():
    """台灣神經重症醫學會"""
    base = "https://www.tncs.org.tw/active"
    events = []
    for page in range(1, 8):
        url = f"{base}/active_list.asp" + ("" if page == 1 else f"?/{page}.html")
        h = fetch(url)
        rows = re.findall(
            r"<b>上課日期：</b></span>(\d{4})/(\d{1,2})/(\d{1,2})</li>\s*"
            r'<li[^>]*><span>.*?</span><a href="(active_info\.asp\?/\d+\.html)">\s*<b>(.*?)</b></a>',
            h, re.DOTALL)
        if not rows:
            break
        page_events = [make_event(parse_date(y, m_, d), t, "tncs",
                                  url=f"{base}/{href}", organizer="台灣神經重症醫學會")
                       for y, m_, d, href, t in rows]
        events += [e for e in page_events if in_window(e)]
        if all(e and e["date_obj"] < TODAY for e in page_events):
            break
    return events

def src_tnms():
    """台灣神經免疫醫學會（研討會訊息卡片）"""
    base = "https://member.tnms.com.tw/active"
    events = []
    for page in range(1, 7):
        url = f"{base}/list.asp" + ("" if page == 1 else f"?/{page}.html")
        h = fetch(url)
        cards = re.findall(
            r'<h2><a href="(index\.asp\?/\d+\.html)">(.*?)</a></h2>(.*?)(?=<h2>|<div class="mPage|$)',
            h, re.DOTALL)
        if not cards:
            break
        page_events = []
        for href, title, body in cards:
            dm = re.search(r"活動日期：\s*(\d{4})\.(\d{1,2})\.(\d{1,2})", body)
            if not dm:
                continue
            lm = re.search(r"會議地點：\s*([^<\r\n]+)", body)
            e = make_event(parse_date(*dm.groups()), title, "tnms",
                           url=f"{base}/{href}", organizer="台灣神經免疫醫學會",
                           location=lm.group(1) if lm else "")
            page_events.append(e)
        events += [e for e in page_events if in_window(e)]
        if page_events and all(e and e["date_obj"] < TODAY for e in page_events):
            break
    return events

def src_pmr():
    """台灣復健醫學會（學術活動；標題內嵌積分文字）"""
    base = "https://www.pmr.org.tw/active_news"
    events = []
    for page in range(1, 9):
        url = f"{base}/active.asp" + ("" if page == 1 else f"?/{page}.html")
        h = fetch(url, extra_args=["--tls-max", "1.2"])
        rows = re.findall(
            r'<li class="text-dateO"><span>日期：</span>(\d{4})/(\d{1,2})/(\d{1,2})</li>\s*'
            r'<li><a href="(active_info\.asp\?/\d+\.html)">(.*?)</a>(.*?)</li>',
            h, re.DOTALL)
        if not rows:
            break
        for y, m_, d, href, title, extra in rows:
            om = re.search(r"主辦：([^<]+)", extra)
            lm = re.search(r"地點：([^<]+)", extra)
            cm = re.search(r"[（(]共\s*([\d.]+)\s*分[）)]", title)
            e = make_event(parse_date(y, m_, d), title, "pmr",
                           url=f"{base}/{href}",
                           organizer=om.group(1) if om else "台灣復健醫學會",
                           location=lm.group(1) if lm else "",
                           credit_text=f"復健 {cm.group(1)} 分" if cm else "")
            if in_window(e):
                events.append(e)
    return events

def src_tssm():
    """台灣睡眠醫學學會（含結構化學分欄位；?page=N 由遠到近）"""
    base = "https://www.tssm.org.tw"
    events = []
    for page in range(1, 12):
        url = f"{base}/learn_list.php" + ("" if page == 1 else f"?page={page}")
        h = fetch(url)
        rows = re.findall(
            r'<tr>\s*<td align="center" width="10%">(\d{4})-(\d{2})-(\d{2})</td>\s*'
            r"<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*"
            r"<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>",
            h, re.DOTALL)
        if not rows:
            break
        page_events = []
        for y, m_, d, title, _time, _hours, credit, org, status in rows:
            if "通過" not in status:
                continue
            try:
                cr = float(clean_text(credit))
            except ValueError:
                cr = 0
            e = make_event(parse_date(y, m_, d), title, "tssm",
                           url=f"{base}/learn_list.php",
                           organizer=org,
                           credits=cr,
                           credit_text=f"睡眠 {clean_text(credit)} 學分" if cr else "")
            page_events.append(e)
        events += [e for e in page_events if in_window(e)]
        # 列表由遠到近（未來→過去）：整頁都早於今天即可停
        if page_events and all(e and e["date_obj"] < TODAY for e in page_events):
            break
    return events

def src_tsim():
    """台灣內科醫學會（單頁全量；民國年；收神經相關或線上課程）"""
    base = "https://www.tsim.org.tw"
    h = fetch(f"{base}/ehc-tsim/s/w/edu/schedule/schedule", timeout=60)
    events = []
    items = re.findall(
        r'<li class="col-xs-12 col-md-6"><a href="(/ehc-tsim/s/w/edu/scheduleInfo1/schedule/[0-9a-f]+)">'
        r'\s*<div class="Txt">\s*<h4>(.*?)</h4>(.*?)</a>\s*</li>',
        h, re.DOTALL)
    for href, title, body in items:
        text = re.sub(r"<svg.*?</svg>", "\x01", body, flags=re.DOTALL)
        parts = [clean_text(p) for p in text.split("\x01")]
        parts = [p for p in parts if p]
        dm = re.search(r"(\d{3})/(\d{1,2})/(\d{1,2})", body)
        if not dm:
            continue
        d = parse_date(int(dm.group(1)) + 1911, dm.group(2), dm.group(3))
        location  = parts[1] if len(parts) > 1 else ""
        organizer = re.sub(r"認定類別.*$", "", parts[2]).strip() if len(parts) > 2 else ""
        title_c = clean_text(title)
        if not is_relevant(title_c, organizer, location):
            continue
        e = make_event(d, title_c, "tsim", url=f"{base}{href}",
                       organizer=organizer, location=location)
        if in_window(e):
            events.append(e)
    return events

def src_headache():
    """台灣頭痛學會（WordPress REST API；活動日期取自標題 YYYYMMDD）"""
    raw = fetch("https://taiwanheadache.org.tw/wp-json/wp/v2/posts"
                "?categories=41&per_page=100")
    events = []
    try:
        posts = json.loads(raw)
    except (ValueError, TypeError):
        return events
    for p in posts:
        title = clean_text(p.get("title", {}).get("rendered", ""))
        content = p.get("content", {}).get("rendered", "")[:3000]
        dm = re.search(r"\b(20\d{2})(\d{2})(\d{2})\b", title) or \
             re.search(r"\b(20\d{2})(\d{2})(\d{2})\b", content)
        if not dm:
            continue
        d = parse_date(*dm.groups())
        e = make_event(d, title, "headache", url=p.get("link", ""),
                       organizer="台灣頭痛學會")
        if in_window(e):
            events.append(e)
    return events

def src_epilepsy():
    """台灣癲癇醫學會（RSS；活動日期取自標題/附件檔名 YYYYMMDD）"""
    x = fetch("https://www.epilepsy.org.tw/taxonomy/term/4/feed")
    events = []
    for it in re.findall(r"<item>(.*?)</item>", x, re.DOTALL):
        tm = re.search(r"<title>(.*?)</title>", it, re.DOTALL)
        lm = re.search(r"<link>(.*?)</link>", it, re.DOTALL)
        desc = re.search(r"<description>(.*?)</description>", it, re.DOTALL)
        title = clean_text(tm.group(1)) if tm else ""
        blob = title + " " + (htmllib.unescape(desc.group(1)) if desc else "")
        dm = re.search(r"\b(20\d{2})(\d{2})(\d{2})\b", blob)
        if not dm:
            continue
        d = parse_date(*dm.groups())
        title = re.sub(r"[-.．]?\s*歡迎報名參加\s*$", "", title)
        e = make_event(d, title, "epilepsy",
                       url=(lm.group(1).strip() if lm else ""),
                       organizer="台灣癲癇醫學會")
        if in_window(e):
            events.append(e)
    return events

def src_tma():
    """醫師公會全聯會 全國課程總表（日期由近到遠遞增；收神經相關或線上課程）"""
    base = "https://www.tma.tw/credit"
    events, prev_rows = [], None
    for page in range(1, 61):
        url = f"{base}/index_06.asp" + ("" if page == 1 else f"?/{page}.html")
        h = fetch(url)
        rows = re.findall(
            r"<li><span>活動主題:<br></span>(.*?)</li>\s*"
            r"<li><span>活動日期:<br></span>(.*?)</li>\s*"
            r"<li><span>活動地點:<br></span>(.*?)</li>\s*"
            r"<li><span>主辦單位 ／主講人:<br></span>(.*?)</li>.*?"
            r"<li><span>積分類別:<br></span>(.*?)</li>",
            h, re.DOTALL)
        # 超過實際頁數時此站會一直回傳最後一頁 → 內容重複即停
        if not rows or rows == prev_rows:
            break
        prev_rows = rows
        dates_on_page = []
        for title, dstr, loc, org, credit in rows:
            dm = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", dstr)
            if not dm:
                continue
            d = parse_date(*dm.groups())
            if d is None:
                continue
            dates_on_page.append(d)
            title_c, org_c = clean_text(title), clean_text(org)
            loc_c = clean_text(loc)
            if not (TODAY <= d <= CUTOFF) or not is_relevant(title_c, org_c, loc_c):
                continue
            events.append(make_event(
                d, title_c, "tma", url=f"{base}/index_06.asp",
                organizer=org_c, location=loc, credit_text=credit))
        # 列表按日期遞增：整頁都超過截止日即可停
        if dates_on_page and min(dates_on_page) > CUTOFF:
            break
    return [e for e in events if e]

def src_tafm():
    """台灣家庭醫學醫學會（首頁免驗證碼精選課程；民國年）

    完整課程查詢頁需伺服器端驗證碼，故改抓首頁渲染的近期精選課程
    （學會主打的年會/認證課程），全數列入（家醫課程視同基層相關）。
    首頁走 http（其 https 憑證經 proxy 驗證失敗）。
    """
    base = "http://www.tafm.org.tw"
    h = fetch(f"{base}/ehc-tafm/s/index.htm")
    events = []
    blocks = re.findall(
        r'<li>\s*<div class="row">(.*?)</div>\s*'
        r'<div class="title"><a href="(/ehc-tafm/s/w/edu/scheduleInfo1/schedule/[0-9a-f]+)">'
        r'(.*?)</a></div>\s*<div class="row info">(.*?)</div>',
        h, re.DOTALL)
    seen = set()
    for tags, href, title, info in blocks:
        dm = re.search(r"(\d{3})/(\d{1,2})/(\d{1,2})", info)
        if not dm:
            continue
        d = parse_date(int(dm.group(1)) + 1911, dm.group(2), dm.group(3))
        cm = re.search(r"積分<span[^>]*>([\d.]+)", tags)
        lm = re.search(r'fa-map-marker"></i>(.*?)</span>', info, re.DOTALL)
        try:
            credits = float(cm.group(1)) if cm else 0
        except ValueError:
            credits = 0
        e = make_event(d, title, "tafm", url=f"{base}{href}",
                       organizer="台灣家庭醫學醫學會",
                       location=lm.group(1) if lm else "",
                       credits=credits,
                       credit_text=f"家醫 {cm.group(1)} 積分" if cm else "")
        if e and e["url"] not in seen and in_window(e):
            seen.add(e["url"])
            events.append(e)
    return events

FETCHERS = {
    "neuro": src_neuro, "stroke": src_stroke, "tmds": src_tmds,
    "tncs": src_tncs, "tnms": src_tnms, "pmr": src_pmr,
    "tssm": src_tssm, "tsim": src_tsim, "headache": src_headache,
    "epilepsy": src_epilepsy, "tma": src_tma, "tafm": src_tafm,
}

# ── 跨來源去重 ────────────────────────────────────
def _norm_title(t):
    return re.sub(r"[^0-9a-z一-鿿]", "", t.lower())

def _similar(a, b):
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return False
    if len(na) >= 8 and len(nb) >= 8 and (na in nb or nb in na):
        return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.8

def dedupe(events):
    """同日期 + 相似標題視為同一活動；保留優先序高（學會官網）者"""
    events = sorted(events, key=lambda e: (e["date_obj"],
                                           SRC_PRIORITY[e["source"]]))
    kept = []
    for e in events:
        if any(k["date_obj"] == e["date_obj"] and _similar(k["title"], e["title"])
               for k in kept):
            continue
        kept.append(e)
    return kept

# ── 地區分類（北／中／南／東／線上）────────────────
REGION_KEYWORDS = {
    # 註：較長的專有名詞（高雄長庚、嘉義長庚）放在 south，先於 north 的通用詞比對前
    #     由 classify_region 逐區掃描；north 不放單獨「長庚」以免誤收南部長庚。
    "south":   ["台南", "臺南", "高雄", "屏東", "嘉義", "澎湖", "成大", "奇美",
                "高醫", "高長", "高榮", "義大", "郭綜合", "安泰醫", "輔英",
                "南區", "南部", "高屏"],
    "north":   ["台北", "臺北", "新北", "基隆", "桃園", "新竹", "宜蘭", "林口",
                "淡水", "板橋", "新莊", "三重", "中和", "永和", "汐止", "土城",
                "台大", "臺大", "北榮", "北醫", "萬芳", "馬偕", "國泰", "新光",
                "振興", "三軍總醫院", "雙和", "亞東", "耕莘", "和信", "恩主公",
                "長庚轉運站", "張榮發", "南京東路", "文化大學", "北區", "北部"],
    "central": ["台中", "臺中", "彰化", "南投", "雲林", "苗栗", "中榮", "中國醫",
                "中山醫", "澄清", "童綜合", "豐原", "大里", "沙鹿", "光田", "若瑟",
                "中區", "中部", "中南區"],
    "east":    ["花蓮", "台東", "臺東", "花蓮慈濟", "東區", "東部"],
}
ONLINE_KEYWORDS = ["線上", "視訊", "直播", "遠距", "雲端", "網路課程", "webex",
                   "teams", "zoom", "webinar", "online"]

def is_online(text):
    low = text.lower()
    return any(kw in low for kw in ONLINE_KEYWORDS)

def classify_region(e):
    """優先看地點，再看標題/主辦；有實體地點者歸實體區（線上另以 online 旗標標示）"""
    loc_text   = e["location"] + " " + e["organizer"]
    title_text = e["title"]
    for text in (loc_text, title_text):
        for region, kws in REGION_KEYWORDS.items():
            if any(kw in text for kw in kws):
                return region
    if is_online(loc_text + " " + title_text):
        return "online"
    return "other"

# ── 類別分類 ──────────────────────────────────────
CAT_KEYWORDS = {
    "movement": ["動作障礙", "巴金森", "帕金森", "movement disorder", "parkinson"],
    "dementia": ["失智", "ad treatment", "alzheimer", "dementia", "lewy", "阿茲海默", "認知"],
    "stroke":   ["腦中風", "中風", "stroke", "抗栓", "vascular", "血管"],
    "epilepsy": ["癲癇", "epilep", "伊比力斯", "腦電圖", "eeg"],
    "sleep":    ["睡眠", "sleep", "失眠", "安眠", "insomnia", "呼吸中止", "osa"],
    "headache": ["頭痛", "headache", "migraine", "偏頭痛"],
    "pain":     ["疼痛", "pain"],
    "rehab":    ["復健", "rehab", "早療", "早期療育", "兒童發展"],
    "dm":       ["糖尿病", "共同照護網", "共照網", "血糖", "胰島素", "腸泌素",
                 "糖心腎", "dkd", "diabetes", "glp-1", "sglt2"],
    "neuro":    ["神經", "neuro", "肌無力", "nf1", "神經影像", "臨床神經", "頭暈",
                 "眩暈", "急症", "住院醫師", "多發性硬化", "脊髓"],
}

def classify_cat(title):
    low = title.lower()
    for cat, keywords in CAT_KEYWORDS.items():
        for kw in keywords:
            if kw in low:
                return cat
    return "other"

# ── 生成 HTML ─────────────────────────────────────
def build_html(events, src_stats):
    today_str  = TODAY.strftime("%Y/%m/%d")
    cutoff_str = CUTOFF.strftime("%Y/%m/%d")

    js_items = []
    for e in events:
        d_iso  = e["date_obj"].strftime("%Y-%m-%d")
        cat    = classify_cat(e["title"])
        region = classify_region(e)
        online = region == "online" or is_online(
            e["title"] + " " + e["location"] + " " + e["organizer"])
        place = " / ".join(x for x in (e["organizer"], e["location"]) if x)
        js_items.append(
            f'  {{date:"{d_iso}",title:{json.dumps(e["title"], ensure_ascii=False)},'
            f'location:{json.dumps(place, ensure_ascii=False)},'
            f'credits:{json.dumps(e["credits"])},'
            f'ctext:{json.dumps(e["credit_text"], ensure_ascii=False)},'
            f'url:{json.dumps(e["url"], ensure_ascii=False)},'
            f'src:"{e["source"]}",'
            f'cat:"{cat}",online:{"true" if online else "false"},region:"{region}"}}'
        )
    events_js = "[\n" + ",\n".join(js_items) + "\n]"

    src_labels_js = json.dumps(SRC_LABELS, ensure_ascii=False)
    src_pills = "".join(
        f'<button class="pill src" data-src="{k}">{v}</button>'
        for k, v in SRC_LABELS.items())
    stats_line = "｜".join(
        f"{SRC_LABELS[k]} {n}筆" if ok else f"{SRC_LABELS[k]} ⚠️更新失敗"
        for k, (n, ok) in src_stats.items())

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>基層神經科醫師教育活動全集（未來三個月）</title>
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
      display: inline-flex; gap: 20px; margin-top: 14px; flex-wrap: wrap; justify-content: center;
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
    .pill.src {{ border-color: #ffcc80; color: #e65100; }}
    .pill.src:hover  {{ background: #fff3e0; }}
    .pill.src.active {{ background: #e65100; color: #fff; border-color: #e65100; }}
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
    .card.cat-headache  {{ border-left-color: #ad1457; }}
    .card.cat-pain      {{ border-left-color: #ef6c00; }}
    .card.cat-rehab     {{ border-left-color: #558b2f; }}
    .card.cat-dm        {{ border-left-color: #00695c; }}
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
    .tag-src     {{ background: #fff3e0; color: #e65100; }}
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
    footer {{ text-align: center; margin-top: 40px; font-size: .8em; color: #999; line-height: 1.9; }}
    footer a {{ color: #3949ab; text-decoration: none; }}
    @media (max-width: 600px) {{
      header h1 {{ font-size: 1.4em; }}
      .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
<header>
  <h1>🧠 基層神經科醫師教育活動全集</h1>
  <p>神經科與相關學會繼續教育活動 — 未來三個月彙整（{len(SRC_LABELS)} 個資料來源）</p>
  <div class="header-meta">
    <span>📅 {today_str} 起</span>
    <span>⏱️ 截至 {cutoff_str}</span>
    <span>📚 共 {len(events)} 筆</span>
  </div>
</header>

<div class="container">
  <div class="controls">
    <input class="search-box" id="search" type="text" placeholder="搜尋活動名稱、主辦單位或地點…">
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
    <button class="pill" data-cat="headache">頭痛</button>
    <button class="pill" data-cat="pain">疼痛</button>
    <button class="pill" data-cat="rehab">復健</button>
    <button class="pill" data-cat="dm">糖尿病照護網</button>
    <button class="pill" data-cat="neuro">神經科</button>
    <button class="pill" data-cat="other">其他</button>
    <div class="pill-divider"></div>
    <span class="pill-label">地區：</span>
    <button class="pill region active" data-region="all">全台</button>
    <button class="pill region" data-region="north">🏙️ 北部</button>
    <button class="pill region" data-region="central">🏔️ 中部</button>
    <button class="pill region" data-region="south">🌊 南部</button>
    <button class="pill region" data-region="east">🌿 東部</button>
    <button class="pill region" data-region="online">💻 線上</button>
    <button class="pill region" data-region="other">❓ 其他</button>
  </div>

  <div class="pills-bar">
    <span class="pill-label">來源：</span>
    <button class="pill src active" data-src="all">全部</button>
    {src_pills}
  </div>

  <div class="stats" id="stats"></div>
  <div class="grid" id="grid"></div>

  <footer>
    資料來源：{stats_line}<br>
    更新於 {today_str}｜每週日 15:00 自動更新｜地區依活動地點自動判讀，可能有誤，請以主辦單位公告為準
  </footer>
</div>

<script>
const EVENTS = {events_js};

const WEEKDAYS = ['日','一','二','三','四','五','六'];
const CAT_LABELS = {{
  movement:'動作障礙', dementia:'失智症', stroke:'腦中風', epilepsy:'癲癇',
  sleep:'睡眠', headache:'頭痛', pain:'疼痛', rehab:'復健',
  dm:'糖尿病照護網', neuro:'神經科', other:'其他'
}};
const REGION_LABELS = {{
  north:'🏙️ 北部', central:'🏔️ 中部', south:'🌊 南部',
  east:'🌿 東部', online:'💻 線上', other:'❓ 其他'
}};
const SRC_LABELS = {src_labels_js};

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

// 類別、地區、來源皆可複選（空集合＝全部）
let currentCats = new Set(), currentRegions = new Set(), currentSrcs = new Set(),
    currentSearch = '', currentSort = 'date-asc';

function matchRegion(e, r) {{
  return r === 'online' ? (e.online || e.region === 'online') : e.region === r;
}}

function getFiltered() {{
  let data = [...EVENTS];
  if (currentCats.size)    data = data.filter(e => currentCats.has(e.cat));
  if (currentRegions.size) data = data.filter(e => [...currentRegions].some(r => matchRegion(e, r)));
  if (currentSrcs.size)    data = data.filter(e => currentSrcs.has(e.src));
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
    const creditLabel = e.ctext ? e.ctext : (e.credits ? `${{e.credits}} 積分` : '');
    const creditBadge = creditLabel ? `<span class="tag tag-credits">⭐ ${{creditLabel}}</span>` : '';
    const onlineBadge = e.online   ? `<span class="tag tag-online">💻 線上</span>` : '';
    const catBadge    = `<span class="tag tag-cat">${{CAT_LABELS[e.cat]||e.cat}}</span>`;
    const regionBadge = `<span class="tag tag-region">${{REGION_LABELS[e.region]||e.region}}</span>`;
    const srcBadge    = `<span class="tag tag-src">${{SRC_LABELS[e.src]||e.src}}</span>`;
    const linkBtn     = e.url ? `<a class="card-link" href="${{e.url}}" target="_blank">詳細資訊 →</a>` : '';
    html += `
      <div class="card cat-${{e.cat}}">
        <div class="card-date">
          <span>${{fmtDate(e.date)}}</span>
          <span class="weekday">（${{wd}}）</span>
          <span class="${{dlClass}}">${{dlText}}</span>
        </div>
        <div class="card-title">${{e.title}}</div>
        <div class="card-tags">${{catBadge}}${{regionBadge}}${{srcBadge}}${{creditBadge}}${{onlineBadge}}</div>
        <div class="card-location">${{e.location}}</div>
        ${{linkBtn}}
      </div>`;
  }});
  grid.innerHTML = html;
}}

// 複選 pill（類別、地區、來源）：dataKey 為 'all' 者是「全部」鈕
//  · 點分項 → 加入/移除該項並取消「全部」；若全部取消則自動回到「全部」
//  · 點「全部」→ 清空分項選取
function bindMultiPills(selector, dataKey, stateSet) {{
  const pills = [...document.querySelectorAll(selector)];
  const allPill = pills.find(p => p.dataset[dataKey] === 'all');
  pills.forEach(btn => {{
    btn.addEventListener('click', () => {{
      const val = btn.dataset[dataKey];
      if (val === 'all') {{
        stateSet.clear();
        pills.forEach(b => b.classList.remove('active'));
        allPill.classList.add('active');
      }} else {{
        if (stateSet.has(val)) {{ stateSet.delete(val); btn.classList.remove('active'); }}
        else {{ stateSet.add(val); btn.classList.add('active'); }}
        allPill.classList.toggle('active', stateSet.size === 0);
      }}
      render();
    }});
  }});
}}

bindMultiPills('.pill:not(.region):not(.src)', 'cat', currentCats);
bindMultiPills('.pill.region', 'region', currentRegions);
bindMultiPills('.pill.src', 'src', currentSrcs);

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

# ── Git deploy（本機手動執行用）────────────────────
def git_deploy(html_content):
    repo = REPO_DIR
    if repo.exists():
        subprocess.run(["git", "-C", str(repo), "pull", "--rebase"], check=True)
    else:
        subprocess.run(["git", "clone", REPO_URL, str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "tommy.lan@gmail.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Hermes Auto-Update"], check=True)
    (repo / "index.html").write_text(html_content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "index.html"], check=True)
    result = subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--quiet"],
                            capture_output=True)
    if result.returncode != 0:
        msg = f"Auto-update: 神經學教育活動 {TODAY.strftime('%Y/%m/%d')}"
        subprocess.run(["git", "-C", str(repo), "commit", "-m", msg], check=True)
        subprocess.run(["git", "-C", str(repo), "push", "origin", "main"], check=True)
        return True, "已更新並推送"
    return False, "資料無變動，跳過 commit"

# ── 主程式 ────────────────────────────────────────
def main():
    print("=== 神經學相關教育活動自動更新（多來源）===")
    print(f"今天：{TODAY}，截止：{CUTOFF}")

    all_events, src_stats = [], {}
    for key, fetcher in FETCHERS.items():
        label = SRC_LABELS[key]
        print(f"[{key}] 爬取 {label} ...", flush=True)
        try:
            evs = fetcher()
            src_stats[key] = (len(evs), True)
            all_events += evs
            print(f"  → {len(evs)} 筆（未來3個月）")
        except Exception as exc:
            src_stats[key] = (0, False)
            print(f"  ⚠️ 失敗：{exc}")

    before = len(all_events)
    events = dedupe(all_events)
    events.sort(key=lambda e: e["date_obj"])
    print(f"\n彙整：{before} 筆 → 去重後 {len(events)} 筆")

    failed = [SRC_LABELS[k] for k, (_, ok) in src_stats.items() if not ok]
    if failed:
        print(f"⚠️ 更新失敗的來源：{'、'.join(failed)}")

    if not events:
        print("所有來源皆無未來活動（可能全數無法存取），不更新")
        # 在 GitHub Actions 以非零結束，讓 GitHub 寄信通知 → 避免靜默失敗
        sys.exit(1 if os.environ.get("GITHUB_ACTIONS") == "true" else 0)

    print("產生 HTML...")
    html = build_html(events, src_stats)

    if os.environ.get("GITHUB_ACTIONS") == "true":
        out_path = Path(__file__).resolve().parent.parent / "index.html"
        out_path.write_text(html, encoding="utf-8")
        changed, msg = True, f"已寫入 {out_path}（交由 workflow commit/push）"
        print(f"[CI] {msg}")
    else:
        print("部署到 GitHub...")
        changed, msg = git_deploy(html)
        print(f"部署結果：{msg}")

    summary_lines = [f"🧠 基層神經科醫師教育活動已更新 ({TODAY.strftime('%Y/%m/%d')})"]
    summary_lines.append(f"📊 共 {len(events)} 筆活動（未來3個月，{len(FETCHERS)} 個來源）")
    if failed:
        summary_lines.append(f"⚠️ 失敗來源：{'、'.join(failed)}")
    summary_lines.append("🔗 https://tlan1012.github.io/Taiwan_Neurology/")
    summary_lines.append("✅ 已推送更新" if changed else "⏭️ 資料無變動")
    summary_lines.append("\n📌 近期活動：")
    for e in events[:5]:
        summary_lines.append(f"• {e['date']} [{SRC_LABELS[e['source']]}] {e['title'][:25]}")
    print("\n" + "\n".join(summary_lines))

if __name__ == "__main__":
    main()
