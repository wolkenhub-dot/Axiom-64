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
        # Remove extensões e limpa nomes de dump (E), (U), [!]
        name = filename.replace(".n64z", "").replace(".z64", "").replace(".n64", "").replace(".v64", "")
        # Remove tags como (USA), (Japan), [!]
        name = re.sub(r'\(.*?\)|\[.*?\]', '', name).strip()
        return name

    def fetch_game_info(self, game_name):
        if game_name in self.cache:
            return self.cache[game_name]

        query = urllib.parse.quote(game_name)
        url = f"https://api.rawg.io/api/games?key={API_KEY}&search={query}&platforms=7" # 7 = N64
        
        try:
            with urllib.request.urlopen(url) as response:
                data = json.loads(response.read().decode())
                if data['results']:
                    res = data['results'][0]
                    info = {
                        "name": res.get("name", game_name),
                        "released": res.get("released", "Desconhecido"),
                        "rating": res.get("rating", 0),
                        "background_image": res.get("background_image"),
                        "slug": res.get("slug")
                    }
                    self.cache[game_name] = info
                    self.save_cache()
                    return info
        except Exception as e:
            print(f"Erro ao buscar RAWG para {game_name}: {e}")
        
        return {"name": game_name, "released": "N/A", "rating": 0, "background_image": None}

    def scan_roms(self):
        if not os.path.exists(ROM_DIR):
            os.makedirs(ROM_DIR)
        
        rom_files = [f for f in os.listdir(ROM_DIR) if f.lower().endswith(".n64z")]
        library = []
        
        for f in rom_files:
            clean = self.clean_name(f)
            info = self.fetch_game_info(clean)
            info['filename'] = f
            library.append(info)
            
        return library

# --- SERVER LOGIC ---

PORT = 6464
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Axiom-64 Library</title>
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&family=Press+Start+2P&family=Outfit:wght@300;600&display=swap" rel="stylesheet">
    <style>
        :root {
            --neon-blue: #00f2ff;
            --n64-red: #ff0000;
            --n64-yellow: #ffcc00;
            --glass: rgba(0, 0, 0, 0.7);
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
            background: linear-gradient(rgba(18, 16, 16, 0) 50%, rgba(0, 0, 0, 0.25) 50%), 
                        linear-gradient(90deg, rgba(255, 0, 0, 0.06), rgba(0, 255, 0, 0.02), rgba(0, 0, 255, 0.06));
            background-size: 100% 4px, 3px 100%;
            pointer-events: none;
            z-index: 1000;
        }

        header {
            padding: 40px;
            text-align: center;
            background: rgba(0,0,0,0.5);
            backdrop-filter: blur(10px);
            border-bottom: 3px solid var(--n64-red);
            box-shadow: 0 10px 30px rgba(0,0,0,0.8);
        }

        h1 {
            font-family: 'Press Start 2P', cursive;
            font-size: 2.5rem;
            margin: 0;
            color: var(--n64-yellow);
            text-shadow: 4px 4px var(--n64-red), 0 0 20px var(--neon-blue);
            letter-spacing: -2px;
        }

        .container {
            max-width: 1400px;
            margin: 50px auto;
            padding: 20px;
        }

        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 30px;
        }

        .card {
            background: var(--glass);
            border: 2px solid #333;
            border-radius: 15px;
            overflow: hidden;
            transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
            cursor: pointer;
            position: relative;
            backdrop-filter: blur(5px);
        }

        .card:hover {
            transform: scale(1.05) translateY(-10px);
            border-color: var(--neon-blue);
            box-shadow: 0 0 30px rgba(0, 242, 255, 0.4);
            z-index: 10;
        }

        .card img {
            width: 100%;
            height: 400px;
            object-fit: cover;
            filter: grayscale(20%);
            transition: 0.3s;
        }

        .card:hover img {
            filter: grayscale(0%);
        }

        .card-info {
            padding: 20px;
            text-align: center;
        }

        .card-info h3 {
            margin: 0;
            font-family: 'Orbitron', sans-serif;
            font-size: 1.1rem;
            color: var(--n64-yellow);
        }

        .meta {
            font-size: 0.8rem;
            color: #888;
            margin-top: 10px;
        }

        /* Modal */
        #modal {
            display: none;
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.9);
            z-index: 2000;
            backdrop-filter: blur(20px);
            align-items: center;
            justify-content: center;
        }

        .modal-content {
            width: 80%;
            max-width: 900px;
            display: flex;
            background: #111;
            border: 4px solid var(--n64-red);
            border-radius: 20px;
            overflow: hidden;
            box-shadow: 0 0 50px rgba(255,0,0,0.3);
        }

        .modal-img {
            width: 50%;
            height: 600px;
            background-size: cover;
            background-position: center;
        }

        .modal-text {
            width: 50%;
            padding: 40px;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }

        .modal-text h2 {
            font-family: 'Orbitron', sans-serif;
            font-size: 2rem;
            color: var(--n64-yellow);
            margin-top: 0;
        }

        .play-btn {
            background: var(--n64-red);
            color: white;
            border: none;
            padding: 20px 40px;
            font-family: 'Press Start 2P', cursive;
            font-size: 1.2rem;
            cursor: pointer;
            margin-top: 30px;
            transition: 0.3s;
            clip-path: polygon(0 0, 100% 0, 95% 100%, 5% 100%);
            box-shadow: 0 10px 0 #800;
        }

        .play-btn:hover {
            transform: scale(1.1);
            background: #ff4d4d;
        }

        .play-btn:active {
            transform: translateY(5px);
            box-shadow: 0 5px 0 #800;
        }

        .loading {
            text-align: center;
            font-family: 'Press Start 2P', cursive;
            color: #fff;
            margin-top: 100px;
        }
    </style>
