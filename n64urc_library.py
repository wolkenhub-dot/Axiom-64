import os
import sys
import json
import struct
import subprocess
import urllib.request
import urllib.parse
import http.server
import socketserver
import threading
import time
import re
import difflib
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Configurações do RAWG
API_KEY = "14247a239c144af8be5b90650dff95a8"
ROM_DIR = "roms"
CACHE_FILE = "library_cache.json"

class LibraryBackend:
    def __init__(self):
        self.cache = self.load_cache()

    def load_cache(self):
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except: return {}
        return {}

    def save_cache(self):
        try:
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, indent=4, ensure_ascii=False)
        except: pass

    def clean_name(self, filename):
        name = filename.replace(".n64z", "").replace(".z64", "").replace(".n64", "").replace(".v64", "")
        # Remove tags regionais e dumps
        name = re.sub(r'\(.*?\)|\[.*?\]', '', name).strip()
        return name

    def fetch_game_info(self, game_name):
        if game_name in self.cache:
            return self.cache[game_name].copy()

        query = urllib.parse.quote(game_name)
        url = f"https://api.rawg.io/api/games?key={API_KEY}&search={query}&platforms=7"
        
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                data = json.loads(response.read().decode())
                if data['results']:
                    best_res = None
                    best_score = 0
                    for res in data['results'][:5]:
                        res_name = res.get("name", "")
                        score = difflib.SequenceMatcher(None, game_name.lower(), res_name.lower()).ratio()
                        if score > best_score:
                            best_score = score
                            best_res = res
                    
                    if best_res and best_score > 0.4:
                        info = {
                            "name": best_res.get("name", game_name),
                            "released": best_res.get("released", "Desconhecido"),
                            "rating": best_res.get("rating", 0),
                            "background_image": best_res.get("background_image"),
                            "slug": best_res.get("slug", game_name.lower().replace(" ", "-"))
                        }
                        self.cache[game_name] = info
                        return info.copy()
        except: pass
        
        return {"name": game_name, "released": "N/A", "rating": 0, "background_image": None, "slug": game_name.lower().replace(" ", "-")}

    def scan_roms(self):
        if not os.path.exists(ROM_DIR):
            os.makedirs(ROM_DIR)
        
        rom_files = sorted([f for f in os.listdir(ROM_DIR) if f.lower().endswith(".n64z")])
        
        def process_one(f):
            clean = self.clean_name(f)
            metadata = self.fetch_game_info(clean)
            return (f, metadata)

        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(process_one, rom_files))

        games_map = {}
        for f, metadata in results:
            slug = metadata['slug']
            if slug not in games_map:
                games_map[slug] = metadata
                games_map[slug]['files'] = []
            games_map[slug]['files'].append(f)
            
        self.save_cache()
        return sorted(list(games_map.values()), key=lambda x: x['name'])

# --- SERVER LOGIC ---

