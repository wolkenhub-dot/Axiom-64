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
import webbrowser
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
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.cache, f, indent=4, ensure_ascii=False)

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
            with urllib.request.urlopen(url) as response:
                data = json.loads(response.read().decode())
                if data['results']:
                    # Tenta encontrar um match mais preciso comparando o nome
                    res = data['results'][0]
                    # Se o primeiro resultado for muito diferente, talvez a busca fuzzy falhou
                    info = {
                        "name": res.get("name", game_name),
                        "released": res.get("released", "Desconhecido"),
                        "rating": res.get("rating", 0),
                        "background_image": res.get("background_image"),
                        "slug": res.get("slug", game_name.lower().replace(" ", "-"))
                    }
                    self.cache[game_name] = info
                    self.save_cache()
                    return info.copy()
        except Exception as e:
            print(f"Erro ao buscar RAWG para {game_name}: {e}")
        
        return {"name": game_name, "released": "N/A", "rating": 0, "background_image": None, "slug": game_name.lower().replace(" ", "-")}

    def scan_roms(self):
        if not os.path.exists(ROM_DIR):
            os.makedirs(ROM_DIR)
        
        rom_files = sorted([f for f in os.listdir(ROM_DIR) if f.lower().endswith(".n64z")])
        games_map = {}
        
        for f in rom_files:
            clean = self.clean_name(f)
            metadata = self.fetch_game_info(clean)
            slug = metadata['slug']
            
            if slug not in games_map:
                games_map[slug] = metadata
                games_map[slug]['files'] = []
            
            games_map[slug]['files'].append(f)
            
        # Converte o mapa para uma lista ordenada por nome
        library = sorted(list(games_map.values()), key=lambda x: x['name'])
        return library

# --- SERVER LOGIC ---

PORT = 6464
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Axiom-64 Library v2</title>
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&family=Press+Start+2P&family=Outfit:wght@300;600&display=swap" rel="stylesheet">
    <style>
        :root {
            --neon-blue: #00f2ff;
            --n64-red: #ff0000;
            --n64-yellow: #ffcc00;
            --glass: rgba(0, 0, 0, 0.8);
        }

        body {
            margin: 0;
            background: #050505 url('bg.png') no-repeat center center fixed;
            background-size: cover;
            color: white;
            font-family: 'Outfit', sans-serif;
            overflow-x: hidden;
        }

        .crt-overlay {
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: linear-gradient(rgba(18, 16, 16, 0) 50%, rgba(0, 0, 0, 0.2) 50%), 
                        linear-gradient(90deg, rgba(255, 0, 0, 0.03), rgba(0, 255, 0, 0.01), rgba(0, 0, 255, 0.03));
            background-size: 100% 3px, 3px 100%;
            pointer-events: none;
            z-index: 1000;
        }

        header {
            padding: 30px;
            text-align: center;
            background: rgba(0,0,0,0.6);
            backdrop-filter: blur(15px);
            border-bottom: 4px solid var(--n64-red);
            box-shadow: 0 10px 40px rgba(0,0,0,0.9);
        }

        h1 {
            font-family: 'Press Start 2P', cursive;
            font-size: 2rem;
            margin: 0;
            color: var(--n64-yellow);
            text-shadow: 3px 3px var(--n64-red), 0 0 15px var(--neon-blue);
        }

        .container {
            max-width: 1400px;
            margin: 40px auto;
            padding: 20px;
        }

        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 40px;
        }

        .card {
            background: var(--glass);
            border: 2px solid #444;
            border-radius: 12px;
            overflow: hidden;
            transition: all 0.3s ease;
            cursor: pointer;
            position: relative;
        }

        .card:hover {
            transform: translateY(-12px);
            border-color: var(--neon-blue);
            box-shadow: 0 0 40px rgba(0, 242, 255, 0.3);
        }

        .card-img-wrap {
            position: relative;
            height: 400px;
            overflow: hidden;
        }

        .card img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            transition: 0.5s;
        }

        .card:hover img {
            transform: scale(1.1);
        }

        .version-badge {
            position: absolute;
            top: 15px; right: 15px;
            background: var(--n64-red);
            color: white;
            padding: 5px 12px;
            font-family: 'Press Start 2P', cursive;
            font-size: 0.6rem;
            border-radius: 5px;
            box-shadow: 2px 2px 0 #800;
        }

        .card-info {
            padding: 20px;
            text-align: center;
            border-top: 1px solid #333;
        }

        .card-info h3 {
            margin: 0;
            font-family: 'Orbitron', sans-serif;
            font-size: 1rem;
            color: #fff;
        }

        /* Modal Upgraded */
        #modal {
            display: none;
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.95);
            z-index: 2000;
            backdrop-filter: blur(25px);
            align-items: center;
            justify-content: center;
        }

        .modal-content {
            width: 90%;
            max-width: 1000px;
            display: flex;
            background: #0a0a0a;
            border: 5px solid var(--n64-red);
            border-radius: 20px;
            position: relative;
            box-shadow: 0 0 100px rgba(255,0,0,0.2);
        }

        .modal-img {
            width: 45%;
            background-size: cover;
            background-position: center;
            border-right: 2px solid #222;
        }

        .modal-text {
            width: 55%;
            padding: 50px;
            max-height: 80vh;
            overflow-y: auto;
        }

        .modal-text h2 {
            font-family: 'Orbitron', sans-serif;
            font-size: 2.2rem;
            color: var(--n64-yellow);
            margin-top: 0;
        }

        .file-list {
            margin-top: 30px;
            display: flex;
            flex-direction: column;
            gap: 15px;
        }

        .file-item {
            background: #1a1a1a;
            border: 1px solid #444;
            padding: 15px;
            border-radius: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            transition: 0.2s;
        }

        .file-item:hover {
            border-color: var(--neon-blue);
            background: #222;
        }

        .btn-play-small {
            background: var(--n64-red);
            color: white;
            border: none;
            padding: 8px 20px;
            font-family: 'Press Start 2P', cursive;
            font-size: 0.7rem;
            cursor: pointer;
            border-radius: 4px;
            box-shadow: 2px 2px 0 #800;
        }

        .btn-play-small:hover {
            background: #ff4d4d;
            transform: scale(1.05);
        }

        .close-btn {
            position: absolute;
            top: 20px; right: 20px;
            color: white;
            font-size: 2rem;
            cursor: pointer;
            z-index: 10;
        }

        .loading {
            text-align: center;
            font-family: 'Press Start 2P', cursive;
            color: var(--neon-blue);
            margin-top: 150px;
            animation: pulse 1s infinite alternate;
        }

        @keyframes pulse { from { opacity: 0.5; } to { opacity: 1; } }
    </style>
