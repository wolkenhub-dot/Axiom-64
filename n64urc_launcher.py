import os
import sys
import struct
import subprocess
import configparser
import tempfile
import time
import tkinter as tk
from tkinter import filedialog, messagebox

# Tenta importar o motor Axiom-64
try:
    import n64urc
except ImportError:
    # Se o launcher estiver em uma pasta diferente, este erro será pego
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("Erro de Dependência", "O arquivo n64urc.py não foi encontrado.\nCertifique-se de que o launcher está na mesma pasta do compressor.")
    sys.exit(1)

CONFIG_FILE = "launcher_config.ini"

def get_emulator_path():
    """Lê ou solicita o caminho do executável do emulador."""
    config = configparser.ConfigParser()
    
    if os.path.exists(CONFIG_FILE):
        config.read(CONFIG_FILE)
        if 'SETTINGS' in config and 'emulator_path' in config['SETTINGS']:
            emu_path = config['SETTINGS']['emulator_path']
            if os.path.exists(emu_path):
                return emu_path

    # Se não existir ou for inválido, pergunta ao usuário
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo("Configuração Inicial", "Por favor, selecione o executável do seu Emulador (ex: Project64.exe)")
    
    emu_path = filedialog.askopenfilename(
        title="Selecionar Emulador N64",
        filetypes=[("Executáveis", "*.exe"), ("Todos os Arquivos", "*.*")]
    )
    
    if not emu_path:
        sys.exit(0) # Usuário cancelou
        
    # Salva para a próxima vez
    config['SETTINGS'] = {'emulator_path': emu_path}
    with open(CONFIG_FILE, 'w') as configfile:
        config.write(configfile)
    
    root.destroy()
    return emu_path

def main():
    # 1. Verifica argumentos
    if len(sys.argv) < 2:
        # Se abrir sem argumentos, talvez o usuário queira apenas configurar
        get_emulator_path()
        sys.exit(0)

    rom_n64z = sys.argv[1]
    if not rom_n64z.lower().endswith(".n64z"):
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Erro de Formato", f"O arquivo selecionado não é um .n64z válido:\n{rom_n64z}")
        sys.exit(1)

    # 2. Obtém caminho do emulador
    emu_path = get_emulator_path()

    # 3. Define caminho temporário seguro
    temp_dir = tempfile.gettempdir()
    # Usamos timestamp para evitar collisions se o usuário abrir vários jogos
    temp_filename = f"axiom_tmp_{int(time.time())}.z64"
    temp_path = os.path.join(temp_dir, temp_filename)

    try:
        # 4. Descompressão God Tier (Modo Silencioso)
        # Note: extract_rom pode levantar exceções se o arquivo estiver corrompido
        n64urc.N64ZContainer.extract_rom(rom_n64z, temp_path, quiet=True)
        
        if not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
            raise Exception("Falha ao gerar arquivo temporário (espaço em disco insuficiente?)")

        # 5. Inicia o emulador bloqueando o script
        # No Windows, você pode usar CREATE_NO_WINDOW se quiser esconder o console, 
        # mas como este script deve ser compilado com --noconsole no PyInstaller,
        # o subprocess.run funcionará de forma limpa.
        subprocess.run([emu_path, temp_path], check=False)

    except Exception as e:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Erro na Execução", f"Falha ao processar ROM:\n{str(e)}")
    
    finally:
        # 6. Limpeza Obrigatória (Mesmo se houver crash)
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception as e:
                # Se o arquivo estiver preso (raro após subprocess.run), tentamos ignorar
                pass

if __name__ == "__main__":
    # DICA PARA COMPILAÇÃO:
    # pyinstaller --onefile --noconsole --icon=n64.ico n64urc_launcher.py
    main()