PORT = 6464
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Axiom-64 Vault</title>
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&family=Outfit:wght@300;600&display=swap" rel="stylesheet">
    <style>
        :root {
            --n64-gray: #7a7a7a;
            --n64-dark-gray: #4d4d4d;
            --n64-red: #ff0000;
            --n64-yellow: #ffcc00;
            --neon-blue: #00f2ff;
        }

        body {
            margin: 0;
            background: #020202;
            color: #ccc;
            font-family: 'Outfit', sans-serif;
            overflow-x: hidden;
            display: flex;
            flex-direction: column;
            min-height: 100vh;
        }

        header {
            padding: 40px;
            text-align: center;
            border-bottom: 1px solid #1a1a1a;
        }

        h1 {
            font-family: 'Orbitron', sans-serif;
            font-size: 1.2rem;
            letter-spacing: 5px;
            color: #555;
            margin: 0;
            text-transform: uppercase;
        }

        .container {
            max-width: 1400px;
            margin: 50px auto;
            padding: 20px;
            flex: 1;
        }

        /* 3D Cartridge Grid */
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
            gap: 60px;
            perspective: 1000px;
        }

        .cartridge-wrap {
            position: relative;
            cursor: pointer;
            transition: 0.4s;
            transform-style: preserve-3d;
        }

        .cartridge-wrap:hover {
            transform: translateY(-20px) rotateX(10deg);
        }

        /* N64 Cartridge Shape CSS */
        .cartridge {
            position: relative;
            width: 200px;
            height: 150px;
            background: var(--n64-gray);
            border-radius: 10px 10px 5px 5px;
            box-shadow: 
                inset 0 10px 20px rgba(255,255,255,0.1),
                0 15px 30px rgba(0,0,0,0.8);
            overflow: hidden;
            border: 2px solid #555;
            display: flex;
            flex-direction: column;
            align-items: center;
        }

        /* Top Curved Part */
        .cartridge::before {
            content: '';
            position: absolute;
            top: -5px; width: 80%; height: 20px;
            background: var(--n64-dark-gray);
            border-radius: 50%;
            z-index: 0;
        }

        .label {
            width: 80%;
            height: 60%;
            background: #111;
            margin-top: 25px;
            border-radius: 4px;
            border: 2px solid #222;
            z-index: 1;
            background-size: cover;
            background-position: center;
            box-shadow: inset 0 0 10px rgba(0,0,0,0.5);
            transition: 0.3s;
        }

        .cartridge-wrap:hover .label {
            filter: brightness(1.2);
        }

        .cart-info {
            margin-top: 15px;
            text-align: center;
            font-family: 'Orbitron', sans-serif;
            font-size: 0.7rem;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 1px;
            transition: 0.3s;
        }

        .cartridge-wrap:hover .cart-info {
            color: #fff;
        }

        .count-badge {
            position: absolute;
            bottom: -5px; right: -5px;
            background: var(--n64-red);
            color: white;
            font-size: 10px;
            padding: 2px 6px;
            border-radius: 50%;
            z-index: 10;
        }

        /* Modal Minimalist */
        #modal {
            display: none;
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.9);
            z-index: 2000;
            backdrop-filter: blur(20px);
            align-items: center; justify-content: center;
        }

        .modal-content {
            background: #0a0a0a;
            width: 85%; max-width: 600px;
            padding: 40px;
            border: 1px solid #222;
            border-radius: 10px;
            text-align: center;
        }

        h2 { font-family: 'Orbitron'; font-size: 1.5rem; color: #fff; margin-bottom: 5px; }
        .meta { font-size: 0.8rem; color: #555; margin-bottom: 30px; }

        .file-list { display: flex; flex-direction: column; gap: 10px; }
        .file-item {
            background: #111; border: 1px solid #1a1a1a;
            padding: 15px; border-radius: 5px;
            display: flex; justify-content: space-between; align-items: center;
            transition: 0.2s;
        }
        .file-item:hover { border-color: var(--n64-red); }

        .btn-play {
            background: #333; color: #fff; border: none;
            padding: 8px 20px; font-family: 'Orbitron'; font-size: 0.7rem;
            cursor: pointer; border-radius: 3px; transition: 0.3s;
        }
        .btn-play:hover { background: var(--n64-red); }

        .close-btn { position: absolute; top: 20px; right: 20px; color: #333; cursor: pointer; font-size: 2rem; }
        .close-btn:hover { color: #fff; }

        .loading { font-family: 'Orbitron'; letter-spacing: 5px; color: #222; margin-top: 200px; text-align: center; }
    </style>
</head>
<body>
    <header>
        <h1>Axiom-64 Collection</h1>
    </header>

    <div class="container">
        <div id="loader" class="loading">SCANNING SYSTEM...</div>
        <div id="grid" class="grid"></div>
    </div>

    <div id="modal">
        <span class="close-btn" onclick="closeModal()">&times;</span>
        <div class="modal-content">
            <h2 id="m-title">Game Title</h2>
            <div class="meta" id="m-meta">YEAR | RATING</div>
            <div class="file-list" id="m-files"></div>
        </div>
    </div>

    <script>
        let currentGames = [];

        async function loadLibrary() {
            try {
                const res = await fetch('/api/roms');
                const data = await res.json();
                currentGames = data;
                renderGrid(data);
                document.getElementById('loader').style.display = 'none';
            } catch(e) {
                document.getElementById('loader').innerText = 'ERROR';
            }
        }

        function renderGrid(games) {
            const grid = document.getElementById('grid');
            grid.innerHTML = '';
            games.forEach((game, index) => {
                const img = game.background_image || '';
                const vCount = game.files.length;
                grid.innerHTML += `
                    <div class="cartridge-wrap" onclick="openModal(${index})">
                        <div class="cartridge">
                            <div class="label" style="background-image: url('${img}')"></div>
                        </div>
                        ${vCount > 1 ? `<div class="count-badge">${vCount}</div>` : ''}
                        <div class="cart-info">${game.name}</div>
                    </div>
                `;
            });
        }

        function openModal(index) {
            const game = currentGames[index];
            document.getElementById('m-title').innerText = game.name;
            document.getElementById('m-meta').innerText = `${game.released} | ★ ${game.rating}`;
            
            const fileList = document.getElementById('m-files');
            fileList.innerHTML = '';
            game.files.forEach(filename => {
                fileList.innerHTML += `
                    <div class="file-item">
                        <div style="font-size: 0.8rem; color: #888">${filename}</div>
                        <button class="btn-play" onclick="playGame('${filename}', this)">JOGAR</button>
                    </div>
                `;
            });
            document.getElementById('modal').style.display = 'flex';
        }

        function closeModal() { document.getElementById('modal').style.display = 'none'; }

        async function playGame(filename, btn) {
            btn.innerText = 'AGUARDE';
            try {
                await fetch(`/api/play?file=${encodeURIComponent(filename)}`);
            } finally {
                setTimeout(() => { btn.innerText = 'JOGAR'; }, 1000);
            }
        }

        loadLibrary();
        window.addEventListener('keydown', (e) => { if(e.key === 'Escape') closeModal(); });
    </script>
</body>
</html>
"""

class RequestHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args): return

    def do_GET(self):
        parsed_path = urllib.parse.urlparse(self.path)
        if parsed_path.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML_CONTENT.encode())
        elif parsed_path.path == '/api/roms':
            data = backend.scan_roms()
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        elif parsed_path.path == '/api/play':
            query = urllib.parse.parse_qs(parsed_path.query)
            filename = query.get('file', [None])[0]
            if filename:
                path = os.path.join(ROM_DIR, filename)
                subprocess.Popen([sys.executable, "n64urc_launcher.py", path])
                self.send_response(200)
                self.end_headers()
            else: self.send_error(400)
        else: self.send_error(404)

def run_server():
    with socketserver.TCPServer(("", PORT), RequestHandler) as httpd:
        httpd.serve_forever()

if __name__ == "__main__":
    backend = LibraryBackend()
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    time.sleep(0.5)
    webbrowser.open(f"http://localhost:{PORT}")
    print(f"🚀 Axiom-64 Vault Active: http://localhost:{PORT}")
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt: pass