</head>
<body>
    <div class="crt-overlay"></div>
    <header>
        <h1>STABLE N64 LIBRARY</h1>
    </header>

    <div class="container">
        <div id="loader" class="loading">CALIBRATING CARTRIDGES...</div>
        <div id="grid" class="grid"></div>
    </div>

    <div id="modal">
        <span class="close-btn" onclick="closeModal()">&times;</span>
        <div class="modal-content">
            <div class="modal-img" id="m-img"></div>
            <div class="modal-text">
                <h2 id="m-title">Game Title</h2>
                <div style="color: #888; margin-bottom: 20px" id="m-meta">Loading meta...</div>
                <div style="font-family: 'Orbitron'; color: var(--neon-blue); margin-top:30px">AVAILABLE VERSIONS:</div>
                <div class="file-list" id="m-files"></div>
            </div>
        </div>
    </div>

    <script>
        let currentGames = [];

        async function loadLibrary() {
            const res = await fetch('/api/roms');
            const data = await res.json();
            currentGames = data;
            renderGrid(data);
            document.getElementById('loader').style.display = 'none';
        }

        function renderGrid(games) {
            const grid = document.getElementById('grid');
            grid.innerHTML = '';
            games.forEach((game, index) => {
                const img = game.background_image || 'https://via.placeholder.com/400x600/111/444?text=NO+COVER';
                const vCount = game.files.length;
                grid.innerHTML += `
                    <div class="card" onclick="openModal(${index})">
                        <div class="card-img-wrap">
                            ${vCount > 1 ? `<div class="version-badge">${vCount} ROMS</div>` : ''}
                            <img src="${img}" alt="${game.name}">
                        </div>
                        <div class="card-info">
                            <h3>${game.name}</h3>
                        </div>
                    </div>
                `;
            });
        }

        function openModal(index) {
            const game = currentGames[index];
            document.getElementById('m-title').innerText = game.name;
            document.getElementById('m-meta').innerText = `Released: ${game.released} | RAWG Rating: ★ ${game.rating}`;
            document.getElementById('m-img').style.backgroundImage = `url(${game.background_image || ''})`;
            
            const fileList = document.getElementById('m-files');
            fileList.innerHTML = '';
            game.files.forEach(filename => {
                fileList.innerHTML += `
                    <div class="file-item">
                        <div style="font-size: 0.9rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 300px">
                            ${filename}
                        </div>
                        <button class="btn-play-small" onclick="playGame('${index}', '${filename}', this)">PLAY</button>
                    </div>
                `;
            });
            document.getElementById('modal').style.display = 'flex';
        }

        function closeModal() {
            document.getElementById('modal').style.display = 'none';
        }

        async function playGame(index, filename, btn) {
            const originalText = btn.innerText;
            btn.innerText = 'WAIT...';
            btn.disabled = true;
            try {
                await fetch(`/api/play?file=${encodeURIComponent(filename)}`);
            } finally {
                setTimeout(() => {
                    btn.innerText = originalText;
                    btn.disabled = false;
                }, 2000);
            }
        }

        loadLibrary();
    </script>
</body>
</html>
"""

class RequestHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urllib.parse.urlparse(self.path)
        if parsed_path.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML_CONTENT.encode())
        elif parsed_path.path == '/bg.png':
            try:
                with open('bg.png', 'rb') as f:
                    self.send_response(200)
                    self.send_header('Content-type', 'image/png')
                    self.end_headers()
                    self.wfile.write(f.read())
            except: self.send_error(404)
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
    time.sleep(1)
    webbrowser.open(f"http://localhost:{PORT}")
    print(f"Biblioteca Ativa em http://localhost:{PORT}")
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt: pass
