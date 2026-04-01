import os
import sys
import glob
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import winsound

# Importa o módulo original do compressor
try:
    import n64urc
except ImportError:
    messagebox.showerror("Erro Crítico", "O arquivo n64urc.py não foi encontrado no mesmo diretório!")
    sys.exit(1)

# Estética 90s CRT e Paleta Nintendo
BG_COLOR = "#1A1A1A"        # Cinza muito escuro / Preto CRT
FG_COLOR = "#F5F5F5"        # Branco gelo
CRT_GREEN = "#00FF41"       # Verde Matrix/Terminal
BTN_RED = "#E60012"         # Vermelho Nintendo
BTN_TEXT_YELLOW = "#FFD700" # Texto Amarelo
BTN_BLUE = "#0038A8"        # Azul N64
FONT_TITLE = ("Courier New", 20, "bold")
FONT_MAIN = ("Consolas", 10, "bold")
FONT_CRT = ("Consolas", 9)

class StdoutRedirector:
    """Redireciona sys.stdout para a Fila (Queue) que a GUI consome."""
    def __init__(self, msg_queue):
        self.msg_queue = msg_queue

    def write(self, string):
        self.msg_queue.put(('text', string))

    def flush(self):
        pass

class N64URCGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("N64-URC God Tier Compressor")
        self.root.geometry("600x570")
        self.root.resizable(False, False)
        self.root.configure(bg=BG_COLOR)

        self.msg_queue = queue.Queue()
        self.is_processing = False
        self.old_stdout = sys.stdout

        self.build_ui()
        self.poll_queue()

    def build_ui(self):
        # 1. HEADER
        header_frame = tk.Frame(self.root, bg=BG_COLOR)
        header_frame.pack(fill=tk.X, pady=(15, 5))
        
        lbl_title = tk.Label(
            header_frame, 
            text="N64-URC : GOD TIER COMPRESSOR", 
            fg=BTN_TEXT_YELLOW, 
            bg=BG_COLOR, 
            font=FONT_TITLE
        )
        lbl_title.pack()

        # Separator Line
        tk.Frame(self.root, bg="#333333", height=2).pack(fill=tk.X, padx=10, pady=5)

        # 2. CONFIGURATIONS FRAME
        config_frame = tk.Frame(self.root, bg=BG_COLOR)
        config_frame.pack(fill=tk.X, padx=15, pady=5)

        # Variables
        self.var_mode = tk.StringVar(value="compress")
        self.var_target = tk.StringVar(value="single")
        self.var_path = tk.StringVar(value="")

        # Mode Selection
        mode_lf = tk.LabelFrame(config_frame, text=" [ MODE SELECTION ] ", fg=FG_COLOR, bg=BG_COLOR, font=FONT_MAIN, relief=tk.SOLID, bd=2)
        mode_lf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        tk.Radiobutton(mode_lf, text="Compress (Z64 -> N64Z)", variable=self.var_mode, value="compress", 
                       fg=FG_COLOR, bg=BG_COLOR, selectcolor="#333", font=FONT_MAIN, activebackground=BG_COLOR, activeforeground=FG_COLOR).pack(anchor=tk.W, padx=10, pady=5)
        tk.Radiobutton(mode_lf, text="Extract (N64Z -> Z64)", variable=self.var_mode, value="extract", 
                       fg=FG_COLOR, bg=BG_COLOR, selectcolor="#333", font=FONT_MAIN, activebackground=BG_COLOR, activeforeground=FG_COLOR).pack(anchor=tk.W, padx=10, pady=5)

        # Target Selection
        target_lf = tk.LabelFrame(config_frame, text=" [ TARGET TARGET ] ", fg=FG_COLOR, bg=BG_COLOR, font=FONT_MAIN, relief=tk.SOLID, bd=2)
        target_lf.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))

        tk.Radiobutton(target_lf, text="Single File", variable=self.var_target, value="single", 
                       fg=FG_COLOR, bg=BG_COLOR, selectcolor="#333", font=FONT_MAIN, command=self.on_target_change, activebackground=BG_COLOR, activeforeground=FG_COLOR).pack(anchor=tk.W, padx=10, pady=5)
        tk.Radiobutton(target_lf, text="Fullset Batch", variable=self.var_target, value="batch", 
                       fg=FG_COLOR, bg=BG_COLOR, selectcolor="#333", font=FONT_MAIN, command=self.on_target_change, activebackground=BG_COLOR, activeforeground=FG_COLOR).pack(anchor=tk.W, padx=10, pady=5)

        # 3. PATH INPUTS
        path_frame = tk.Frame(self.root, bg=BG_COLOR)
        path_frame.pack(fill=tk.X, padx=15, pady=10)

        self.entry_path = tk.Entry(path_frame, textvariable=self.var_path, state='readonly', font=FONT_MAIN, bg="#333", fg=FG_COLOR, relief=tk.SOLID, bd=2)
        self.entry_path.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)

        self.btn_browse = tk.Button(path_frame, text="BROWSE", bg=BTN_BLUE, fg=FG_COLOR, font=FONT_MAIN, relief=tk.SOLID, bd=2, command=self.browse_path)
        self.btn_browse.pack(side=tk.RIGHT, padx=(10, 0), ipadx=10, ipady=2)

        # 4. MAIN BUTTON
        self.btn_start = tk.Button(self.root, text=">> START PROCESSING <<", bg=BTN_RED, fg=BTN_TEXT_YELLOW, font=("Fixedsys", 16, "bold"), relief=tk.SOLID, bd=4, command=self.start_processing)
        self.btn_start.pack(fill=tk.X, padx=15, pady=10, ipady=10)

        # 5. CRT MONITOR (Terminal)
        crt_frame = tk.Frame(self.root, bg="black", relief=tk.SOLID, bd=3)
        crt_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0, 10))

        self.txt_crt = scrolledtext.ScrolledText(crt_frame, bg="black", fg=CRT_GREEN, font=FONT_CRT, state=tk.DISABLED, insertbackground=CRT_GREEN)
        self.txt_crt.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        # 6. PROGRESS BAR
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TProgressbar", thickness=15, background="#008F11", troughcolor="#333333", bordercolor=BG_COLOR)
        
        self.progress = ttk.Progressbar(self.root, style="TProgressbar", orient="horizontal", mode="indeterminate")
        self.progress.pack(fill=tk.X, padx=15, pady=(0, 15))

    def on_target_change(self):
        self.var_path.set("") # Limpa o caminho quando muda o modo

    def browse_path(self):
        target = self.var_target.get()
        if target == "single":
            path = filedialog.askopenfilename(title="Select File", filetypes=[("N64 ROMs", "*.z64 *.n64 *.v64 *.rom *.n64z"), ("All Files", "*.*")])
        else:
            path = filedialog.askdirectory(title="Select Folder")
        
        if path:
            self.var_path.set(os.path.normpath(path))

    def write_crt(self, text):
        self.txt_crt.config(state=tk.NORMAL)
        self.txt_crt.insert(tk.END, text)
        self.txt_crt.see(tk.END)
        self.txt_crt.config(state=tk.DISABLED)

    def log_message(self, text):
        self.msg_queue.put(('text', text + "\n"))

    def toggle_ui(self, state):
        mode_state = tk.NORMAL if state else tk.DISABLED
        self.btn_browse.config(state=mode_state)
        self.btn_start.config(state=mode_state)
        
        # O ttk radiobutton nao suporta forçar state tão dinâmico diretamente na variavel entao usamos loop
        for child in self.root.winfo_children():
            if isinstance(child, tk.Frame):
                for sub in child.winfo_children():
                    if isinstance(sub, tk.LabelFrame):
                        for rad in sub.winfo_children():
                            if isinstance(rad, tk.Radiobutton):
                                rad.config(state=mode_state)

    def start_processing(self):
        path = self.var_path.get()
        if not path:
            messagebox.showwarning("Aviso", "Por favor, selecione um arquivo ou pasta primeiro!")
            return

        mode = self.var_mode.get()
        target = self.var_target.get()

        if target == "single" and not os.path.isfile(path):
            messagebox.showerror("Erro", "O caminho selecionado não é um arquivo válido.")
            return
        
        if target == "batch" and not os.path.isdir(path):
            messagebox.showerror("Erro", "O caminho selecionado não é um diretório válido.")
            return

        # Preparação Visual
        self.toggle_ui(False)
        self.txt_crt.config(state=tk.NORMAL)
        self.txt_crt.delete(1.0, tk.END)
        self.txt_crt.config(state=tk.DISABLED)
        
        self.log_message(">>> INICIANDO SISTEMA <<<")
        self.log_message(f"MODO: {mode.upper()}")
        self.log_message(f"TARGET: {target.upper()}\n")

        # Redireciona o stdout
        sys.stdout = StdoutRedirector(self.msg_queue)
        
        self.is_processing = True

        if target == "single":
            self.progress.config(mode="indeterminate")
            self.progress.start(15)
        else:
            self.progress.config(mode="determinate", value=0)

        # Inicia a Thread Gêmea
        thread = threading.Thread(target=self.worker_thread, args=(mode, target, path), daemon=True)
        thread.start()

    def worker_thread(self, mode, target, path):
        try:
            if target == "single":
                self._process_single(mode, path)
            else:
                self._process_batch(mode, path)
        except Exception as e:
            self.msg_queue.put(('text', f"\n[ERRO CRÍTICO] {str(e)}\n"))
        finally:
            self.msg_queue.put(('done', None))

    def _process_single(self, mode, file_path):
        if mode == "compress":
            out_file = file_path + ".n64z"
            n64urc.N64ZContainer.compress_rom(file_path, out_file, quiet=False)
        else:
            out_file = file_path.replace(".n64z", "") + "_rec.z64"
            n64urc.N64ZContainer.extract_rom(file_path, out_file, quiet=False)

    def _process_batch(self, mode, directory):
        if mode == "compress":
            patterns = ['*.z64', '*.n64', '*.v64', '*.rom']
        else:
            patterns = ['*.n64z']

        files = []
        for pat in patterns:
            search_path = os.path.join(directory, pat)
            files.extend(glob.glob(search_path))

        if not files:
            self.log_message("[ERRO] Nenhum arquivo válido encontrado para processamento neste diretório.")
            return

        self.msg_queue.put(('progress_max', len(files)))

        for i, f in enumerate(files):
            self.log_message(f"\n--- Processando [{i+1}/{len(files)}]: {os.path.basename(f)} ---")
            
            if mode == "compress":
                out_file = f + ".n64z"
                n64urc.N64ZContainer.compress_rom(f, out_file, quiet=True) # Usa Quiet mode ativado
            else:
                out_file = f.replace(".n64z", "") + "_rec.z64"
                n64urc.N64ZContainer.extract_rom(f, out_file, quiet=True)

            self.msg_queue.put(('progress_step', 1))

    def poll_queue(self):
        """Consome mensagens da fila gerada pela Worker Thread de forma Thread-Safe"""
        try:
            while True:
                msg_type, data = self.msg_queue.get_nowait()
                
                if msg_type == 'text':
                    self.write_crt(data)
                
                elif msg_type == 'progress_max':
                    self.progress.config(maximum=data)
                
                elif msg_type == 'progress_step':
                    self.progress.step(data)
                
                elif msg_type == 'done':
                    self.finish_processing()
                    
                self.msg_queue.task_done()
        except queue.Empty:
            pass
        finally:
            # Roda novamente em 100ms
            self.root.after(100, self.poll_queue)

    def finish_processing(self):
        sys.stdout = self.old_stdout  # Restaura stdout
        self.is_processing = False
        self.progress.stop()
        
        target = self.var_target.get()
        if target == "single":
            self.progress.config(value=0) # Reseta o indeterminado
        else:
            self.progress.config(value=self.progress['maximum']) # Completa
            
        self.write_crt("\n>>> JOB FINISHED <<<\n")
        self.toggle_ui(True)

        try:
            winsound.MessageBeep(winsound.MB_OK)
        except:
            pass

if __name__ == "__main__":
    if sys.platform.startswith("win"):
        # Garante que o multiprocessamento continue funcionando caso embutido num exe
        import multiprocessing
        multiprocessing.freeze_support()

    root = tk.Tk()
    app = N64URCGUI(root)
    root.mainloop()
