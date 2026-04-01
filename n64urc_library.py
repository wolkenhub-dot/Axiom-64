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
                        # Cache locking would be good here but dict is thread-safe in CPython for simple ops
                        self.cache[game_name] = info
                        return info.copy()
        except: pass
        
        return {"name": game_name, "released": "N/A", "rating": 0, "background_image": None, "slug": game_name.lower().replace(" ", "-")}

    def scan_roms(self):
        if not os.path.exists(ROM_DIR):
            os.makedirs(ROM_DIR)
        
        rom_files = sorted([f for f in os.listdir(ROM_DIR) if f.lower().endswith(".n64z")])
        
        # Carregamento paralelo para máxima velocidade
        def process_one(f):
            clean = self.clean_name(f)
            metadata = self.fetch_game_info(clean)
            return (f, metadata)

        # ThreadPoolExecutor acelera em até 5x o carregamento inicial
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(process_one, rom_files))

        games_map = {}
        for f, metadata in results:
            slug = metadata['slug']
            if slug not in games_map:
                games_map[slug] = metadata
                games_map[slug]['files'] = []
            games_map[slug]['files'].append(f)
            
        self.save_cache() # Salva tudo de uma vez
        return sorted(list(games_map.values()), key=lambda x: x['name'])

# --- SERVER LOGIC ---