</head>
<body>
    <div class="crt-overlay"></div>
    <header>
        <h1>ULTIMATE N64 LIBRARY</h1>
    </header>

    <div class="container">
        <div id="loader" class="loading">SCANNING CARTRIDGES...</div>
        <div id="grid" class="grid"></div>
    </div>

    <!-- Details Modal -->
    <div id="modal" onclick="closeModal(event)">
        <div class="modal-content" onclick="event.stopPropagation()">
            <div class="modal-img" id="m-img"></div>
            <div class="modal-text">
                <h2 id="m-title">Game Title</h2>
                <div class="meta" id="m-meta">Date: 1996 | Rating: 4.5/5</div>
                <button class="play-btn" id="m-btn">PLAY GAME</button>
                <div class="meta" style="margin-top:40px; color: var(--neon-blue)">GOD TIER COMPRESSION: ON</div>
            </div>
        </div>
    </div>

    <script>
        let currentRoms = [];

        async function loadLibrary() {
            const res = await fetch('/api/roms');
            const data = await res.json();
            currentRoms = data;
            renderGrid(data);
            document.getElementById('loader').style.display = 'none';
        }

        function renderGrid(roms) {
            const grid = document.getElementById('grid');
            grid.innerHTML = '';
            roms.forEach((rom, index) => {
                const img = rom.background_image || 'https://via.placeholder.com/400x600/111/444?text=N64+CART';
                grid.innerHTML += `
                    <div class="card" onclick="openModal(${index})">
                        <img src="${img}" alt="${rom.name}">
                        <div class="card-info">
                            <h3>${rom.name}</h3>
                            <div class="meta">${rom.released} | ★ ${rom.rating}</div>
                        </div>
                    </div>
                `;
            });
        }

        function openModal(index) {
            const rom = currentRoms[index];
            document.getElementById('m-title').innerText = rom.name;
            document.getElementById('m-meta').innerText = `Release: ${rom.released} | Rating: ★ ${rom.rating}`;
            document.getElementById('m-img').style.backgroundImage = `url(${rom.background_image || ''})`;
            document.getElementById('m-btn').onclick = () => playGame(rom.filename);
            document.getElementById('modal').style.display = 'flex';
        }

        function closeModal(e) {
            document.getElementById('modal').style.display = 'none';
        }

        async function playGame(filename) {
            document.getElementById('m-btn').innerText = 'LOADING...';
            document.getElementById('m-btn').disabled = true;
            try {
                await fetch(`/api/play?file=${encodeURIComponent(filename)}`);
            } finally {
                document.getElementById('m-btn').innerText = 'PLAY GAME';
                document.getElementById('m-btn').disabled = false;
                closeModal();
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
            except:
                self.send_error(404)

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
                # Chama o launcher de forma assíncrona
                subprocess.Popen([sys.executable, "n64urc_launcher.py", path])
                self.send_response(200)
                self.end_headers()
            else:
                self.send_error(400)
        else:
            self.send_error(404)

def run_server():
    with socketserver.TCPServer(("", PORT), RequestHandler) as httpd:
        print(f"O Servidor da Biblioteca está rodando em http://localhost:{PORT}")
        httpd.serve_forever()

if __name__ == "__main__":
    backend = LibraryBackend()
    
    # Inicia o servidor em uma thread separada
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    
    # Abre o navegador automaticamente
    time.sleep(1)
    webbrowser.open(f"http://localhost:{PORT}")
    
    print("Mantenha este terminal aberto para manter a biblioteca ativa.")
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        print("Encerrando Biblioteca.")