PORT = 6464
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sua Biblioteca N64 - Axiom-64</title>
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&family=Press+Start+2P&family=Outfit:wght@300;600&display=swap" rel="stylesheet">
    <style>
        :root {
            --neon-blue: #00f2ff;
            --n64-red: #ff0000;
            --n64-yellow: #ffcc00;
            --glass: rgba(0, 0, 0, 0.85);
        }

        body {
            margin: 0;
            background: #050505 url('bg.png') no-repeat center center fixed;
            background-size: cover;
            color: white;
            font-family: 'Outfit', sans-serif;
            overflow-x: hidden;
            -webkit-font-smoothing: antialiased;
        }

        /* Efeito CRT Leve e Veloz */
        .crt-overlay {
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: linear-gradient(rgba(18, 16, 16, 0) 50%, rgba(0, 0, 0, 0.1) 50%);
            background-size: 100% 4px;
            pointer-events: none;
            z-index: 1000;
        }

        header {
            padding: 25px;
            text-align: center;
            background: rgba(0,0,0,0.7);
            backdrop-filter: blur(8px);
            border-bottom: 2px solid var(--n64-red);
            box-shadow: 0 5px 20px rgba(0,0,0,0.8);
        }

        h1 {
            font-family: 'Press Start 2P', cursive;
            font-size: 1.5rem;
            margin: 0;
            color: var(--n64-yellow);
            text-shadow: 2px 2px var(--n64-red);
            letter-spacing: -1px;
        }

        .container {
            max-width: 1400px;
            margin: 30px auto;
            padding: 0 20px;
        }

        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
            gap: 25px;
        }

        .card {
            background: var(--glass);
            border: 1px solid #333;
            border-radius: 8px;
            overflow: hidden;
            transition: transform 0.2s, border-color 0.2s;
            cursor: pointer;
            position: relative;
        }

        .card:hover {
            transform: scale(1.03);
            border-color: var(--neon-blue);
        }

        .card-img-wrap {
            position: relative;
            height: 360px;
            background: #111;
        }

        .card img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            transition: 0.3s opacity;
        }

        .version-badge {
            position: absolute;
            top: 10px; right: 10px;
            background: var(--n64-red);
            color: white;
            padding: 4px 8px;
            font-family: 'Press Start 2P', cursive;
            font-size: 0.5rem;
            border-radius: 3px;
            box-shadow: 1px 1px 0 #800;
        }

        .card-info {
            padding: 15px;
            text-align: center;
        }

        .card-info h3 {
            margin: 0;
            font-family: 'Orbitron', sans-serif;
            font-size: 0.9rem;
            color: #fff;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        /* Modal Slim */
        #modal {
            display: none;
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.9);
            z-index: 2000;
            backdrop-filter: blur(10px);
            align-items: center;
            justify-content: center;
        }

        .modal-content {
            width: 90%;
            max-width: 900px;
            display: flex;
            background: #0d0d0d;
            border: 3px solid var(--n64-red);
            border-radius: 12px;
            position: relative;
            box-shadow: 0 0 40px rgba(255,0,0,0.3);
            overflow: hidden;
        }

        .modal-img {
            width: 40%;
            background-size: cover;
            background-position: center;
        }

        .modal-text {
            width: 60%;
            padding: 40px;
            max-height: 80vh;
            overflow-y: auto;
        }

        .modal-text h2 {
            font-family: 'Orbitron', sans-serif;
            font-size: 1.8rem;
            color: var(--n64-yellow);
            margin-top: 0;
        }

        .file-list {
            margin-top: 25px;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }

        .file-item {
            background: #181818;
            border: 1px solid #333;
            padding: 12px 15px;
            border-radius: 6px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .btn-play-small {
            background: var(--n64-red);
            color: white;
            border: none;
            padding: 8px 15px;
            font-family: 'Press Start 2P', cursive;
            font-size: 0.6rem;
            cursor: pointer;
            border-radius: 4px;
            box-shadow: 2px 2px 0 #800;
        }

        .btn-play-small:hover { background: #ff4d4d; }

        .close-btn {
            position: absolute;
            top: 15px; right: 20px;
            color: white;
            font-size: 2rem;
            cursor: pointer;
            z-index: 100;
        }

        .loading {
            text-align: center;
            font-family: 'Press Start 2P', cursive;
            color: var(--neon-blue);
            margin-top: 120px;
        }
    </style>
</head>
<body>
    <div class="crt-overlay"></div>
    <header>
        <h1>BIBLIOTECA AXIOM-64</h1>
    </header>

    <div class="container">
        <div id="loader" class="loading">CALIBRANDO CARTUCHOS...</div>
        <div id="grid" class="grid"></div>
    </div>

    <div id="modal">
        <span class="close-btn" onclick="closeModal()">&times;</span>
        <div class="modal-content">
            <div class="modal-img" id="m-img"></div>
            <div class="modal-text">
                <h2 id="m-title">Título do Jogo</h2>
                <div style="color: #aaa; font-size: 0.9rem" id="m-meta">Carregando...</div>
                <div style="font-family: 'Orbitron'; color: var(--neon-blue); margin-top:25px; font-size: 0.8rem">VERSÕES DISPONÍVEIS:</div>
                <div class="file-list" id="m-files"></div>
            </div>
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
                document.getElementById('loader').innerText = 'ERRO AO CARREGAR BIBLIOTECA';
            }
        }

        function renderGrid(games) {
            const grid = document.getElementById('grid');
            grid.innerHTML = '';
            games.forEach((game, index) => {
                const img = game.background_image || '';
                const vCount = game.files.length;
                grid.innerHTML += `
                    <div class="card" onclick="openModal(${index})">
                        <div class="card-img-wrap">
                            ${vCount > 1 ? `<div class="version-badge">${vCount} ROMS</div>` : ''}
                            ${img ? `<img src="${img}" alt="${game.name}">` : '<div style="padding:100px 0; text-align:center; color:#444">SEM CAPA</div>'}
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
            document.getElementById('m-meta').innerText = `Lançamento: ${game.released} | Avaliação: ★ ${game.rating}`;
            document.getElementById('m-img').style.backgroundImage = game.background_image ? `url(${game.background_image})` : 'none';
            
            const fileList = document.getElementById('m-files');
            fileList.innerHTML = '';
            game.files.forEach(filename => {
                fileList.innerHTML += `
                    <div class="file-item">
                        <div style="font-size: 0.8rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 260px">
                            ${filename}
                        </div>
                        <button class="btn-play-small" onclick="playGame('${index}', '${filename}', this)">JOGAR</button>
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
            btn.innerText = 'AGUARDE';
            btn.disabled = true;
            try {
                await fetch(`/api/play?file=${encodeURIComponent(filename)}`);
            } finally {
                setTimeout(() => {
                    btn.innerText = originalText;
                    btn.disabled = false;
                }, 1500);
            }
        }

        loadLibrary();

        // Atalho ESC para fechar modal
        window.addEventListener('keydown', (e) => {
            if(e.key === 'Escape') closeModal();
        });
    </script>
</body>
</html>
"""

class RequestHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args): return # Silencia logs para ser "leve" no terminal

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
    time.sleep(0.5)
    webbrowser.open(f"http://localhost:{PORT}")
    print(f"🚀 Biblioteca Ativa em http://localhost:{PORT}")
    print("Mantenha este terminal aberto.")
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt: pass
